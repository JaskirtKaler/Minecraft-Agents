"""Observed tool mutations, not action selection or overall goal verification.

These narrow predicates recognize the existing Node proof contracts and the
explicit construction-simulator dig contract. A successful invocation alone,
an unknown block, or a receipt at another coordinate never proves an effect.
"""
from collections.abc import Mapping
from math import isfinite
import re


_IDENTIFIER = re.compile(r"(?:[a-z0-9_]+:)?[a-z0-9_]+\Z")
_UNKNOWN = frozenset({"unknown", "unloaded", "minecraft:unknown", "minecraft:unloaded"})


def _coordinate(raw):
    if not isinstance(raw, Mapping) or not all(type(raw.get(axis)) is int for axis in ("x", "y", "z")):
        return None
    return raw["x"], raw["y"], raw["z"]


def _known_name(raw):
    # Registry validation belongs to the ordinary executor. Here "known"
    # means an explicitly observed identifier, not an unloaded/unknown marker.
    return isinstance(raw, str) and raw not in _UNKNOWN and _IDENTIFIER.fullmatch(raw) is not None


def _changed_names(before, after):
    return _known_name(before) and _known_name(after) and before != after


def _properties(raw):
    if not isinstance(raw, Mapping) or any(not isinstance(key, str) or not key for key in raw):
        return None
    for value in raw.values():
        if type(value) in {bool, int}:
            continue
        if type(value) is float and isfinite(value):
            continue
        if isinstance(value, str) and value and value not in _UNKNOWN:
            continue
        return None
    return dict(raw)


def _interaction_effect(data, requested):
    before, after = data.get("before"), data.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return False
    if any(block.get("loaded") is False or _coordinate(block.get("position")) != requested
           or not _known_name(block.get("name")) for block in (before, after)):
        return False
    if _changed_names(before["name"], after["name"]):
        return True
    prior_properties, current_properties = _properties(before.get("properties")), _properties(after.get("properties"))
    return prior_properties is not None and current_properties is not None and prior_properties != current_properties


def _batch_effect(name, args, data):
    raw_positions = args.get("positions")
    if not isinstance(raw_positions, list) or not 1 <= len(raw_positions) <= 64:
        return False
    requested = [_coordinate(p) for p in raw_positions]
    if any(p is None for p in requested) or len(set(requested)) != len(requested):
        return False
    proof = data.get("partial", data)
    if not isinstance(proof, Mapping):
        return False
    rows = proof.get("placements" if name == "place_batch" else "cleared")
    if not isinstance(rows, list) or len(rows) > 64:
        return False
    return any(isinstance(row, Mapping) and row.get("verified") is True
               and _coordinate(row.get("position")) in requested
               and _changed_names(row.get("before"), row.get("after")) for row in rows)


def observed_tool_effect(action, outcome):
    """Whether matching actual block observations prove SOME requested mutation.

    Partial verified batch cells count even if the overall batch failed.
    ``use_on_block`` normally has verified:false: its matching before/after
    descriptors may show a real mutation without proving the intended result.
    Movement, inventory changes and success/changed flags alone are intentionally
    not handled here. This predicate never asserts permission or task completion.
    """
    if not isinstance(action, Mapping) or not isinstance(outcome, Mapping):
        return False
    args, data = action.get("args"), outcome.get("data")
    if not isinstance(args, Mapping) or not isinstance(data, Mapping):
        return False
    name = action.get("name")
    if not isinstance(name, str):
        return False
    if name in {"place_batch", "dig_batch", "repair_batch"}:
        return type(outcome.get("success")) is bool and _batch_effect(name, args, data)
    requested = _coordinate(args.get("position"))
    if requested is None or outcome.get("success") is not True:
        return False
    if name == "dig":
        if outcome.get("verified") is not True or _coordinate(data.get("position")) != requested:
            return False
        if "replacement" in data:  # Ordinary Node dig proof.
            return _changed_names(data.get("broken"), data.get("replacement"))
        # Explicit simulator receipt: actual broken/name coordinates AND the
        # mutation flag are required; changed:true by itself proves nothing.
        return data.get("changed") is True and _changed_names(data.get("broken"), data.get("name"))
    if name == "use_on_block":
        return _interaction_effect(data, requested)
    return False
