"""Current construction facts, not a blueprint, action sequence or learned policy.

Frozen world goals supply *desired* blocks. Only ``evidence.observed`` supplies
actual blocks; an initial baseline is never silently reused as a fresh survey.
This helper is deliberately independent of the model, bridge and Minecraft.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

from agent.regions import RegionError, compile_goal, position_dict, position_key


_AIR = frozenset({"air", "cave_air", "void_air"})
_NATURAL = frozenset({"grass", "tall_grass", "grass_block", "dirt", "stone", "andesite", "diorite", "granite"})
_CATEGORY_ORDER = (
    "natural_obstructions", "missing_nonair", "unknown", "other_occupied_mismatches",
    "empty_block_mismatches", "matched_change_required", "matched_no_change_required",
)
_MAX_SAMPLE_LIMIT = 32
_MAX_LAYER_RANGES = 24


def _coordinate(raw):
    if not isinstance(raw, Mapping) or not all(type(raw.get(axis)) is int for axis in ("x", "y", "z")):
        raise RegionError("block_is position needs integer x, y, z")
    return raw["x"], raw["y"], raw["z"]


def _parse_key(raw):
    if not isinstance(raw, str):
        return None
    parts = raw.split(",")
    if len(parts) != 3:
        return None
    try:
        coordinate = tuple(int(part) for part in parts)
    except ValueError:
        return None
    # Reject fractional, whitespace and other non-canonical aliases rather
    # than allowing two spellings to assert different facts about one cell.
    return coordinate if position_key(coordinate) == raw else None


def _actual(raw):
    if isinstance(raw, Mapping):
        if raw.get("loaded") is False:
            return "unknown"
        raw = raw.get("name", raw.get("block"))
    return raw if isinstance(raw, str) and raw and raw != "unknown" else "unknown"


def _natural(block):
    # Descriptive parity with the ordinary natural-site clearing tool. A name
    # does not prove provenance, reachability, permissions or safe support.
    return block in _NATURAL or (block.endswith(("_log", "_leaves")) and not block.startswith("stripped_"))


def _targets(goals):
    targets = {}
    construction_goals = []
    for goal in goals:
        if not isinstance(goal, Mapping) or goal.get("kind") not in {"regions_match", "block_is"}:
            continue
        if goal["kind"] == "regions_match":
            cells = compile_goal(goal)
        else:
            block = goal.get("block")
            if not isinstance(block, str) or not block:
                raise RegionError("block_is needs a non-empty block identifier")
            cells = {_coordinate(goal.get("position")): block}
        change_required = goal.get("must_change", False)
        if type(change_required) is not bool:
            raise RegionError("must_change must be Boolean")
        construction_goals.append(goal)
        for coordinate, desired in cells.items():
            existing = targets.get(coordinate)
            if existing is not None and existing["desired"] != desired:
                raise RegionError(f"Conflicting construction targets at {position_dict(coordinate)}")
            targets[coordinate] = {"desired": desired, "change_required": change_required or
                                   bool(existing and existing["change_required"])}
    return dict(sorted(targets.items())), construction_goals


def _observations(goals, evidence):
    observations = {}
    conflicts = set()
    for entry in evidence:
        if not isinstance(entry, Mapping) or entry.get("goal") not in goals:
            continue
        goal = entry["goal"]
        if goal["kind"] == "regions_match":
            raw_map = entry.get("observed")
            items = raw_map.items() if isinstance(raw_map, Mapping) else ()
        else:
            items = ((position_key(_coordinate(goal["position"])), entry.get("observed")),)
        for raw_key, raw_block in items:
            coordinate = _parse_key(raw_key)
            if coordinate is None:
                continue
            actual = _actual(raw_block)
            if coordinate in observations and observations[coordinate] != actual:
                conflicts.add(coordinate)
            observations[coordinate] = actual
    for coordinate in conflicts:
        observations[coordinate] = "unknown"
    return observations, conflicts


def _bounds(coordinates):
    if not coordinates:
        return None
    return {
        "min": position_dict(tuple(min(value[axis] for value in coordinates) for axis in range(3))),
        "max": position_dict(tuple(max(value[axis] for value in coordinates) for axis in range(3))),
    }


def _spread(values, count):
    if count >= len(values):
        return values
    if count == 1:
        return [values[len(values) // 2]]
    return [values[index * (len(values) - 1) // (count - 1)] for index in range(count)]


def _diverse_samples(values, count):
    """Spread across terrain families, then heights/names and spatial ranges.

    Dirt and grass are different names in the same terrain family. Giving
    each of them the first sample slot would still hide all leaf positions
    under a small budget. Families are sampling strata, not action priorities.
    """
    if not count:
        return []
    groups = defaultdict(list)
    for value in values:
        observed = value["observed"]
        if observed in {"grass", "tall_grass", "grass_block", "dirt"}:
            family = (0, "soil_and_plants")
        elif observed.endswith("_leaves"):
            family = (1, "leaves")
        elif observed.endswith("_log"):
            family = (2, "logs")
        elif observed in {"stone", "andesite", "diorite", "granite"}:
            family = (3, "stone")
        else:
            family = (4, observed)
        groups[family].append(value)
    names = sorted(groups)
    allocation = {name: 0 for name in names}
    remaining = count
    while remaining:
        for name in names:
            if allocation[name] < len(groups[name]) and remaining:
                allocation[name] += 1
                remaining -= 1
    samples = []
    for name in names:
        strata = defaultdict(list)
        for value in groups[name]:
            strata[(value["position"]["y"], value["observed"], value["desired"])].append(value)
        keys = sorted(strata)
        budget = allocation[name]
        if not budget:
            continue
        # At low budgets include distant strata rather than only the first
        # rows (e.g. the lowest layer and roof, instead of the first x row).
        if budget < len(keys):
            samples.extend(_spread(strata[key], 1)[0] for key in _spread(keys, budget))
            continue
        counts = {key: 0 for key in keys}
        remaining = budget
        while remaining:
            for key in keys:
                if counts[key] < len(strata[key]) and remaining:
                    counts[key] += 1
                    remaining -= 1
        samples.extend(sample for key in keys for sample in _spread(strata[key], counts[key]))
    return samples


def _layer_summary(layers):
    """Exact per-height facts, run-compressed and explicitly bounded."""
    ranges = []
    for y, raw in sorted(layers.items()):
        facts = {**{key: raw[key] for key in ("cell_count", "matched", "unknown", "known_mismatches")},
                 "desired_blocks": dict(sorted(raw["desired_blocks"].items())),
                 "observed_blocks": dict(sorted(raw["observed_blocks"].items()))}
        if ranges and ranges[-1]["max_y"] + 1 == y and ranges[-1]["per_layer"] == facts:
            ranges[-1]["max_y"] = y
            ranges[-1]["layer_count"] += 1
        else:
            ranges.append({"min_y": y, "max_y": y, "layer_count": 1, "per_layer": facts})
    emitted = _spread(ranges, min(len(ranges), _MAX_LAYER_RANGES))
    return {
        "format": "Inclusive Y ranges; per_layer counts apply separately to every Y in that range.",
        "distinct_layers": len(layers), "total_ranges": len(ranges), "ranges": emitted,
        "complete": len(emitted) == len(ranges),
        "omitted_range_count": len(ranges) - len(emitted),
        "omitted_layer_count": len(layers) - sum(row["layer_count"] for row in emitted),
        "omission_semantics": "All target/category/material totals include omitted layers. Omitted layer details are not evidence of air or an action order.",
    }


def construction_knowledge(goals, evidence, state, max_samples=8):
    """Return bounded facts for frozen region/block targets and fresh evidence.

    ``evidence`` has the controller's normal entries: ``goal`` plus
    ``observed`` (a full ``"x,y,z" -> block-name`` map for regions, a name for
    block_is). Map values may additionally be ``{"name": ..., "loaded": ...}``.
    ``state.inventory`` supplies current stacks. Other goal kinds are ignored.
    Samples have a GLOBAL budget, not eight cells per category. Every target
    contributes to exact counts even when omitted from coordinate samples.

    No calls, actions, baseline completion claims or input mutations occur.
    Invalid/conflicting desired contracts raise RegionError. Contradictory
    observations are unknown; missing/unloaded evidence is never air.
    """
    if not isinstance(goals, Sequence) or isinstance(goals, (str, bytes)):
        raise ValueError("goals must be a sequence")
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        raise ValueError("evidence must be a sequence")
    if type(max_samples) is not int or not 0 <= max_samples <= _MAX_SAMPLE_LIMIT:
        raise ValueError(f"max_samples must be an integer from 0 to {_MAX_SAMPLE_LIMIT}")
    targets, relevant_goals = _targets(goals)
    observed, conflicts = _observations(relevant_goals, evidence)
    categories = {name: [] for name in _CATEGORY_ORDER}
    materials = {}
    layers = defaultdict(lambda: {"cell_count": 0, "matched": 0, "unknown": 0, "known_mismatches": 0,
                                  "desired_blocks": Counter(), "observed_blocks": Counter()})
    inventory = Counter()
    stacks = state.get("inventory", []) if isinstance(state, Mapping) else []
    if isinstance(stacks, Sequence) and not isinstance(stacks, (str, bytes)):
        for stack in stacks:
            if (isinstance(stack, Mapping) and isinstance(stack.get("name"), str)
                    and type(stack.get("count")) is int and stack["count"] >= 0):
                inventory[stack["name"]] += stack["count"]

    for coordinate, target in targets.items():
        desired, change_required = target["desired"], target["change_required"]
        actual = observed.get(coordinate, "unknown")
        below = (coordinate[0], coordinate[1] - 1, coordinate[2])
        fact = {"position": position_dict(coordinate), "desired": desired, "observed": actual,
                "change_required": change_required,
                "below": {"position": position_dict(below), "observed": observed.get(below, "unknown")}}
        if below in targets:
            fact["below"]["desired"] = targets[below]["desired"]
        if actual == "unknown":
            fact["unknown_reason"] = "conflicting_observations" if coordinate in conflicts else "missing_or_unloaded"
            category = "unknown"
        elif actual == desired:
            category = "matched_change_required" if change_required else "matched_no_change_required"
        elif actual in _AIR:
            category = "missing_nonair" if desired not in _AIR else "empty_block_mismatches"
        else:
            category = "natural_obstructions" if _natural(actual) else "other_occupied_mismatches"
        categories[category].append(fact)
        layer = layers[coordinate[1]]
        layer["cell_count"] += 1
        layer["desired_blocks"][desired] += 1
        layer["observed_blocks"][actual] += 1
        layer["unknown" if actual == "unknown" else "matched" if actual == desired else "known_mismatches"] += 1
        if desired not in _AIR:
            material = materials.setdefault(desired, {
                "held_same_name_items": inventory[desired], "target_cells": 0,
                "already_matching_cells": 0, "known_remaining_cells": 0,
                "unknown_target_cells": 0, "empty_targets": 0, "occupied_targets": 0,
            })
            material["target_cells"] += 1
            if actual == desired:
                material["already_matching_cells"] += 1
            elif actual == "unknown":
                material["unknown_target_cells"] += 1
            else:
                material["known_remaining_cells"] += 1
                material["empty_targets" if actual in _AIR else "occupied_targets"] += 1

    allocation = {name: 0 for name in categories}
    remaining = min(max_samples, len(targets))
    while remaining:
        for name in _CATEGORY_ORDER:
            if allocation[name] < len(categories[name]) and remaining:
                allocation[name] += 1
                remaining -= 1
    summaries = {}
    for name, facts in categories.items():
        summaries[name] = {
            "count": len(facts),
            "observed_blocks": dict(sorted(Counter(fact["observed"] for fact in facts).items())),
            "desired_blocks": dict(sorted(Counter(fact["desired"] for fact in facts).items())),
            "samples": _diverse_samples(facts, allocation[name]),
            "omitted_sample_count": len(facts) - allocation[name],
        }
    for material in materials.values():
        known = material["known_remaining_cells"]
        possible = known + material["unknown_target_cells"]
        held = material["held_same_name_items"]
        material.update(remaining_cells_upper_bound=possible,
                        minimum_additional_same_name_items=max(0, known - held),
                        maximum_additional_if_unknowns_need_placement=max(0, possible - held))

    return {
        "applicable": bool(targets),
        "source": "frozen desired goals + current evidence.observed + current inventory; baselines not used",
        "target_cell_count": len(targets), "target_bounds": _bounds(targets),
        "categories": summaries, "materials": dict(sorted(materials.items())), "layers": _layer_summary(layers),
        "sample_budget": max_samples,
        "sample_policy": "Global round-robin category budget; terrain-family, height/name and spatial diversity, not an action order. Counts include unsampled cells.",
        "coordinate_knowledge": {
            "axes": "x, y (height), z are absolute world block coordinates, not row indices or relative offsets.",
            "signed": "Keep every coordinate's sign: z=-13 and z=13 are different cells; increasing negative z moves toward zero.",
            "inclusive_bounds": "Both min and max are included; a dimension is max-min+1. The bounding box is not permission to alter undeclared cells.",
            "target_vs_support": "A floor block at (x,y,z) occupies that exact target cell; the cell below is (x,y-1,z), not the same target. Desired blocks are not observed supports.",
        },
        "mechanics": {
            "placement": "A desired non-air block in observed air needs placement, not digging. Normal placement consumes one held item for a registry-confirmed same-name placeable block and needs a loaded valid adjacent support; below is only one possible support.",
            "occupied": "A differing observed non-air block occupies the target. Natural-looking names describe observations, not permission, provenance or a safe clearing guarantee.",
            "air": "Required air means an empty final cell. Never dig an observed air/cave_air/void_air cell; exact-name air mismatches remain separate from occupied cells.",
            "quantity": "Already-matching blocks require no additional material. Counts concern remaining final cells, not final held inventory. Unknown cells make remaining quantities upper bounds. Block names are not automatically inventory items; registry mechanics must confirm the pair.",
            "verification": "Current matches are geometry facts, not proof of must_change or overall success. Only the original goal verifier can confirm completion; missing evidence and conflicting observations remain unknown.",
        },
    }
