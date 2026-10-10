"""Bounded, declarative Minecraft world-region contracts.

This module deliberately understands geometry only.  It does not encode a
house, a build order, or any Mineflayer action.  The model supplies the
regions and the controller expands them into exact final-world cell facts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


MAX_REGIONS = 16
MAX_EXCEPTIONS = 64
MAX_CELLS = 2304
MAX_INSPECT_CELLS = 4096
MODES = frozenset({"solid", "perimeter_xz"})


class RegionError(ValueError):
    """A declarative region cannot be expanded into an unambiguous contract."""


def _position(value, label="position"):
    if not isinstance(value, Mapping) or not all(type(value.get(axis)) is int for axis in ("x", "y", "z")):
        raise RegionError(f"{label} needs integer x, y, z")
    return value["x"], value["y"], value["z"]


def position_dict(value):
    """Make a JSON-safe position from a coordinate tuple."""
    return {"x": value[0], "y": value[1], "z": value[2]}


def position_key(value):
    """Stable, JSON-object-friendly key for an integer block coordinate."""
    return f"{value[0]},{value[1]},{value[2]}"


def _identifier(value, label="block"):
    if not isinstance(value, str) or not value.strip():
        raise RegionError(f"{label} needs a non-empty block identifier")
    return value


def _region(region, index):
    if not isinstance(region, Mapping):
        raise RegionError(f"region {index} must be an object")
    allowed = {"min", "max", "block", "mode"}
    if set(region) - allowed or not {"min", "max", "block", "mode"} <= set(region):
        raise RegionError(f"region {index} must contain only min, max, block, mode")
    minimum = _position(region["min"], f"region {index}.min")
    maximum = _position(region["max"], f"region {index}.max")
    if any(low > high for low, high in zip(minimum, maximum)):
        raise RegionError(f"region {index} min must not exceed max")
    mode = region["mode"]
    if mode not in MODES:
        raise RegionError(f"region {index} mode must be one of {sorted(MODES)}")
    return minimum, maximum, _identifier(region["block"], f"region {index}.block"), mode


def _cell_count(minimum, maximum, mode):
    width = maximum[0] - minimum[0] + 1
    height = maximum[1] - minimum[1] + 1
    depth = maximum[2] - minimum[2] + 1
    if mode == "solid" or width <= 2 or depth <= 2:
        return width * height * depth
    # An x/z perimeter is present at every y layer.  The inner rectangle is
    # absent, so corners are counted once rather than once per side.
    return height * (width * depth - (width - 2) * (depth - 2))


def _positions(minimum, maximum, mode):
    for x in range(minimum[0], maximum[0] + 1):
        for y in range(minimum[1], maximum[1] + 1):
            for z in range(minimum[2], maximum[2] + 1):
                if mode == "solid" or x in {minimum[0], maximum[0]} or z in {minimum[2], maximum[2]}:
                    yield x, y, z


def compile_regions(regions, exceptions=()):
    """Return ``{(x, y, z): expected_block}`` for a bounded region contract.

    A same-block overlap is harmless and useful for composable geometry.  A
    different-block overlap is ambiguous before exceptions are considered, so
    it is rejected rather than letting declaration order choose a world fact.
    Exceptions are then explicit final-state overrides, but only for cells the
    regions actually cover (an interior of a perimeter is not covered).
    """
    if not isinstance(regions, list) or not 1 <= len(regions) <= MAX_REGIONS:
        raise RegionError(f"regions must contain 1–{MAX_REGIONS} entries")
    if exceptions is None:
        exceptions = []
    if not isinstance(exceptions, list) or len(exceptions) > MAX_EXCEPTIONS:
        raise RegionError(f"exceptions may contain at most {MAX_EXCEPTIONS} entries")

    cells = {}
    for index, raw_region in enumerate(regions):
        minimum, maximum, block, mode = _region(raw_region, index)
        count = _cell_count(minimum, maximum, mode)
        if count > MAX_CELLS:
            raise RegionError(f"region {index} covers {count} cells; a regions_match goal is limited to {MAX_CELLS}")
        for coordinate in _positions(minimum, maximum, mode):
            prior = cells.get(coordinate)
            if prior is not None and prior != block:
                raise RegionError(
                    f"conflicting non-exception region overlap at {position_dict(coordinate)}: {prior} versus {block}"
                )
            cells[coordinate] = block
            if len(cells) > MAX_CELLS:
                raise RegionError(f"regions_match compiles to more than {MAX_CELLS} unique cells")

    seen_exceptions = set()
    for index, exception in enumerate(exceptions):
        if not isinstance(exception, Mapping) or set(exception) != {"position", "block"}:
            raise RegionError(f"exception {index} must contain only position and block")
        coordinate = _position(exception["position"], f"exception {index}.position")
        if coordinate in seen_exceptions:
            raise RegionError(f"duplicate exception at {position_dict(coordinate)}")
        seen_exceptions.add(coordinate)
        if coordinate not in cells:
            raise RegionError(f"exception {index} is outside the covered region at {position_dict(coordinate)}")
        cells[coordinate] = _identifier(exception["block"], f"exception {index}.block")

    # Sorted output makes tests, persisted evidence and prompt summaries
    # stable without imposing a build order on the model.
    return dict(sorted(cells.items()))


def compile_goal(goal):
    """Expand a raw ``regions_match`` goal after structural validation."""
    if not isinstance(goal, Mapping) or goal.get("kind") != "regions_match":
        raise RegionError("expected a regions_match goal")
    allowed = {"kind", "regions", "exceptions", "must_change"}
    if set(goal) - allowed or "regions" not in goal:
        raise RegionError("regions_match accepts only kind, regions, exceptions, must_change")
    if "must_change" in goal and type(goal["must_change"]) is not bool:
        raise RegionError("must_change must be Boolean for regions_match")
    return compile_regions(goal["regions"], goal.get("exceptions", []))


def inspect_bounds(cells: Mapping[tuple[int, int, int], str] | Iterable[tuple[int, int, int]]):
    """Partition exact target cells into full rectangular read-only surveys.

    Every emitted inclusive box is entirely made of expected cells.  This
    allows the Node ``inspect_region`` tool to prove each required cell without
    treating unrequested neighboring terrain as part of the contract.  The
    caller's 2304-cell goal cap keeps each greedy box below its 4096-cell RPC
    cap, and the explicit check protects this invariant if the constants move.
    """
    remaining = set(cells.keys() if isinstance(cells, Mapping) else cells)
    bounds = []
    while remaining:
        start = min(remaining)
        x0, y0, z0 = start
        x1 = x0
        while (x1 + 1, y0, z0) in remaining:
            x1 += 1
        z1 = z0
        while all((x, y0, z1 + 1) in remaining for x in range(x0, x1 + 1)):
            z1 += 1
        y1 = y0
        while all((x, y1 + 1, z) in remaining
                  for x in range(x0, x1 + 1)
                  for z in range(z0, z1 + 1)):
            y1 += 1
        volume = (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)
        if volume > MAX_INSPECT_CELLS:
            raise RegionError(f"inspection partition exceeds {MAX_INSPECT_CELLS} cells")
        box = {(x, y, z)
               for x in range(x0, x1 + 1)
               for y in range(y0, y1 + 1)
               for z in range(z0, z1 + 1)}
        # The expansion checks above make this true; retain an explicit guard
        # so a future partition optimization cannot survey undeclared cells.
        if not box <= remaining:
            raise RegionError("inspection partition contains an undeclared cell")
        remaining.difference_update(box)
        bounds.append({"min": position_dict((x0, y0, z0)), "max": position_dict((x1, y1, z1))})
    return bounds


def expected_block_counts(cells):
    """Count final non-air and air facts without exposing every coordinate."""
    counts = {}
    for block in cells.values():
        counts[block] = counts.get(block, 0) + 1
    return dict(sorted(counts.items()))
