"""Tests for the narrow operator construction-boundary proxy."""

from __future__ import annotations

import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent.construction_scope import ConstructionScopeError, ScopedConstructionBridge


SCOPE = {
    "min": {"x": -4, "y": 70, "z": -16},
    "max": {"x": 4, "y": 78, "z": -7},
}


class ScopedConstructionBridgeTests(unittest.IsolatedAsyncioTestCase):
    def make_bridge(self):
        delegate = SimpleNamespace(
            execute_tool=AsyncMock(return_value={"success": True, "verified": True, "data": {"sent": True}}),
            get_state=AsyncMock(return_value={"ready": True}),
            cancel_task=AsyncMock(),
            latest_state={"ready": True},
        )
        return ScopedConstructionBridge(delegate, SCOPE), delegate

    async def test_signed_negative_z_is_inclusive_and_positive_z_is_refused(self):
        bridge, delegate = self.make_bridge()
        allowed = {"name": "place", "args": {"item": "cobblestone", "position": {"x": 4, "y": 78, "z": -7}}}
        result = await bridge.execute_tool(allowed)
        self.assertTrue(result["success"])
        delegate.execute_tool.assert_awaited_once_with(allowed)

        outside = {"name": "dig", "args": {"position": {"x": 0, "y": 72, "z": 7}}}
        denied = await bridge.execute_tool(outside)
        self.assertFalse(denied["success"])
        self.assertFalse(denied["verified"])
        self.assertEqual(denied["data"]["error_code"], "OUTSIDE_CONSTRUCTION_SCOPE")
        self.assertEqual(denied["data"]["offending_position"], outside["args"]["position"])
        self.assertEqual(denied["data"]["scope"], SCOPE)
        self.assertEqual(delegate.execute_tool.await_count, 1)

    async def test_repair_batches_cannot_bypass_operator_scope(self):
        bridge, delegate = self.make_bridge()
        tool = {'name': 'repair_batch', 'args': {'positions': [
            {'x': 0, 'y': 72, 'z': -8}, {'x': 0, 'y': 72, 'z': 8}]}}
        result = await bridge.execute_tool(tool)
        self.assertEqual(result['data']['error_code'], 'OUTSIDE_CONSTRUCTION_SCOPE')
        delegate.execute_tool.assert_not_awaited()

    async def test_batch_is_preflighted_as_a_whole_before_any_delegate_call(self):
        bridge, delegate = self.make_bridge()
        tool = {
            "name": "place_batch",
            "args": {
                "item": "cobblestone",
                "positions": [
                    {"x": -4, "y": 70, "z": -16},
                    {"x": 0, "y": 72, "z": -10},
                    {"x": 5, "y": 72, "z": -10},
                ],
            },
        }
        original = copy.deepcopy(tool)
        denied = await bridge.execute_tool(tool)
        self.assertEqual(tool, original, "The scope boundary must not rewrite model arguments.")
        self.assertEqual(denied["data"]["offending_position"], original["args"]["positions"][2])
        delegate.execute_tool.assert_not_awaited()

    async def test_missing_or_noninteger_guarded_positions_are_refused_without_forwarding(self):
        bridge, delegate = self.make_bridge()
        cases = [
            {"name": "place", "args": {"item": "cobblestone"}},
            {"name": "dig", "args": {"position": {"x": 0, "y": 72, "z": "-10"}}},
            {"name": "use_on_block", "args": {"item": "iron_hoe", "position": {"x": 0, "y": 72, "z": 7}}},
            {"name": "dig_batch", "args": {"positions": [{"x": 0, "y": 72, "z": -10}, {"x": 1, "y": True, "z": -10}]}},
            {"name": "place_batch", "args": {"item": "cobblestone", "positions": []}},
        ]
        for tool in cases:
            with self.subTest(tool=tool):
                original = copy.deepcopy(tool)
                denied = await bridge.execute_tool(tool)
                self.assertEqual(denied["data"]["error_code"], "OUTSIDE_CONSTRUCTION_SCOPE")
                self.assertEqual(tool, original)
        delegate.execute_tool.assert_not_awaited()

    async def test_allowed_construction_and_unscoped_gathering_or_read_only_tools_forward(self):
        bridge, delegate = self.make_bridge()
        allowed = [
            {"name": "dig", "args": {"position": {"x": -4, "y": 70, "z": -16}}},
            {"name": "place_batch", "args": {"item": "cobblestone", "positions": [{"x": 0, "y": 72, "z": -10}]}},
            # Quarry gathering and surveys are intentionally not construction-scoped.
            {"name": "mine_resource", "args": {"item": "cobblestone", "count": 32, "max_distance": 64}},
            {"name": "inspect_region", "args": {"min": {"x": 100, "y": 64, "z": 100}, "max": {"x": 101, "y": 64, "z": 101}}},
        ]
        for tool in allowed:
            with self.subTest(name=tool["name"]):
                self.assertTrue((await bridge.execute_tool(tool))["success"])
        self.assertEqual(delegate.execute_tool.await_args_list[0].args, (allowed[0],))
        self.assertEqual(delegate.execute_tool.await_args_list[1].args, (allowed[1],))
        self.assertEqual(delegate.execute_tool.await_args_list[2].args, (allowed[2],))
        self.assertEqual(delegate.execute_tool.await_args_list[3].args, (allowed[3],))

    async def test_bridge_attributes_and_cancellation_delegate_transparently(self):
        bridge, delegate = self.make_bridge()
        self.assertIs(bridge.latest_state, delegate.latest_state)
        self.assertEqual(await bridge.get_state(), {"ready": True})
        await bridge.cancel_task()
        delegate.get_state.assert_awaited_once_with()
        delegate.cancel_task.assert_awaited_once_with()


class ConstructionScopeValidationTests(unittest.TestCase):
    def test_scope_is_required_and_strictly_bounded(self):
        delegate = SimpleNamespace(execute_tool=AsyncMock())
        with self.assertRaises(TypeError):
            ScopedConstructionBridge(delegate)  # type: ignore[call-arg]
        invalid_scopes = [
            None,
            {"min": {"x": 0, "y": 0, "z": 0}, "max": {"x": -1, "y": 0, "z": 0}},
            {"min": {"x": 0, "y": 0, "z": 0}, "max": {"x": 64, "y": 0, "z": 0}},
            {"min": {"x": 0, "y": 0, "z": 0}, "max": {"x": 1, "y": 0, "z": True}},
            {"min": {"x": 0, "y": 0, "z": 0}, "max": {"x": 1, "y": 0, "z": 1}, "label": "house"},
        ]
        for scope in invalid_scopes:
            with self.subTest(scope=scope):
                with self.assertRaises(ConstructionScopeError):
                    ScopedConstructionBridge(delegate, scope)

    def test_scope_property_does_not_allow_callers_to_mutate_the_boundary(self):
        bridge = ScopedConstructionBridge(SimpleNamespace(execute_tool=AsyncMock()), SCOPE)
        exposed = bridge.scope
        exposed["min"]["z"] = 999
        self.assertEqual(bridge.scope, SCOPE)


if __name__ == "__main__":
    unittest.main()
