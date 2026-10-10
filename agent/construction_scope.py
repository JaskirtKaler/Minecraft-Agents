"""Operator-supplied coordinate guard for construction actions.

This wrapper is deliberately a permission boundary, not a planner or goal
verifier.  It only prevents construction-site world mutations outside one
explicit construction footprint.  Discovery, navigation, and resource
gathering keep using the underlying bridge unchanged.
"""

from __future__ import annotations

from copy import deepcopy
import inspect
from typing import Any, Mapping


class ConstructionScopeError(ValueError):
    """The operator supplied an ambiguous or unsafe construction footprint."""


class ScopedConstructionBridge:
    """Proxy a bridge while limiting only explicit construction coordinates.

    ``scope`` is an inclusive, axis-aligned ``{"min": ..., "max": ...}``
    footprint supplied by the operator.  Its maximum span is intentionally
    small so a mistaken scope cannot silently authorize a large area.
    """

    MAX_AXIS_SPAN = 64
    # ``use_on_block`` can change a world block (for example a hoe turning
    # dirt into farmland), so it belongs with direct site mutations.
    _GUARDED_SINGLE = frozenset({"place", "dig", "use_on_block"})
    _GUARDED_BATCH = frozenset({"place_batch", "dig_batch", "repair_batch"})
    _AXES = ("x", "y", "z")
    _NO_VIOLATION = object()

    def __init__(self, delegate: Any, scope: Mapping[str, Any]):
        self._delegate = delegate
        self._scope = self._validate_scope(scope)

    @property
    def scope(self) -> dict[str, dict[str, int]]:
        """A copy of the validated operator footprint, safe for diagnostics."""
        return self._scope_copy()

    def __getattr__(self, name: str) -> Any:
        """Keep the rest of the bridge surface transparent to callers."""
        return getattr(self._delegate, name)

    async def execute_tool(self, tool: Any, *args: Any, **kwargs: Any) -> Any:
        """Preflight guarded actions before the delegate can send them to a bot."""
        violation = self._violation(tool)
        if violation is not self._NO_VIOLATION:
            return self._outside_scope_result(violation)

        outcome = self._delegate.execute_tool(tool, *args, **kwargs)
        if inspect.isawaitable(outcome):
            return await outcome
        return outcome

    @classmethod
    def _validate_scope(cls, scope: Mapping[str, Any]) -> dict[str, dict[str, int]]:
        if not isinstance(scope, Mapping):
            raise ConstructionScopeError("Construction scope must be an object with min and max bounds.")
        if set(scope) != {"min", "max"}:
            raise ConstructionScopeError("Construction scope must contain exactly min and max bounds.")

        normalized: dict[str, dict[str, int]] = {}
        for edge in ("min", "max"):
            bound = scope.get(edge)
            if not isinstance(bound, Mapping) or set(bound) != set(cls._AXES):
                raise ConstructionScopeError(f"Construction scope {edge} needs exactly integer x, y, z bounds.")
            normalized[edge] = {}
            for axis in cls._AXES:
                value = bound.get(axis)
                if type(value) is not int:
                    raise ConstructionScopeError(f"Construction scope {edge}.{axis} must be an integer.")
                normalized[edge][axis] = value

        for axis in cls._AXES:
            lower, upper = normalized["min"][axis], normalized["max"][axis]
            if lower > upper:
                raise ConstructionScopeError(f"Construction scope min.{axis} cannot exceed max.{axis}.")
            if upper - lower + 1 > cls.MAX_AXIS_SPAN:
                raise ConstructionScopeError(
                    f"Construction scope {axis} span exceeds {cls.MAX_AXIS_SPAN} inclusive blocks."
                )
        return normalized

    def _scope_copy(self) -> dict[str, dict[str, int]]:
        return {edge: dict(bounds) for edge, bounds in self._scope.items()}

    def _violation(self, tool: Any) -> Any:
        """Return the first unsafe raw position, or a private sentinel when safe."""
        if not isinstance(tool, Mapping):
            return self._NO_VIOLATION
        name = tool.get("name")
        if name in self._GUARDED_SINGLE:
            args = tool.get("args")
            position = args.get("position") if isinstance(args, Mapping) else None
            return self._NO_VIOLATION if self._contains(position) else position
        if name in self._GUARDED_BATCH:
            args = tool.get("args")
            positions = args.get("positions") if isinstance(args, Mapping) else None
            if not isinstance(positions, list) or not positions:
                return positions
            for position in positions:
                if not self._contains(position):
                    return position
        return self._NO_VIOLATION

    def _contains(self, position: Any) -> bool:
        if not isinstance(position, Mapping):
            return False
        for axis in self._AXES:
            value = position.get(axis)
            if type(value) is not int:
                return False
            if not self._scope["min"][axis] <= value <= self._scope["max"][axis]:
                return False
        return True

    def _outside_scope_result(self, position: Any) -> dict[str, Any]:
        # Copy diagnostic data rather than exposing either caller-owned action
        # objects or the wrapper's stored permission boundary.
        offending = deepcopy(position)
        return {
            "success": False,
            "verified": False,
            "status": "failed",
            "message": "Construction action is outside the operator-supplied construction scope.",
            "data": {
                "error_code": "OUTSIDE_CONSTRUCTION_SCOPE",
                "offending_position": offending,
                "position": deepcopy(offending),
                "scope": self._scope_copy(),
            },
        }
