"""Local-model construction practice, not Minecraft or model-weight training.

This deliberately small simulator tests signed coordinates, support, inventory,
site preparation and complete final geometry. It does NOT simulate Mineflayer
pathfinding/reach, server packets, item pickup, light, growth, mobs or physics.
The model chooses its goals and every action; the task oracle never supplies an
action sequence or rewrites a model decision. Generated experience stays in a
private run directory and is never promoted to normal-world memory automatically.
"""
import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
from time import perf_counter
import uuid


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENDPOINT = "http://localhost:11434/v1"
TASK_NAMES = ("negative_floor", "obstacle_floor", "footing_floor", "shelter", "partial_row")
REPAIR_TASK_NAMES = ("repair_shelter",)
SIMULATION_LIMITS = (
    "Synthetic blocks, adjacency support and inventory only; no Minecraft server, "
    "pathfinding, interaction reach, pickup, lighting, mobs or protocol. "
    "mine_resource is a finite synthetic cobblestone dispenser, NOT a mining test."
)
NATURAL = {"dirt", "grass_block", "oak_leaves", "birch_leaves", "oak_log", "stone"}
SOLID = {"cobblestone", "dirt", "grass_block", "oak_log", "oak_leaves", "birch_leaves", "stone", "chest", "crafting_table"}
KNOWN_BLOCKS = SOLID | NATURAL | {"air"}
ITEMS = (KNOWN_BLOCKS - {"air", "grass_block", "stone"}) | {"stone_pickaxe", "stone_axe"}
STABLE_REPAIR_SUPPORT = {"cobblestone", "dirt", "grass_block", "oak_log", "stone"}
REPAIR_HAZARDS = {"sand", "red_sand", "gravel", "anvil", "chipped_anvil", "damaged_anvil",
                  "fire", "soul_fire", "campfire", "soul_campfire", "magma_block", "cactus"}


def coordinates(value):
    if not isinstance(value, dict) or not all(type(value.get(axis)) is int for axis in ("x", "y", "z")):
        raise ValueError("Positions require integer x,y,z, preserving their signs.")
    return tuple(value[axis] for axis in ("x", "y", "z"))


def position(value):
    return dict(zip(("x", "y", "z"), value))


def cell_key(value):
    return ",".join(str(component) for component in value)


def cells_between(minimum, maximum):
    if any(low > high for low, high in zip(minimum, maximum)):
        raise ValueError("Minimum exceeds maximum.")
    for x in range(minimum[0], maximum[0] + 1):
        for y in range(minimum[1], maximum[1] + 1):
            for z in range(minimum[2], maximum[2] + 1):
                yield x, y, z


class VersionedBlockMap(dict):
    """Synthetic block updates invalidate receipts, including same-name ABA.

    These revision counters model the executor's blockUpdate invalidation, not
    Minecraft physics. Deliberate fixture/external writes go through the same
    mutation hooks as verified tool writes, so an old receipt is never revived
    merely by putting the old block name back at its coordinate.
    """
    def __init__(self, initial, on_mutation=None):
        super().__init__(initial)
        self.revisions = {p: 0 for p in initial}
        self._on_mutation = on_mutation

    def touch(self, p):
        self.revisions[p] = self.revisions.get(p, 0) + 1
        if self._on_mutation:
            self._on_mutation(p)

    def __setitem__(self, p, block):
        self.touch(p)
        super().__setitem__(p, block)

    def __delitem__(self, p):
        super().__delitem__(p)
        self.touch(p)

    def update(self, values=(), **kwargs):
        for p, block in dict(values, **kwargs).items():
            self[p] = block

    def pop(self, p, *default):
        if len(default) > 1:
            raise TypeError("pop expected at most two arguments")
        if p in self:
            result = self[p]
            del self[p]
            return result
        if default:
            return default[0]
        raise KeyError(p)

    def clear(self):
        for p in tuple(self):
            del self[p]

    def popitem(self):
        if not self:
            raise KeyError("popitem(): dictionary is empty")
        p = next(reversed(self))
        return p, self.pop(p)

    def setdefault(self, p, default=None):
        if p not in self:
            self[p] = default
        return self[p]

    def __ior__(self, values):
        self.update(values)
        return self


class VersionedPropertiesMap(VersionedBlockMap):
    """Properties and in-place property updates share their block revision."""
    def __init__(self, blocks):
        self.blocks = blocks
        super().__init__({}, on_mutation=blocks.touch)

    def __setitem__(self, p, properties):
        if not isinstance(properties, dict):
            raise ValueError("Synthetic block properties must be objects.")
        # No external mutable alias can alter a fingerprint invisibly.
        copied = json.loads(json.dumps(properties))
        tracked = VersionedBlockMap(copied, on_mutation=lambda unused: self.blocks.touch(p))
        super().__setitem__(p, tracked)


@dataclass
class ConstructionTask:
    name: str
    seed: int
    objective: str
    minimum: tuple
    maximum: tuple
    expected: dict
    initial: dict
    inventory: dict
    fixtures: list
    bot_position: tuple
    quarry_supply: int = 256
    owned_mistake_fixture: tuple = ()

    def snapshot_description(self):
        return {
            "name": self.name, "seed": self.seed, "objective": self.objective,
            "construction_scope": {"min": position(self.minimum), "max": position(self.maximum)},
            "expected_cells": {cell_key(p): name for p, name in self.expected.items()},
            "initial_cells": {cell_key(p): name for p, name in self.initial.items()},
            "inventory": dict(self.inventory), "fixtures": self.fixtures,
            "owned_mistake_fixture": {
                "enabled": bool(self.owned_mistake_fixture),
                "positions": [position(p) for p in self.owned_mistake_fixture],
                "phase": "Trusted harness injection after frozen-contract registration; not model actions or training.",
            },
            "simulation_limits": SIMULATION_LIMITS,
        }


def make_task(name, seed):
    """Seeded REQUESTS and world layouts, never scripted construction actions."""
    if name not in TASK_NAMES + REPAIR_TASK_NAMES:
        raise ValueError("Unknown construction task: " + str(name))
    family = "shelter" if name == "repair_shelter" else name
    rng = random.Random(seed)
    x0, z0, floor = rng.randint(-8, 4), rng.randint(-18, -9), rng.randint(61, 68)
    width, depth = rng.randint(3, 5), rng.randint(3, 4)
    if family == "shelter":
        width, depth = rng.randint(4, 5), rng.randint(4, 5)
    x1, z1 = x0 + width - 1, z0 + depth - 1
    top = floor + (3 if family == "shelter" else 2)
    initial = {}
    # Every cell inside this small loaded arena is explicit. Beyond it is UNKNOWN.
    for p in cells_between((x0 - 3, floor - 2, z0 - 3), (x1 + 3, top + 2, z1 + 3)):
        initial[p] = "dirt" if p[1] == floor - 2 else "grass_block" if p[1] == floor - 1 else "air"
    fixtures = [
        {"name": "chest", "position": position((x1 + 2, floor, z0))},
        {"name": "crafting_table", "position": position((x1 + 2, floor, z0 + 1))},
    ]
    for fixture in fixtures:
        initial[coordinates(fixture["position"])] = fixture["name"]
    expected = {p: "cobblestone" for p in cells_between((x0, floor, z0), (x1, floor, z1))}
    detail = f"an exact {width} by {depth} cobblestone floor at Y={floor}, X={x0}..{x1}, Z={z0}..{z1} (inclusive)"
    if name == "obstacle_floor":
        expected.update({p: "air" for p in cells_between((x0, floor + 1, z0), (x1, floor + 2, z1))})
        blocked = rng.sample(list(expected), min(5, len(expected)))
        for index, p in enumerate(blocked):
            initial[p] = ("dirt", "oak_leaves", "grass_block")[index % 3]
        # Guarantee a real obstruction in the floor, independently of RNG draws.
        initial[(x0, floor, z0)] = "dirt"
        detail += f" and leave both layers Y={floor + 1}..{floor + 2} above the whole floor empty; natural obstructions may be cleared only inside that volume"
    elif name == "footing_floor":
        holes = [(x0, floor - 1, z0), (x0 + 1, floor - 1, z0)]
        for p in holes:
            initial[p] = "air"
            expected[p] = "cobblestone"
        detail += "; also fill ONLY these two foundation holes with cobblestone: " + json.dumps([position(p) for p in holes])
        detail += f". The floor itself remains at Y={floor}, not at the ground/support height"
    elif family == "shelter":
        expected = {}
        for p in cells_between((x0, floor, z0), (x1, floor + 3, z1)):
            x, y, z = p
            expected[p] = "cobblestone" if y in {floor, floor + 3} or x in {x0, x1} or z in {z0, z1} else "air"
        door_x = x0 + width // 2
        doorway = [(door_x, floor + 1, z1), (door_x, floor + 2, z1)]
        for p in doorway:
            expected[p] = "air"
        initial[(x0 + 1, floor + 1, z0 + 1)] = "oak_leaves"
        detail = (
            f"a {width} by {depth} outside-footprint cobblestone shelter, X={x0}..{x1}, Z={z0}..{z1} (inclusive). "
            f"The entire floor is Y={floor}, the perimeter walls are Y={floor + 1}..{floor + 2}, "
            f"and the complete flat roof is Y={floor + 3}. The inside X={x0 + 1}..{x1 - 1}, "
            f"Z={z0 + 1}..{z1 - 1}, Y={floor + 1}..{floor + 2} must be air. "
            "Leave a two-high open doorway in the south wall at " + json.dumps([position(p) for p in doorway]) + ". "
            "Natural obstacles inside this volume may be cleared"
        )
    elif name == "partial_row":
        completed = [(x, floor, z0) for x in range(x0, x1 + 1)]
        for p in completed:
            initial[p] = "cobblestone"
        detail += "; one existing cobblestone row is already correct. Keep it and finish the remaining floor, without demolishing or replacing any completed blocks"
    owned_mistake_fixture = ()
    if name == "repair_shelter":
        initial.update(expected)
        interior = [p for p, block in expected.items()
                    if block == "air" and p[1] == floor + 1 and p[2] != z1]
        owned_mistake_fixture = tuple(rng.sample(interior, 2))
        detail = (
            "the existing completed shelter described as " + detail + ". Keep every already-correct block. "
            "This explicit repair benchmark injects two verified current-task OWN misplaced cobblestones "
            "at final-air interior cells immediately after your complete final geometry is frozen: " +
            json.dumps([position(p) for p in owned_mistake_fixture]) + ". "
            "The injection is trusted simulator fixture setup, not your model actions or training. "
            "Observe the wrong blocks and choose controlled own-block repair to restore the required air; "
            "do not demolish the correct floor, walls or roof"
        )
    minimum = (x0, floor - (1 if name == "footing_floor" else 0), z0)
    maximum = (x1, top, z1)
    required = sum(block == "cobblestone" and initial.get(p) != block for p, block in expected.items())
    held = required if name in {"negative_floor", "partial_row"} else max(0, required - rng.randint(3, 7))
    if owned_mistake_fixture:
        held = len(owned_mistake_fixture)  # Reserved for the labelled verified fixture placements.
    objective = (
        "Execute this construction request: " + ("repair " if name == "repair_shelter" else "build ") + detail + ". "
        "Keep all ground outside explicitly requested foundation holes, the nearby chest/table, their supports/access, "
        "and every block outside the requested geometry unchanged. Do not add a final held-inventory goal for material "
        "that the build consumes. Use observations; coordinate signs are significant. "
        "If materials are short, mine_resource(cobblestone) is a finite synthetic dispenser in this simulator, not real mining. "
        "Declare your own complete final-world goals and choose your own actions."
    )
    return ConstructionTask(name, seed, objective, minimum, maximum, expected, initial,
                            {"cobblestone": held, "stone_pickaxe": 1, "stone_axe": 1},
                            fixtures, (x0 - 2, floor, z0 - 2), owned_mistake_fixture=owned_mistake_fixture)


class SimulatedConstructionBridge:
    """Small synchronous world mutation wrapped in the agent's async interface."""
    def __init__(self, task, stream=None):
        self.task, self.stream = task, stream
        self.blocks, self.inventory = VersionedBlockMap(task.initial), dict(task.inventory)
        self.block_properties = VersionedPropertiesMap(self.blocks)
        self.player_positions = {}  # Explicit synthetic fixture observations, not player tracking.
        self._construction_contract = None
        self._owned_placements = {}
        self._fixture_setup = {"required": bool(task.owned_mistake_fixture), "complete": False,
                               "receipts": [], "phase": "trusted-harness-owned-mistake-injection"}
        self.bot_position, self.supply = task.bot_position, task.quarry_supply
        self.events, self.calls = [], []
        self.world = {"id": f"construction-sim:{task.name}:{task.seed}", "sessionId": str(uuid.uuid4()), "dimension": "overworld"}
        self.protected = set(task.initial) - set(task.expected)
        self.protected.update(p for p, block in task.initial.items() if block == "cobblestone")
        self.latest_state = self.state()

    def state(self):
        items = []
        for item, total in sorted(self.inventory.items()):
            while total > 0:
                count = min(64, total)
                items.append({"name": item, "count": count, "slot": len(items) + 9})
                total -= count
        return {
            "ready": True, "world": dict(self.world), "inventory": items,
            "stats": {"position": position(self.bot_position), "health": 20, "food": 20},
            "observedAt": datetime.now(timezone.utc).isoformat(),
            "nearbyBlocks": list(self.task.fixtures),
            "constructionPractice": {
                "source": "python-synthetic-simulator", "limits": SIMULATION_LIMITS,
                "construction_scope": {"min": position(self.task.minimum), "max": position(self.task.maximum)},
                "loaded_arena": {
                    "min": position(tuple(min(p[axis] for p in self.blocks) for axis in range(3))),
                    "max": position(tuple(max(p[axis] for p in self.blocks) for axis in range(3))),
                },
                "quarry_supply_remaining": self.supply,
                "fixture_protection": "Ground, chest/table, outside geometry and completed cobblestone are read-only.",
                "construction_status": self._construction_status(),
            },
        }

    async def get_state(self):
        self.latest_state = self.state()
        return json.loads(json.dumps(self.latest_state))

    async def cancel_task(self):
        await self.set_construction_contract(None)
        return {"success": True, "data": {"busy": False, "active": False}}

    def _world_identity(self):
        return {key: self.world.get(key) for key in ("id", "sessionId", "dimension")}

    async def set_construction_contract(self, contract):
        """Trusted controller handshake, intentionally absent from tool dispatch.

        Model arguments never supply a task ID, desired state or ownership. A
        registered task keeps an immutable compiled geometry; clearing/changing
        the task drops all ephemeral repair authority, never adopts old blocks.
        """
        if contract is None:
            self._construction_contract = None
            self._owned_placements.clear()
            return {"active": False}
        if not isinstance(contract, dict) or set(contract) != {"task_id", "world", "desired_cells"}:
            raise ValueError("INVALID_CONSTRUCTION_CONTRACT")
        task_id, supplied_world, cells = contract["task_id"], contract["world"], contract["desired_cells"]
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 200:
            raise ValueError("INVALID_CONSTRUCTION_CONTRACT: task_id")
        if not isinstance(supplied_world, dict) or any(supplied_world.get(key) != value
                                                       for key, value in self._world_identity().items()):
            raise ValueError("CONSTRUCTION_WORLD_CHANGED")
        if not isinstance(cells, dict) or not 1 <= len(cells) <= 4096:
            raise ValueError("INVALID_CONSTRUCTION_CONTRACT: desired_cells")
        desired = {}
        try:
            for key, block in cells.items():
                if not isinstance(key, str) or block not in KNOWN_BLOCKS:
                    raise ValueError("Expected a canonical coordinate key and known synthetic block.")
                parts = key.split(",")
                if len(parts) != 3:
                    raise ValueError("Expected three integer coordinates.")
                p = tuple(int(value) for value in parts)
                if cell_key(p) != key:
                    raise ValueError("Coordinate keys must retain canonical integer signs.")
                desired[p] = block
        except (ValueError, TypeError) as error:
            raise ValueError("INVALID_CONSTRUCTION_CONTRACT: desired cells") from error
        normalized = {"task_id": task_id, "world": self._world_identity(), "desired": desired}
        if self._construction_contract and self._construction_contract["task_id"] == task_id:
            if self._construction_contract != normalized:
                raise ValueError("IMMUTABLE_CONSTRUCTION_CONTRACT: current-task geometry cannot change")
            return {"active": True, "task_id": task_id, "cell_count": len(desired)}
        if self.task.owned_mistake_fixture and not self._fixture_setup["complete"]:
            if any(desired.get(p) != "air" or self.blocks.get(p) != "air"
                   for p in self.task.owned_mistake_fixture):
                raise ValueError("INVALID_REPAIR_FIXTURE_CONTRACT: frozen targets must require air")
        self._construction_contract = normalized
        self._owned_placements.clear()
        # Optional benchmark setup is explicitly trusted harness work. This is
        # the only consistency bypass, private to the fixture registration phase;
        # it cannot be requested through execute_tool or model-supplied arguments.
        if self.task.owned_mistake_fixture and not self._fixture_setup["complete"]:
            receipts = []
            for p in self.task.owned_mistake_fixture:
                receipt = self.place(p, "cobblestone")
                receipts.append(receipt)
                if not receipt["success"]:
                    self._fixture_setup["receipts"] = receipts
                    raise RuntimeError("REPAIR_FIXTURE_SETUP_FAILED: " + receipt["message"])
                self.events[-1]["source"] = "trusted-harness-owned-mistake-injection"
            self._fixture_setup.update(complete=True, receipts=receipts, task_id=task_id,
                                       world=self._world_identity())
        if self.stream:
            self.stream.write(json.dumps({"event": "trusted-construction-contract-registration",
                "world": self._world_identity(), "task_id": task_id, "desired_cell_count": len(desired),
                "owned_mistake_fixture": self._fixture_setup}) + "\n")
            self.stream.flush()
        return {"active": True, "task_id": task_id, "cell_count": len(desired)}

    def _fingerprint(self, p):
        return json.dumps({"name": self.blocks.get(p, "unknown"),
                           "properties": self.block_properties.get(p, {})}, sort_keys=True, separators=(",", ":"))

    def _record_owned_placement(self, p, item, inventory_before, inventory_after):
        contract = self._construction_contract
        if (not contract or p not in contract["desired"] or contract["world"] != self._world_identity()
                or inventory_before - inventory_after != 1):
            return
        self._owned_placements[p] = {
            "position": position(p), "task_id": contract["task_id"], "world": dict(contract["world"]),
            "placed": item, "fingerprint": self._fingerprint(p), "revision": self.blocks.revisions[p],
            "inventory_delta": -1, "source": "verified-backend-air-to-block-placement",
        }

    def _repair_rejection(self, p):
        contract = self._construction_contract
        if not contract:
            return "NO_CONSTRUCTION_CONTRACT"
        if contract["world"] != self._world_identity():
            return "CONSTRUCTION_WORLD_CHANGED"
        if p not in contract["desired"]:
            return "OUTSIDE_CONSTRUCTION_CONTRACT"
        if p not in self.blocks or self.blocks[p] == "unknown":
            return "UNLOADED_BLOCK"
        if self.blocks[p] == contract["desired"][p]:
            return "CONSTRUCTION_TARGET_ALREADY_CORRECT"
        if p in self.protected or self.blocks[p] in {"chest", "crafting_table"}:
            return "PROTECTED_BLOCK"
        receipt = self._owned_placements.get(p)
        if not receipt:
            return "NOT_OWNED_PLACEMENT"
        if receipt["world"] != self._world_identity() or receipt["task_id"] != contract["task_id"]:
            return "STALE_PLACEMENT_RECEIPT"
        if (receipt["revision"] != self.blocks.revisions.get(p) or
                receipt["fingerprint"] != self._fingerprint(p)):
            return "PLACEMENT_RECEIPT_CHANGED"
        if self.blocks[p] != "cobblestone" or self.block_properties.get(p, {}).get("waterlogged") in (True, "true"):
            return "UNSAFE_REPAIR_BLOCK"
        if p == (self.bot_position[0], self.bot_position[1] - 1, self.bot_position[2]):
            return "UNDERFOOT_MINING"
        if any(p == (feet[0], feet[1] - 1, feet[2]) for feet in self.player_positions.values()):
            return "PLAYER_FOOTING"
        adjacent = [(p[0] + dx, p[1] + dy, p[2] + dz) for dx, dy, dz in
                    ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
        if any(q not in self.blocks or self.blocks[q] == "unknown" for q in adjacent):
            return "UNKNOWN_REPAIR_NEIGHBOR"
        for q in adjacent:
            if (self.blocks[q] in {"water", "lava"} or
                    self.block_properties.get(q, {}).get("waterlogged") in (True, "true")):
                return "LIQUID_ADJACENT"
            if self.blocks[q] in REPAIR_HAZARDS:
                return "HAZARDOUS_ADJACENCY"
        above = self.blocks[(p[0], p[1] + 1, p[2])]
        if above != "air" and above not in STABLE_REPAIR_SUPPORT:
            return "UNSUPPORTED_OVERBURDEN"
        if not self.inventory.get("stone_pickaxe", 0):
            return "MISSING_TOOL"
        return None

    def _construction_status(self):
        contract = self._construction_contract
        if not contract:
            return {"active": False, "repairable_owned_mismatches": [], "complete": True}
        eligible, owned, rejected = [], [], Counter()
        for p in sorted(self._owned_placements):
            reason = self._repair_rejection(p)
            receipt = self._owned_placements[p]
            owned.append({"position": position(p), "placed_block": receipt["placed"],
                          "desired_block": contract["desired"].get(p),
                          "observed_block": self.blocks.get(p, "unknown"),
                          "repair_eligible": reason is None, "reason": reason})
            if reason:
                rejected[reason] += 1
            else:
                eligible.append({"position": position(p), "observed": self.blocks[p],
                                 "desired": contract["desired"][p],
                                 "source": "verified-current-task-owned-placement"})
        return {"active": True, "task_id": contract["task_id"], "world": dict(contract["world"]),
                "world_session_id": contract["world"]["sessionId"],
                "accepted_cell_count": len(contract["desired"]), "owned_placement_count": len(owned),
                "owned_placements": owned[:32],
                "repairable_owned_mismatches": eligible[:32], "repairable_count": len(eligible),
                "complete": len(owned) <= 32, "omitted_count": max(0, len(owned) - 32),
                "repairable_complete": len(eligible) <= 32,
                "repairable_omitted_count": max(0, len(eligible) - 32),
                "rejected_receipts": dict(rejected),
                "note": "Read-only backend provenance facts; model claims cannot grant repair authority."}

    async def construction_status(self):
        return self._construction_status()

    def snapshot(self):
        return {"blocks": {cell_key(p): block for p, block in self.blocks.items()},
                "inventory": dict(self.inventory), "position": position(self.bot_position),
                "events": list(self.events), "quarry_supply_remaining": self.supply,
                "owned_mistake_fixture": json.loads(json.dumps(self._fixture_setup))}

    @staticmethod
    def outcome(data=None, error=None, message=""):
        if error:
            return {"success": False, "verified": False, "status": "failed", "message": message or error,
                    "data": {**(data or {}), "error_code": error}}
        return {"success": True, "verified": True, "status": "complete", "message": message,
                "data": data or {}}

    def descriptor(self, p):
        name = self.blocks.get(p, "unknown")
        return {"position": position(p), "name": name, "loaded": name != "unknown",
                "boundingBox": "block" if name in SOLID else "empty",
                "properties": json.loads(json.dumps(self.block_properties.get(p, {}))),
                "diggable": name in SOLID,
                "natural_obstruction": name in NATURAL,
                "protected": p in self.protected}

    def permitted(self, p):
        return all(low <= component <= high for component, low, high in zip(p, self.task.minimum, self.task.maximum))

    def preflight(self, positions):
        if not isinstance(positions, list) or not 1 <= len(positions) <= 64:
            return self.outcome(error="INVALID_ARGUMENT", message="Supply 1–64 explicit positions.")
        try:
            targets = [coordinates(p) for p in positions]
        except ValueError as error:
            return self.outcome(error="INVALID_ARGUMENT", message=str(error))
        if len(set(targets)) != len(targets):
            return self.outcome(error="INVALID_ARGUMENT", message="Batch target coordinates must be unique.")
        for p in targets:
            if not self.permitted(p):
                return self.outcome({"position": position(p)}, "OUTSIDE_CONSTRUCTION_SCOPE",
                                    "Coordinate is outside the requested construction area; preserve signs.")
            if p not in self.blocks:
                return self.outcome({"position": position(p)}, "UNLOADED_BLOCK", "Unknown is not air.")
        return targets

    def place(self, p, item):
        existing = self.blocks[p]
        if existing == item:
            return self.outcome({"position": position(p), "name": item, "already_correct": True,
                                 "inventory_delta": 0})
        if p in self.protected:
            return self.outcome({"position": position(p)}, "PROTECTED_BLOCK", "Ground, fixtures and completed blocks are protected.")
        if existing != "air":
            return self.outcome({"position": position(p), "name": existing}, "TARGET_OCCUPIED",
                                "The placement target itself is occupied; this is not a placement on its support.")
        if p in {self.bot_position, (self.bot_position[0], self.bot_position[1] + 1, self.bot_position[2])}:
            return self.outcome({"position": position(p)}, "OCCUPIED_BY_BOT")
        adjacent = [(p[0] + dx, p[1] + dy, p[2] + dz) for dx, dy, dz in
                    ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
        if not any(self.blocks.get(q) in SOLID for q in adjacent):
            return self.outcome({"position": position(p)}, "NO_SUPPORT", "Needs an observed adjacent solid block; unknown/air are not support.")
        if self.inventory.get(item, 0) < 1:
            return self.outcome(error="MISSING_ITEM", message="Not carrying " + item)
        before = self.inventory[item]
        self.blocks[p] = item
        self.inventory[item] -= 1
        self.events.append({"kind": "place", "position": position(p), "before": existing, "after": item})
        self._record_owned_placement(p, item, before, self.inventory[item])
        return self.outcome({"position": position(p), "name": item, "inventory_before": before,
                             "inventory_after": self.inventory[item], "inventory_delta": -1,
                             "before": existing, "after": item, "observed": item})

    def dig(self, p):
        existing = self.blocks[p]
        if p in self.protected:
            return self.outcome({"position": position(p), "name": existing}, "PROTECTED_BLOCK",
                                "Do not demolish ground, fixtures or completed cobblestone.")
        if p == (self.bot_position[0], self.bot_position[1] - 1, self.bot_position[2]):
            return self.outcome(error="UNDERFOOT_MINING")
        if existing == "air":
            return self.outcome({"position": position(p), "already_clear": True, "changed": False})
        if existing not in NATURAL:
            if existing == "cobblestone" and self._repair_rejection(p) is None:
                return self.outcome({"position": position(p), "name": existing, "diggable": True,
                    "own_block_repair": {"eligible": True, "reason": None,
                        "desired": self._construction_contract["desired"][p],
                        "source": "current-backend-ledger-and-frozen-contract",
                        "available_tool": "repair_batch"}}, "REPAIR_REQUIRED",
                    "This block is physically breakable, but ordinary dig has no build-demolition authority. "
                    "The backend confirms a current-task owned mismatch; repair_batch is the separate available tool "
                    "and must be chosen explicitly. Nothing was changed.")
            return self.outcome({"position": position(p), "name": existing}, "UNSAFE_BLOCK",
                                "Only observed natural obstructions are clearable; player construction is not.")
        self.blocks[p] = "air"
        drop = "cobblestone" if existing == "stone" else "dirt" if existing == "grass_block" else existing if existing in {"dirt", "oak_log"} else None
        if drop:
            self.inventory[drop] = self.inventory.get(drop, 0) + 1
        self.events.append({"kind": "dig", "position": position(p), "before": existing, "after": "air"})
        return self.outcome({"position": position(p), "name": "air", "broken": existing,
                             "synthetic_drop": drop, "changed": True})

    def repair(self, p, tool=None):
        """Remove one provably owned WRONG block, never choose its replacement."""
        reason = self._repair_rejection(p)
        if reason:
            return self.outcome({"position": position(p)}, reason,
                                "No repair authorized by current backend provenance and final geometry.")
        if tool is not None and (tool != "stone_pickaxe" or not self.inventory.get(tool, 0)):
            return self.outcome({"position": position(p)}, "UNSUITABLE_TOOL")
        existing, receipt = self.blocks[p], self._owned_placements[p]
        self.blocks[p] = "air"
        self._owned_placements.pop(p, None)
        self.inventory["cobblestone"] = self.inventory.get("cobblestone", 0) + 1
        self.events.append({"kind": "repair", "position": position(p), "before": existing, "after": "air",
                            "desired": self._construction_contract["desired"][p],
                            "owned_placement_receipt": dict(receipt), "source": "verified-current-task-own-repair"})
        return self.outcome({"position": position(p), "before": existing, "after": "air", "observed": "air",
                             "name": "air", "broken": existing, "changed": True,
                             "desired_block": self._construction_contract["desired"][p],
                             "task_id": self._construction_contract["task_id"],
                             "provenance": "verified_current_task_placement",
                             "synthetic_drop": "cobblestone", "tool": tool or "stone_pickaxe",
                             "note": "Immediate synthetic pickup is not evidence of Minecraft item-pickup competence."})

    def batch_evidence(self, name, item, targets, originals, receipts, inventory_before):
        """Node-compatible spatial proof lists alongside untouched raw receipts."""
        placements, cleared, skipped, failed = [], [], [], None
        for p, result in zip(targets, receipts):
            data = result["data"]
            row = {"position": position(p), "before": originals[p], "after": self.blocks.get(p, "unknown"),
                   "observed": self.blocks.get(p, "unknown"), "verified": result["verified"]}
            if not result["success"]:
                row["reason"] = data.get("error_code", "SIMULATION_TOOL_FAILED")
                skipped.append(row)
                failed = {"position": position(p), "code": row["reason"], "message": result["message"]}
            elif name.startswith("place") and data.get("inventory_delta") == -1:
                placements.append({**row, "inventory_before": data["inventory_before"],
                                   "inventory_after": data["inventory_after"]})
            elif name.startswith(("dig", "repair")) and data.get("changed") is True:
                cleared.append({**row, **{key: data[key] for key in
                    ("desired_block", "task_id", "provenance", "tool") if key in data}})
            else:
                skipped.append({**row, "reason": "already_correct" if name.startswith("place") else "already_air"})
        complete = len(receipts) == len(targets) and failed is None
        return {**({"mode": "own_placement_repair", "task_id": self._construction_contract["task_id"]}
                   if name == "repair_batch" and self._construction_contract else {}),
                "item": item if name.startswith("place") else None, "requested_count": len(targets),
                "complete": complete, "verified": complete, "receipts": receipts,
                "placements": placements, "cleared": cleared, "skipped": skipped, "failed": failed,
                "placed_count": len(placements), "cleared_count": len(cleared),
                "inventory_before": inventory_before,
                "inventory_after": self.inventory.get(item, 0) if name.startswith("place") else dict(self.inventory)}

    def execute(self, tool):
        if not isinstance(tool, dict) or not isinstance(tool.get("args", {}), dict):
            return self.outcome(error="INVALID_ARGUMENT")
        name, args = tool.get("name"), tool.get("args", {})
        if name == "inspect":
            if "position" in args:
                p = coordinates(args["position"])
                if p not in self.blocks:
                    return self.outcome(self.descriptor(p), "UNLOADED_BLOCK", "This coordinate is unknown, not air.")
                data = self.descriptor(p)
                data.update(below=self.descriptor((p[0], p[1] - 1, p[2])),
                            above=self.descriptor((p[0], p[1] + 1, p[2])), protected=p in self.protected)
                if self._construction_contract:
                    reason = self._repair_rejection(p)
                    data["own_block_repair"] = {"eligible": reason is None, "reason": reason,
                        "desired": self._construction_contract["desired"].get(p),
                        "source": "current-backend-ledger-and-frozen-contract"}
                return self.outcome(data)
            subject = args.get("item")
            if subject not in KNOWN_BLOCKS | ITEMS:
                return self.outcome(error="UNKNOWN_ITEM")
            return self.outcome({"name": subject, "isItem": subject in ITEMS,
                                 "isBlock": subject in KNOWN_BLOCKS, "plantedBy": [],
                                 "boundingBox": "block" if subject in SOLID else "empty",
                                 "source": "synthetic-registry-subset"})
        if name == "inspect_region":
            minimum, maximum = coordinates(args.get("min")), coordinates(args.get("max"))
            dimensions = [high - low + 1 for low, high in zip(minimum, maximum)]
            count = dimensions[0] * dimensions[1] * dimensions[2]
            if min(dimensions) < 1 or count > 4096:
                return self.outcome(error="REGION_TOO_LARGE", message="Use a valid inclusive region of at most 4096 cells.")
            cells = [self.descriptor(p) for p in cells_between(minimum, maximum)]
            return self.outcome({"min": position(minimum), "max": position(maximum),
                                 "dimensions": dimensions, "cell_count": count, "cells": cells})
        if name in {"place", "place_batch", "dig", "dig_batch", "repair_batch"}:
            if name == "repair_batch" and set(args) - {"positions", "tool"}:
                return self.outcome(error="INVALID_ARGUMENT",
                                    message="Ownership, task and desired geometry cannot come from model arguments.")
            raw_positions = args.get("positions") if name.endswith("_batch") else [args.get("position")]
            targets = self.preflight(raw_positions)
            if isinstance(targets, dict):
                return targets
            item = args.get("item", "cobblestone")
            contract = self._construction_contract
            if contract:
                if contract["world"] != self._world_identity():
                    return self.outcome(error="CONSTRUCTION_WORLD_CHANGED")
                for p in targets:
                    if p not in contract["desired"]:
                        return self.outcome({"position": position(p)}, "OUTSIDE_CONSTRUCTION_CONTRACT")
                    desired = contract["desired"][p]
                    if name.startswith("place") and desired != item:
                        return self.outcome({"position": position(p), "desired": desired, "attempted": item},
                                            "CONSTRUCTION_PLACEMENT_MISMATCH",
                                            "Final geometry does not request this item at the target; no batch cell was changed.")
                    if name.startswith("dig") and self.blocks[p] != "air" and self.blocks[p] == desired:
                        return self.outcome({"position": position(p), "desired": desired},
                                            "CONSTRUCTION_TARGET_ALREADY_CORRECT")
            if name == "repair_batch":
                for p in targets:
                    reason = self._repair_rejection(p)
                    if reason:
                        return self.outcome({"failed": {"position": position(p), "code": reason},
                            "cleared": [], "skipped": [{"position": position(p), "reason": reason,
                                "before": self.blocks[p], "after": self.blocks[p], "verified": False}],
                            "receipts": [], "complete": False}, reason,
                            "Entire repair batch rejected before mutation; model ownership claims are not authority.")
                if "tool" in args and (args["tool"] != "stone_pickaxe" or not self.inventory.get(args["tool"], 0)):
                    return self.outcome(error="UNSUITABLE_TOOL")
            if name.startswith("place"):
                if item != "cobblestone":
                    return self.outcome(error="UNSUPPORTED_SIMULATION_ITEM", message="These tasks permit cobblestone construction only.")
                needed = sum(self.blocks[p] != item for p in targets)
                if self.inventory.get(item, 0) < needed:
                    return self.outcome({"needed": needed, "held": self.inventory.get(item, 0)}, "MISSING_ITEM")
            receipts, originals = [], {p: self.blocks[p] for p in targets}
            inventory_before = self.inventory.get(item, 0) if name.startswith("place") else dict(self.inventory)
            for p in targets:
                result = (self.place(p, item) if name.startswith("place") else
                          self.repair(p, args.get("tool")) if name == "repair_batch" else self.dig(p))
                receipts.append(result)
                if not result["success"]:
                    failure_data = {"partial": self.batch_evidence(name, item, targets, originals, receipts, inventory_before),
                                    "cause": result["data"].get("error_code")}
                    if result["data"].get("error_code") == "REPAIR_REQUIRED":
                        # Keep eligibility feedback in planner-visible data,
                        # not only in raw receipts omitted by history compaction.
                        failure_data.update({key: value for key, value in result["data"].items()
                                             if key != "error_code"})
                    return self.outcome(failure_data,
                                        result["data"].get("error_code"), result["message"])
            return self.outcome(receipts[0]["data"] if not name.endswith("_batch") else
                                self.batch_evidence(name, item, targets, originals, receipts, inventory_before))
        if name == "mine_resource":
            count = args.get("count")
            if args.get("item") != "cobblestone" or type(count) is not int or not 1 <= count <= 64:
                return self.outcome(error="INVALID_ARGUMENT", message="Synthetic dispenser accepts cobblestone count 1–64.")
            mode = args.get("collection_mode", "additional")
            if mode not in {"additional", "ensure_inventory"}:
                return self.outcome(error="INVALID_ARGUMENT", message="Use additional or ensure_inventory.")
            before = self.inventory.get("cobblestone", 0)
            wanted = count if mode == "additional" else max(0, count - before)
            if wanted > self.supply:
                return self.outcome(error="SYNTHETIC_QUARRY_EXHAUSTED")
            self.inventory["cobblestone"] = before + wanted
            self.supply -= wanted
            self.events.append({"kind": "synthetic_acquisition", "item": "cobblestone", "count": wanted})
            return self.outcome({"item": "cobblestone", "requested_count": count, "collection_mode": mode,
                                 "before": before, "after": before + wanted, "delta": wanted,
                                 "synthetic": True, "note": "Dispensed materials, NOT verified Minecraft mining."})
        if name == "find_blocks":
            names = args.get("names", [])
            if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
                return self.outcome(error="INVALID_ARGUMENT")
            matches = [self.descriptor(p) for p, block in self.blocks.items() if block in names]
            return self.outcome({"blocks": matches[:24], "complete": len(matches) <= 24,
                                 "source": "synthetic-loaded-arena"})
        if name == "equip":
            return self.outcome({"held": args.get("item")}) if self.inventory.get(args.get("item"), 0) else self.outcome(error="MISSING_ITEM")
        if name == "walk_to":
            p = coordinates(args.get("position"))
            if args.get("adjacent"):
                options = [(p[0] + dx, p[1], p[2] + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                p = next((q for q in options if self.walkable(q)), None)
            if p is None or not self.walkable(p):
                return self.outcome(error="NO_SAFE_STANCE", message="Synthetic destination needs air feet/head and solid ground.")
            self.bot_position = p
            return self.outcome({"position": position(p), "synthetic": True,
                                 "note": "Destination checked only; pathfinding is NOT simulated."})
        if name == "container":
            p = coordinates(args.get("position"))
            if self.blocks.get(p) == "chest":
                return self.outcome({"position": position(p), "items": {}, "synthetic": True})
            return self.outcome(error="NOT_CONTAINER")
        return self.outcome(error="UNSUPPORTED_SIMULATION_TOOL", message="Tool is not modeled by construction practice: " + str(name))

    def walkable(self, p):
        return (self.blocks.get(p) == "air" and self.blocks.get((p[0], p[1] + 1, p[2])) == "air"
                and self.blocks.get((p[0], p[1] - 1, p[2])) in SOLID)

    async def execute_tool(self, tool):
        try:
            result = self.execute(tool)
        except (ValueError, TypeError, KeyError) as error:
            result = self.outcome(error="INVALID_ARGUMENT", message=str(error))
        self.calls.append({"tool": tool, "result": result})
        self.latest_state = self.state()
        if self.stream:
            self.stream.write(json.dumps({"world": self.world, "call": len(self.calls), "tool": tool, "result": result}) + "\n")
            self.stream.flush()
        return result


def grade_task(task, snapshot):
    """Independent every-cell oracle, unaffected by the model's goal declaration."""
    actual = snapshot["blocks"]
    mismatches = [{"position": position(p), "expected": block, "observed": actual.get(cell_key(p), "unknown")}
                  for p, block in task.expected.items() if actual.get(cell_key(p)) != block]
    preserved = [{"position": position(p), "expected": block, "observed": actual.get(cell_key(p), "unknown")}
                 for p, block in task.initial.items() if p not in task.expected and actual.get(cell_key(p)) != block]
    initial_keys = {cell_key(p) for p in task.initial}
    preserved.extend({"position_key": key, "expected": "unknown", "observed": block}
                     for key, block in actual.items() if key not in initial_keys)
    demolished = [event for event in snapshot.get("events", []) if event.get("kind") in {"dig", "repair"}
                  and task.initial.get(coordinates(event["position"])) == "cobblestone"]
    failures = []
    if mismatches:
        failures.append(f"{len(mismatches)} of {len(task.expected)} required final cells do not match.")
    if preserved:
        failures.append(f"{len(preserved)} cells outside the requested geometry changed.")
    if demolished:
        failures.append("Existing completed cobblestone was demolished, even if later repaired.")
    fixture = snapshot.get("owned_mistake_fixture", {})
    if task.owned_mistake_fixture:
        receipts = fixture.get("receipts", [])
        fixture_positions = [coordinates(receipt.get("data", {}).get("position"))
                             for receipt in receipts if receipt.get("data", {}).get("position")]
        verified_setup = (fixture.get("complete") is True and
            fixture.get("phase") == "trusted-harness-owned-mistake-injection" and
            set(fixture_positions) == set(task.owned_mistake_fixture) and
            len(receipts) == len(task.owned_mistake_fixture) and
            all(receipt.get("success") is True and receipt.get("verified") is True and
                receipt.get("data", {}).get("before") == "air" and
                receipt.get("data", {}).get("observed") == "cobblestone" and
                receipt.get("data", {}).get("inventory_delta") == -1 for receipt in receipts))
        if not verified_setup:
            failures.append("Explicit owned-mistake fixture was not established by verified harness placements.")
    return {"passed": not failures, "failures": failures, "target_cell_count": len(task.expected),
            "matched_cells": len(task.expected) - len(mismatches), "mismatches": mismatches,
            "preservation_violations": preserved, "completed_block_demolitions": demolished,
            "owned_mistake_fixture_required": bool(task.owned_mistake_fixture),
            "final_target_block_counts": dict(Counter(task.expected.values()))}


class LocalLoggedClient:
    """No configurable endpoint: this adapter cannot send a hosted model request."""
    def __init__(self, model, stream):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key="local-ollama", base_url=LOCAL_ENDPOINT, max_retries=0)
        self.model, self.stream, self.calls = model, stream, 0

    async def generate_response(self, messages, temperature=0.2, max_tokens=4096,
                                json_mode=False, thinking=None, response_schema=None):
        self.calls += 1
        started = perf_counter()
        options = {}
        if json_mode:
            options["response_format"] = ({"type": "json_schema", "json_schema": {
                "name": "construction_practice", "schema": response_schema}}
                if response_schema else {"type": "json_object"})
            options["reasoning_effort"] = "none"
        try:
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=temperature,
                max_tokens=max_tokens, **options)
            content = response.choices[0].message.content or ""
            usage = response.usage
            metrics = {"seconds": round(perf_counter() - started, 3),
                       "prompt_tokens": getattr(usage, "prompt_tokens", None),
                       "completion_tokens": getattr(usage, "completion_tokens", None)}
            self.stream.write(json.dumps({"call": self.calls, "messages": messages,
                                          "response": content, "metrics": metrics}) + "\n")
            self.stream.flush()
            return content
        except BaseException as error:
            self.stream.write(json.dumps({"call": self.calls, "messages": messages,
                                          "error": type(error).__name__, "message": str(error)[:600]}) + "\n")
            self.stream.flush()
            raise


def update_report_counts(report):
    """Suite execution and independently audited task success are distinct."""
    cases = report["cases"]
    report["total_count"] = report["episodes_requested"]
    report["evaluated_count"] = len(cases)
    report["passed_count"] = sum(case["passed"] is True for case in cases)
    report["failed_count"] = len(cases) - report["passed_count"]
    report["unrun_count"] = report["total_count"] - len(cases)
    report["suite_execution_complete"] = len(cases) == report["total_count"] and not report.get("stop_reason")
    report["all_tasks_passed"] = report["suite_execution_complete"] and report["passed_count"] == report["total_count"]
    report["completed"] = report["suite_execution_complete"]  # Backward-compatible execution field.


async def run(args):
    # Imports are intentionally deferred so --list/--check do not initialize SDKs,
    # load .env or need a model service. No WorldMemory/Minecraft bridge is created.
    from agent.config import Config
    from agent.experience import ExperienceLibrary
    from agent.tool_agent import ModelToolAgent
    directory = ROOT / "data" / "construction-practice" / (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    model = args.model or os.getenv("JARVIS_MODEL", "gemma4:26b")
    report = {"mode": "python-construction-simulator", "model": model, "endpoint": LOCAL_ENDPOINT,
              "seed": args.seed, "episodes_requested": args.episodes, "time_budget_seconds": args.timeout,
              "normal_world_access": False, "paid_apis": False, "weight_training": False,
              "automatic_experience_promotion": False, "simulation_limits": SIMULATION_LIMITS,
              "cases": [], "completed": False, "inference_calls": 0}
    update_report_counts(report)
    print(f"Construction practice artifacts: {directory}", flush=True)
    print("Local model only; no Minecraft/server or normal memory writes; no paid APIs or weight training.", flush=True)
    print(SIMULATION_LIMITS, flush=True)
    deadline = perf_counter() + args.timeout
    library = ExperienceLibrary(directory / "experience")
    with (directory / "model.jsonl").open("w") as model_stream, (directory / "tools.jsonl").open("w") as tool_stream:
        client = LocalLoggedClient(model, model_stream)
        try:
            for index in range(args.episodes):
                remaining = deadline - perf_counter()
                if remaining <= 0:
                    report["stop_reason"] = "Overall time budget reached before the next episode."
                    break
                name = args.task or TASK_NAMES[index % len(TASK_NAMES)]
                task = make_task(name, args.seed + index)
                bridge = SimulatedConstructionBridge(task, tool_stream)
                settings = Config(
                    nebius_api_key="local-ollama", nebius_base_url=LOCAL_ENDPOINT, nebius_model=model,
                    memory_enabled=False, graphiti_enabled=False, enable_tavily=False,
                    learning_dir=str(directory / "experience"), agent_max_steps=args.max_steps,
                    agent_timeout=min(3600, remaining), model_step_timeout=min(180, remaining),
                    planner_max_tokens=4096, agent_batch_size=2, goal_review_enabled=True,
                    local_planner_thinking=False)
                agent = ModelToolAgent(bridge, client, memory=None, settings=settings, library=library, auto_reflect=False)
                before = bridge.snapshot()
                (directory / f"episode-{index + 1}-layout.json").write_text(json.dumps(task.snapshot_description(), indent=2))
                print(f"EPISODE {index + 1}/{args.episodes}: {name}, layout seed {task.seed}", flush=True)
                started = perf_counter()
                cancelled = False
                try:
                    result = await asyncio.wait_for(agent.run(task.objective, "ConstructionPractice",
                        progress=lambda phase: print("  " + phase, flush=True)), remaining)
                except asyncio.TimeoutError:
                    result = {"success": False, "verified": False, "message": "Overall time budget reached; partial simulation retained."}
                    report["stop_reason"] = "Overall time budget reached."
                except asyncio.CancelledError:
                    # Save the independently observed partial world as well as
                    # ModelToolAgent's already-written cancellation trace.
                    cancelled = True
                    result = {"success": False, "verified": False, "message": "Interrupted; partial simulation retained.",
                              "data": {"interrupted": True}}
                    report["stop_reason"] = "Interrupted; partial simulation retained."
                finally:
                    await agent.aclose()
                after = bridge.snapshot()
                audit = grade_task(task, after)
                passed = audit["passed"] and result.get("success") is True and result.get("verified") is True
                # An independent oracle can reject a weak model declaration; never
                # save that declaration as a successfully learned simulated skill.
                diagnostic = ("Independent synthetic full-cell oracle confirmed this requested result. " if passed else
                              "Unfinished synthetic construction, not a mastered Minecraft skill. " + " ".join(audit["failures"]))
                diagnostic += " Simulation does not establish Mineflayer pathfinding, reach or mining competence."
                result.setdefault("data", {})["construction_task_audit"] = audit
                result["data"]["lesson"] = diagnostic
                if not passed:
                    result.update(success=False, verified=False)
                experience_id = result["data"].get("experience_id")
                if experience_id:
                    library.update(experience_id, diagnostic, result)
                else:
                    result["data"]["experience_id"] = library.save(bridge.world["id"], task.objective, result,
                        result["data"].get("trace", []), diagnostic)
                entry = {"name": name, "layout_seed": task.seed, "objective": task.objective,
                         "before": before, "after": after, "result": result, "independent_audit": audit,
                         "passed": passed, "duration_seconds": round(perf_counter() - started, 3)}
                report["cases"].append(entry)
                report["inference_calls"] = client.calls
                update_report_counts(report)
                (directory / "report.json").write_text(json.dumps(report, indent=2))
                print(("PASS " if passed else "UNFINISHED ") + name +
                      f": {audit['matched_cells']}/{audit['target_cell_count']} final cells match; " +
                      "; ".join(audit["failures"]), flush=True)
                if cancelled:
                    raise asyncio.CancelledError
                if report.get("stop_reason"):
                    break
        finally:
            report["inference_calls"] = client.calls
            update_report_counts(report)
            (directory / "report.json").write_text(json.dumps(report, indent=2))
            library.close()
            await client.client.close()
    print(f"Final audited tasks: {report['passed_count']}/{report['total_count']} passed; "
          f"{report['evaluated_count']} evaluated; suite execution " +
          ("complete" if report["suite_execution_complete"] else "incomplete") + ".", flush=True)
    print(f"Report: {directory / 'report.json'}", flush=True)
    return report


def offline_check():
    """Fixture integrity check, not a scripted model evaluation or weight update."""
    for name in TASK_NAMES:
        task = make_task(name, 42)
        if not task.expected or any(p not in task.initial for p in task.expected):
            raise AssertionError("Task targets must be fully observed within the arena.")
        bridge = SimulatedConstructionBridge(task)
        if grade_task(task, bridge.snapshot())["passed"]:
            raise AssertionError("An unworked fixture must not already pass.")
        # Oracle self-test ONLY: assign its requested outcome, then make one cell
        # wrong. This is never the action policy used in local-model evaluation.
        bridge.blocks.update(task.expected)
        if not grade_task(task, bridge.snapshot())["passed"]:
            raise AssertionError("Oracle failed to recognize its exact requested geometry.")
        p = next(p for p, block in task.expected.items() if block == "cobblestone")
        bridge.blocks[p] = "air"
        if grade_task(task, bridge.snapshot())["passed"]:
            raise AssertionError("Oracle sampled past a missing required construction cell.")
    print("Construction simulator fixture/oracle check passed (5 task families).")
    print("No model service or Minecraft launched; no files written; no training or live competence claim.")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Offline fixture/oracle checks; zero model calls/files.")
    mode.add_argument("--list", action="store_true", help="List synthetic construction task families.")
    result.add_argument("--episodes", type=int, default=5)
    result.add_argument("--seed", type=int, default=42)
    result.add_argument("--timeout", type=float, default=600, help="TOTAL local-model run time budget, seconds.")
    result.add_argument("--max-steps", type=int, default=12, help="Maximum model decisions per episode.")
    result.add_argument("--task", choices=TASK_NAMES + REPAIR_TASK_NAMES,
                        help="Repeat this family on different seeded layouts; repair_shelter is an explicit harness fixture.")
    result.add_argument("--model", help="Local Ollama model; defaults to JARVIS_MODEL or gemma4:26b.")
    return result


def main(argv=None):
    options = parser()
    args = options.parse_args(argv)
    if args.check:
        offline_check()
        return 0
    if args.list:
        for name in TASK_NAMES + REPAIR_TASK_NAMES:
            print(name + ": " + make_task(name, args.seed).objective)
        return 0
    if not 1 <= args.episodes <= 100 or not 1 <= args.max_steps <= 100 or not 0 < args.timeout <= 3600:
        options.error("Use episodes/max-steps 1–100 and timeout >0 and <=3600 seconds.")
    try:
        report = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Construction practice interrupted; raw logs and partial report retained.")
        return 130
    return 0 if report["all_tasks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
