"""Offline unit/oracle checks; these are NOT an LLM construction benchmark."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from practice.construction import (
    LOCAL_ENDPOINT, TASK_NAMES, LocalLoggedClient, SimulatedConstructionBridge, cell_key, coordinates,
    grade_task, main, make_task, position, run, update_report_counts,
)


class TaskOracleTests(unittest.TestCase):
    def test_seeded_layouts_vary_and_negative_z_is_retained(self):
        one, again, other = [make_task("negative_floor", seed) for seed in (42, 42, 43)]
        self.assertEqual(one.snapshot_description(), again.snapshot_description())
        self.assertNotEqual(one.snapshot_description(), other.snapshot_description())
        self.assertLess(one.maximum[2], 0)
        self.assertIn(f"Z={one.minimum[2]}", one.objective)

    def test_every_family_has_independent_complete_cell_oracle(self):
        for name in TASK_NAMES:
            with self.subTest(name=name):
                task = make_task(name, 99)
                bridge = SimulatedConstructionBridge(task)
                self.assertFalse(grade_task(task, bridge.snapshot())["passed"])
                bridge.blocks.update(task.expected)
                self.assertTrue(grade_task(task, bridge.snapshot())["passed"])
                for p in task.expected:
                    expected = bridge.blocks[p]
                    bridge.blocks[p] = "air" if expected != "air" else "cobblestone"
                    audit = grade_task(task, bridge.snapshot())
                    self.assertFalse(audit["passed"])
                    self.assertEqual(len(audit["mismatches"]), 1)
                    bridge.blocks[p] = expected

    def test_final_geometry_does_not_excuse_fixture_or_ground_damage(self):
        task = make_task("shelter", 17)
        bridge = SimulatedConstructionBridge(task)
        bridge.blocks.update(task.expected)
        chest = coordinates(task.fixtures[0]["position"])
        bridge.blocks[chest] = "air"
        audit = grade_task(task, bridge.snapshot())
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["preservation_violations"][0]["position"], position(chest))

    def test_repaired_existing_row_demolition_is_not_accepted(self):
        task = make_task("partial_row", 5)
        bridge = SimulatedConstructionBridge(task)
        bridge.blocks.update(task.expected)
        p = next(p for p, block in task.initial.items() if block == "cobblestone")
        bridge.events.append({"kind": "dig", "position": position(p), "before": "cobblestone", "after": "air"})
        self.assertFalse(grade_task(task, bridge.snapshot())["passed"])

    def test_shelter_contains_floor_walls_roof_door_and_air_interior(self):
        task = make_task("shelter", 3)
        floor = task.minimum[1]
        self.assertTrue(any(p[1] == floor + 3 and b == "cobblestone" for p, b in task.expected.items()))
        door = [p for p, b in task.expected.items() if b == "air" and p[2] == task.maximum[2]]
        self.assertEqual(len(door), 2)
        self.assertTrue(any(b == "air" and p[2] != task.maximum[2] for p, b in task.expected.items()))

    def test_check_and_list_are_offline_and_do_not_start_eval(self):
        with patch("practice.construction.run", side_effect=AssertionError("No inference!")), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--check"]), 0)
            self.assertEqual(main(["--list"]), 0)
        self.assertIn("No model service or Minecraft launched", output.getvalue())
        self.assertIn("partial_row", output.getvalue())

    def test_cli_failure_exit_code_is_not_a_success_claim(self):
        with patch("practice.construction.run", new=AsyncMock(return_value={"all_tasks_passed": True})):
            self.assertEqual(main(["--episodes", "1"]), 0)
        with patch("practice.construction.run", new=AsyncMock(return_value={"all_tasks_passed": False})):
            self.assertEqual(main(["--episodes", "1"]), 1)

    def test_report_distinguishes_execution_from_task_success(self):
        report = {"episodes_requested": 2, "cases": [{"passed": True}, {"passed": False}]}
        update_report_counts(report)
        self.assertTrue(report["suite_execution_complete"])
        self.assertFalse(report["all_tasks_passed"])
        self.assertEqual((report["passed_count"], report["total_count"], report["failed_count"]), (1, 2, 1))
        report["cases"].pop()
        update_report_counts(report)
        self.assertFalse(report["suite_execution_complete"])
        self.assertEqual(report["unrun_count"], 1)
        report["cases"].append({"passed": True})
        report["stop_reason"] = "Interrupted"
        update_report_counts(report)
        self.assertFalse(report["suite_execution_complete"])
        self.assertFalse(report["all_tasks_passed"])

    def test_hosted_endpoint_or_hosted_permission_cli_is_not_supported(self):
        with redirect_stdout(io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            for arguments in (["--base-url", "https://api.nebius.ai/v1"], ["--allow-hosted"]):
                with self.assertRaises(SystemExit) as error:
                    main(arguments)
                self.assertEqual(error.exception.code, 2)
        with patch.dict(os.environ, {"NEBIUS_BASE_URL": "https://api.nebius.ai/v1", "NEBIUS_API_KEY": "not-used"}), \
                patch("openai.AsyncOpenAI") as sdk:
            LocalLoggedClient("fake-model", io.StringIO())
            sdk.assert_called_once_with(api_key="local-ollama", base_url=LOCAL_ENDPOINT, max_retries=0)


class ConstructionBridgeTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, name="negative_floor", seed=42):
        task = make_task(name, seed)
        return task, SimulatedConstructionBridge(task)

    async def test_target_is_air_cell_above_ground_not_support(self):
        task, bridge = self.fixture()
        p = next(p for p, b in task.expected.items() if b == "cobblestone")
        before = bridge.inventory["cobblestone"]
        outcome = await bridge.execute_tool({"name": "place", "args": {"item": "cobblestone", "position": position(p)}})
        self.assertTrue(outcome["verified"])
        self.assertEqual(bridge.blocks[p], "cobblestone")
        self.assertEqual(bridge.blocks[(p[0], p[1] - 1, p[2])], "grass_block")
        self.assertEqual(bridge.inventory["cobblestone"], before - 1)

    async def test_unknown_is_explicit_and_cannot_be_support(self):
        task, bridge = self.fixture()
        unknown = (100, task.minimum[1], 100)
        result = await bridge.execute_tool({"name": "inspect_region", "args": {"min": position(unknown), "max": position(unknown)}})
        self.assertEqual(result["data"]["cells"][0]["name"], "unknown")
        self.assertFalse(result["data"]["cells"][0]["loaded"])
        p = next(iter(task.expected))
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            bridge.blocks.pop((p[0] + dx, p[1] + dy, p[2] + dz), None)
        result = await bridge.execute_tool({"name": "place", "args": {"item": "cobblestone", "position": position(p)}})
        self.assertEqual(result["data"]["error_code"], "NO_SUPPORT")
        self.assertEqual(bridge.blocks[p], "air")

    async def test_sign_flip_rejects_entire_batch_before_mutation(self):
        task, bridge = self.fixture()
        first = next(iter(task.expected))
        flipped = (first[0], first[1], -first[2])
        before = bridge.snapshot()
        result = await bridge.execute_tool({"name": "place_batch", "args": {
            "item": "cobblestone", "positions": [position(first), position(flipped)]}})
        self.assertEqual(result["data"]["error_code"], "OUTSIDE_CONSTRUCTION_SCOPE")
        self.assertEqual(bridge.snapshot(), before)

    async def test_ground_fixtures_and_finished_cells_cannot_be_demolished(self):
        task, bridge = self.fixture("partial_row")
        ground = (task.minimum[0], task.minimum[1] - 1, task.minimum[2])
        chest = coordinates(task.fixtures[0]["position"])
        existing = next(p for p, b in task.initial.items() if b == "cobblestone")
        for p in (ground, chest, existing):
            original = bridge.blocks[p]
            result = await bridge.execute_tool({"name": "dig", "args": {"position": position(p)}})
            self.assertFalse(result["success"])
            self.assertEqual(bridge.blocks[p], original)

    async def test_completed_row_reuses_blocks_without_spending_more_inventory(self):
        task, bridge = self.fixture("partial_row")
        existing = [p for p, b in task.initial.items() if b == "cobblestone"]
        before = bridge.inventory["cobblestone"]
        result = await bridge.execute_tool({"name": "place_batch", "args": {
            "item": "cobblestone", "positions": [position(p) for p in existing]}})
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["placed_count"], 0)
        self.assertEqual(bridge.inventory["cobblestone"], before)

    async def test_obstacle_requires_observation_and_explicit_clearing(self):
        task, bridge = self.fixture("obstacle_floor")
        p = (task.minimum[0], task.minimum[1], task.minimum[2])
        failed = await bridge.execute_tool({"name": "place", "args": {"item": "cobblestone", "position": position(p)}})
        # Top up finite synthetic supplies to isolate the actual occupancy check.
        await bridge.execute_tool({"name": "mine_resource", "args": {"item": "cobblestone", "count": 8}})
        failed = await bridge.execute_tool({"name": "place", "args": {"item": "cobblestone", "position": position(p)}})
        self.assertEqual(failed["data"]["error_code"], "TARGET_OCCUPIED")
        cleared = await bridge.execute_tool({"name": "dig", "args": {"position": position(p)}})
        self.assertTrue(cleared["success"])
        result = await bridge.execute_tool({"name": "place", "args": {"item": "cobblestone", "position": position(p)}})
        self.assertTrue(result["success"])

    async def test_support_placed_earlier_in_batch_is_usable(self):
        task, bridge = self.fixture("shelter")
        await bridge.execute_tool({"name": "mine_resource", "args": {"item": "cobblestone", "count": 8}})
        floor = task.minimum
        wall = (floor[0], floor[1] + 1, floor[2])
        before = bridge.inventory["cobblestone"]
        result = await bridge.execute_tool({"name": "place_batch", "args": {
            "item": "cobblestone", "positions": [position(floor), position(wall)]}})
        self.assertTrue(result["success"])
        self.assertEqual(bridge.inventory["cobblestone"], before - 2)

    async def test_finite_acquisition_stub_has_total_vs_additional_semantics(self):
        task, bridge = self.fixture("footing_floor")
        before = bridge.inventory["cobblestone"]
        result = await bridge.execute_tool({"name": "mine_resource", "args": {
            "item": "cobblestone", "count": before + 3, "collection_mode": "ensure_inventory"}})
        self.assertEqual(result["data"]["delta"], 3)
        result = await bridge.execute_tool({"name": "mine_resource", "args": {
            "item": "cobblestone", "count": 3, "collection_mode": "additional"}})
        self.assertEqual(bridge.inventory["cobblestone"], before + 6)
        self.assertTrue(result["data"]["synthetic"])
        bridge.supply = 0
        result = await bridge.execute_tool({"name": "mine_resource", "args": {"item": "cobblestone", "count": 1}})
        self.assertFalse(result["success"])

    async def test_batch_inventory_preflight_and_partial_failure_receipts(self):
        task, bridge = self.fixture("negative_floor")
        p, q = list(task.expected)[:2]
        bridge.inventory["cobblestone"] = 1
        result = await bridge.execute_tool({"name": "place_batch", "args": {
            "item": "cobblestone", "positions": [position(p), position(q)]}})
        self.assertFalse(result["success"])
        self.assertEqual(bridge.blocks[p], "air")
        bridge.inventory["cobblestone"] = 2
        bridge.blocks[q] = "dirt"
        result = await bridge.execute_tool({"name": "place_batch", "args": {
            "item": "cobblestone", "positions": [position(p), position(q)]}})
        self.assertFalse(result["success"])
        self.assertEqual(bridge.blocks[p], "cobblestone")
        self.assertEqual(bridge.inventory["cobblestone"], 1)
        self.assertEqual(len(result["data"]["partial"]["receipts"]), 2)

    async def test_tool_log_retains_raw_calls_results_and_local_source(self):
        stream = io.StringIO()
        bridge = SimulatedConstructionBridge(make_task("negative_floor", 42), stream)
        p = next(iter(bridge.task.expected))
        tool = {"name": "inspect", "args": {"position": position(p)}}
        await bridge.execute_tool(tool)
        logged = json.loads(stream.getvalue())
        self.assertEqual(logged["tool"], tool)
        self.assertEqual(logged["result"]["data"]["name"], "air")
        self.assertIn("construction-sim", logged["world"]["id"])
        state = await bridge.get_state()
        self.assertEqual(state["constructionPractice"]["source"], "python-synthetic-simulator")
        self.assertEqual(LOCAL_ENDPOINT, "http://localhost:11434/v1")

    async def test_large_batch_compaction_preserves_every_placement_coordinate(self):
        from agent.tool_agent import compact
        task, bridge = self.fixture()
        targets = list(task.expected)
        self.assertGreater(len(targets), 8)
        action = {"name": "place_batch", "args": {"item": "cobblestone", "positions": [position(p) for p in targets]}}
        result = await bridge.execute_tool(action)
        self.assertEqual(len(result["data"]["receipts"]), len(targets))
        self.assertEqual(len(result["data"]["placements"]), len(targets))
        summary = compact({"action": action, "result": result})["result"]["data"]["placements"]
        self.assertEqual(summary["count"], len(targets))
        self.assertEqual([cell["position"] for cell in summary["cells"]], [position(p) for p in targets])
        self.assertTrue(all(cell["before"] == "air" and cell["observed"] == "cobblestone" and cell["verified"] for cell in summary["cells"]))

    async def test_partial_batch_compaction_preserves_success_skip_and_failure(self):
        from agent.tool_agent import compact
        task, bridge = self.fixture()
        targets = list(task.expected)[:12]
        bridge.blocks[targets[-1]] = "dirt"
        action = {"name": "place_batch", "args": {"item": "cobblestone", "positions": [position(p) for p in targets]}}
        result = await bridge.execute_tool(action)
        partial = result["data"]["partial"]
        self.assertEqual(len(partial["receipts"]), 12)
        self.assertEqual(len(partial["placements"]), 11)
        self.assertEqual(partial["failed"]["position"], position(targets[-1]))
        self.assertEqual(partial["skipped"][0]["observed"], "dirt")
        self.assertFalse(partial["skipped"][0]["verified"])
        summary = compact({"action": action, "result": result})["result"]["data"]["partial"]
        self.assertEqual(len(summary["placements"]["cells"]), 11)
        self.assertEqual(summary["skipped"]["cells"][0]["position"], position(targets[-1]))

    async def test_dig_batch_reports_cleared_and_already_air_cells(self):
        task, bridge = self.fixture("obstacle_floor")
        obstacles = [p for p in task.expected if bridge.blocks[p] != "air"]
        empty = next(p for p in task.expected if bridge.blocks[p] == "air")
        result = await bridge.execute_tool({"name": "dig_batch", "args": {"positions": [position(p) for p in obstacles + [empty]]}})
        data = result["data"]
        self.assertEqual(len(data["cleared"]), len(obstacles))
        self.assertEqual(data["skipped"][0]["reason"], "already_air")
        self.assertEqual(data["skipped"][0]["position"], position(empty))
        self.assertTrue(all(cell["after"] == "air" and cell["observed"] == "air" and cell["verified"] for cell in data["cleared"]))


class ControlledRepairTests(unittest.IsolatedAsyncioTestCase):
    """Provenance/permission fixtures, not model competence or an action policy."""
    async def register(self, bridge, desired=None, task_id="fixture-current-task"):
        return await bridge.set_construction_contract({
            "task_id": task_id, "world": dict(bridge.world),
            "desired_cells": {cell_key(p): block for p, block in
                              (bridge.task.expected if desired is None else desired).items()},
        })

    async def owned_mistake(self, seed=42):
        task = make_task("repair_shelter", seed)
        bridge = SimulatedConstructionBridge(task)
        ack = await self.register(bridge)
        self.assertTrue(ack["active"])
        return task, bridge, list(task.owned_mistake_fixture)

    async def test_verified_current_task_owned_mistakes_are_repaired_without_demolishing_completed_build(self):
        task, bridge, targets = await self.owned_mistake()
        before = bridge.snapshot()
        self.assertFalse(grade_task(task, before)["passed"])
        self.assertTrue(before["owned_mistake_fixture"]["complete"])
        self.assertTrue(all(bridge.blocks[p] == "cobblestone" and task.expected[p] == "air" for p in targets))
        self.assertTrue(all(event["source"] == "trusted-harness-owned-mistake-injection"
                            for event in bridge.events))
        status = await bridge.construction_status()
        self.assertEqual(status["repairable_count"], 2)
        result = await bridge.execute_tool({"name": "repair_batch", "args": {
            "positions": [position(p) for p in targets]}})
        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual(len(result["data"]["cleared"]), 2)
        self.assertEqual(len(result["data"]["receipts"]), 2)
        self.assertTrue(all(row["before"] == "cobblestone" and row["after"] == "air"
                            for row in result["data"]["cleared"]))
        self.assertEqual((await bridge.construction_status())["repairable_count"], 0)
        audit = grade_task(task, bridge.snapshot())
        self.assertTrue(audit["passed"], audit)
        self.assertFalse(audit["completed_block_demolitions"])
        self.assertTrue(all(bridge.blocks[p] == block for p, block in task.initial.items()
                            if p not in targets))

    async def test_inconsistent_placement_batch_is_rejected_before_any_cell_or_inventory_changes(self):
        task = make_task("shelter", 42)
        bridge = SimulatedConstructionBridge(task)
        await self.register(bridge)
        p = next(p for p, block in task.expected.items() if block == "cobblestone")
        q = next(p for p, block in task.expected.items() if block == "air")
        before = bridge.snapshot()
        result = await bridge.execute_tool({"name": "place_batch", "args": {
            "item": "cobblestone", "positions": [position(p), position(q)]}})
        self.assertEqual(result["data"]["error_code"], "CONSTRUCTION_PLACEMENT_MISMATCH")
        self.assertEqual(bridge.snapshot(), before)
        self.assertFalse(bridge._owned_placements)

    async def test_correct_owned_final_block_is_not_repairable(self):
        task = make_task("negative_floor", 42)
        bridge = SimulatedConstructionBridge(task)
        await self.register(bridge)
        p = next(iter(task.expected))
        await bridge.execute_tool({"name": "place", "args": {"item": "cobblestone", "position": position(p)}})
        before = bridge.snapshot()
        result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
        self.assertEqual(result["data"]["error_code"], "CONSTRUCTION_TARGET_ALREADY_CORRECT")
        self.assertEqual(bridge.snapshot(), before)

    async def test_preexisting_player_cobble_is_never_adopted_even_when_goals_want_air(self):
        task = make_task("partial_row", 42)
        bridge = SimulatedConstructionBridge(task)
        p = next(p for p, block in task.initial.items() if block == "cobblestone")
        await self.register(bridge, {p: "air"})
        before = bridge.snapshot()
        result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
        self.assertEqual(result["data"]["error_code"], "PROTECTED_BLOCK")
        self.assertEqual(bridge.snapshot(), before)
        self.assertFalse(bridge._owned_placements)

    async def test_no_contract_or_model_claims_cannot_authorize_repair(self):
        task = make_task("shelter", 42)
        bridge = SimulatedConstructionBridge(task)
        p = next(iter(task.expected))
        await bridge.execute_tool({"name": "place", "args": {"item": "cobblestone", "position": position(p)}})
        before = bridge.snapshot()
        result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
        self.assertEqual(result["data"]["error_code"], "NO_CONSTRUCTION_CONTRACT")
        spoofed = await bridge.execute_tool({"name": "repair_batch", "args": {
            "positions": [position(p)], "owned": True, "task_id": "model-claimed", "desired": "air"}})
        self.assertEqual(spoofed["data"]["error_code"], "INVALID_ARGUMENT")
        public_setter = await bridge.execute_tool({"name": "set_construction_contract", "args": {
            "task_id": "model-claimed", "desired_cells": {cell_key(p): "air"}}})
        self.assertEqual(public_setter["data"]["error_code"], "UNSUPPORTED_SIMULATION_TOOL")
        self.assertEqual(bridge.snapshot(), before)
        self.assertFalse(bridge._owned_placements)

    async def test_same_name_external_replacement_invalidates_receipt(self):
        task, bridge, targets = await self.owned_mistake()
        p = targets[0]
        bridge.blocks[p] = "air"
        bridge.blocks[p] = "cobblestone"
        before = bridge.snapshot()
        result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
        self.assertEqual(result["data"]["error_code"], "PLACEMENT_RECEIPT_CHANGED")
        self.assertEqual(bridge.snapshot(), before)

    async def test_full_property_fingerprint_changes_invalidate_receipt(self):
        task, bridge, targets = await self.owned_mistake()
        p = targets[0]
        bridge.block_properties[p] = {"waterlogged": True}
        before = bridge.snapshot()
        result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
        self.assertEqual(result["data"]["error_code"], "PLACEMENT_RECEIPT_CHANGED")
        self.assertEqual(bridge.snapshot(), before)
        self.assertEqual(bridge.descriptor(p)["properties"], {"waterlogged": True})

    async def test_property_aba_and_in_place_update_invalidate_receipts(self):
        for change in ("map_replacement", "in_place"):
            with self.subTest(change=change):
                task, bridge, targets = await self.owned_mistake()
                p = targets[0]
                if change == "map_replacement":
                    bridge.block_properties[p] = {"waterlogged": True}
                    bridge.block_properties[p] = {}
                else:
                    bridge.block_properties[p] = {}
                    bridge.block_properties[p]["waterlogged"] = True
                    bridge.block_properties[p].pop("waterlogged")
                self.assertEqual(bridge.descriptor(p)["properties"], {})
                before = bridge.snapshot()
                result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
                self.assertEqual(result["data"]["error_code"], "PLACEMENT_RECEIPT_CHANGED")
                self.assertEqual(bridge.snapshot(), before)

    async def test_current_task_contract_is_immutable_and_cannot_be_retargeted_for_demolition(self):
        task, bridge, targets = await self.owned_mistake()
        before = bridge.snapshot()
        changed = dict(task.expected)
        changed[targets[0]] = "cobblestone"
        with self.assertRaisesRegex(ValueError, "IMMUTABLE_CONSTRUCTION_CONTRACT"):
            await self.register(bridge, changed)
        self.assertEqual(bridge.snapshot(), before)
        self.assertEqual((await bridge.construction_status())["repairable_count"], 2)

    async def test_new_task_clear_or_changed_world_cannot_reuse_old_ownership(self):
        for change in ("new_task", "clear", "world_id", "session", "dimension"):
            with self.subTest(change=change):
                task, bridge, targets = await self.owned_mistake()
                p = targets[0]
                if change == "new_task":
                    await self.register(bridge, task_id="new-task")
                elif change == "clear":
                    await bridge.set_construction_contract(None)
                else:
                    key = {"world_id": "id", "session": "sessionId", "dimension": "dimension"}[change]
                    bridge.world[key] = "changed"
                before = bridge.snapshot()
                result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
                self.assertFalse(result["success"])
                self.assertIn(result["data"]["error_code"],
                              {"NOT_OWNED_PLACEMENT", "NO_CONSTRUCTION_CONTRACT", "CONSTRUCTION_WORLD_CHANGED"})
                self.assertEqual(bridge.snapshot(), before)

    async def test_outside_contract_unknown_or_unknown_neighbor_rejects_without_mutation(self):
        for change in ("outside", "unknown", "unknown_neighbor"):
            with self.subTest(change=change):
                task, bridge, targets = await self.owned_mistake()
                p = targets[0]
                if change == "outside":
                    # New task contains only a different requested cell, not p.
                    await self.register(bridge, {targets[1]: "air"}, task_id="smaller-new-task")
                elif change == "unknown":
                    bridge.blocks.pop(p)
                else:
                    bridge.blocks.pop((p[0] - 1, p[1], p[2]))
                before = bridge.snapshot()
                result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
                self.assertFalse(result["success"])
                self.assertEqual(result["data"]["error_code"],
                                 {"outside": "OUTSIDE_CONSTRUCTION_CONTRACT", "unknown": "UNLOADED_BLOCK",
                                  "unknown_neighbor": "UNKNOWN_REPAIR_NEIGHBOR"}[change])
                self.assertEqual(bridge.snapshot(), before)

    async def test_batch_rejects_all_before_mutation_if_one_receipt_is_stale(self):
        task, bridge, targets = await self.owned_mistake()
        bridge.blocks[targets[1]] = "cobblestone"  # A same-name external update is still a revision.
        before = bridge.snapshot()
        result = await bridge.execute_tool({"name": "repair_batch", "args": {
            "positions": [position(p) for p in targets]}})
        self.assertFalse(result["success"])
        self.assertEqual(result["data"]["error_code"], "PLACEMENT_RECEIPT_CHANGED")
        self.assertEqual(result["data"]["cleared"], [])
        self.assertEqual(bridge.snapshot(), before)

    async def test_mid_batch_change_keeps_partial_raw_receipts_and_observed_cleared_cells(self):
        from agent.tool_agent import compact
        task, bridge, targets = await self.owned_mistake()
        original = bridge.repair

        def external_change_after_first(p, tool=None):
            result = original(p, tool)
            if p == targets[0]:
                bridge.blocks[targets[1]] = "cobblestone"
            return result

        bridge.repair = external_change_after_first
        action = {"name": "repair_batch", "args": {"positions": [position(p) for p in targets]}}
        result = await bridge.execute_tool(action)
        self.assertFalse(result["success"])
        partial = result["data"]["partial"]
        self.assertEqual(len(partial["receipts"]), 2)
        self.assertEqual(partial["cleared"][0]["position"], position(targets[0]))
        self.assertEqual(partial["skipped"][0]["position"], position(targets[1]))
        self.assertEqual(partial["skipped"][0]["reason"], "PLACEMENT_RECEIPT_CHANGED")
        self.assertEqual(bridge.blocks[targets[0]], "air")
        self.assertEqual(bridge.blocks[targets[1]], "cobblestone")
        self.assertFalse(grade_task(task, bridge.snapshot())["passed"])
        compacted = compact({"action": action, "result": result})["result"]["data"]["partial"]
        self.assertEqual(compacted["cleared"]["cells"][0]["position"], position(targets[0]))

    async def test_underfoot_and_unsuitable_or_missing_pickaxe_are_rejected(self):
        for change in ("underfoot", "missing_tool", "bad_tool"):
            with self.subTest(change=change):
                task, bridge, targets = await self.owned_mistake()
                p = targets[0]
                args = {"positions": [position(p)]}
                if change == "underfoot":
                    bridge.bot_position = (p[0], p[1] + 1, p[2])
                elif change == "missing_tool":
                    bridge.inventory.pop("stone_pickaxe")
                else:
                    args["tool"] = "stone_axe"
                before = bridge.snapshot()
                result = await bridge.execute_tool({"name": "repair_batch", "args": args})
                self.assertEqual(result["data"]["error_code"],
                                 {"underfoot": "UNDERFOOT_MINING", "missing_tool": "MISSING_TOOL",
                                  "bad_tool": "UNSUITABLE_TOOL"}[change])
                self.assertEqual(bridge.snapshot(), before)

    async def test_liquid_falling_fixture_overburden_and_other_player_footing_reject(self):
        for change in ("liquid", "waterlogged_neighbor", "falling", "fixture_above", "player_footing"):
            with self.subTest(change=change):
                task, bridge, targets = await self.owned_mistake()
                p = targets[0]
                if change == "player_footing":
                    bridge.player_positions["synthetic-player"] = (p[0], p[1] + 1, p[2])
                else:
                    neighbor = (p[0], p[1] + 1, p[2]) if change == "fixture_above" else (p[0] - 1, p[1], p[2])
                    if change == "waterlogged_neighbor":
                        bridge.block_properties[neighbor] = {"waterlogged": "true"}
                    else:
                        bridge.blocks[neighbor] = {"liquid": "water", "falling": "gravel",
                                                   "fixture_above": "chest"}[change]
                before = bridge.snapshot()
                result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
                self.assertEqual(result["data"]["error_code"],
                    {"liquid": "LIQUID_ADJACENT", "waterlogged_neighbor": "LIQUID_ADJACENT",
                     "falling": "HAZARDOUS_ADJACENCY", "fixture_above": "UNSUPPORTED_OVERBURDEN",
                     "player_footing": "PLAYER_FOOTING"}[change])
                self.assertEqual(bridge.snapshot(), before)

    async def test_stable_structural_roof_can_remain_above_owned_repair(self):
        task, bridge, targets = await self.owned_mistake()
        p = targets[0]
        above = (p[0], p[1] + 1, p[2])
        bridge.blocks[above] = "cobblestone"
        result = await bridge.execute_tool({"name": "repair_batch", "args": {"positions": [position(p)]}})
        self.assertTrue(result["success"])
        self.assertEqual(bridge.blocks[above], "cobblestone")
        self.assertEqual(result["data"]["mode"], "own_placement_repair")
        self.assertEqual(result["data"]["cleared"][0]["desired_block"], "air")
        self.assertEqual(result["data"]["cleared"][0]["provenance"], "verified_current_task_placement")

    async def test_fixture_never_registered_or_only_assigned_final_geometry_cannot_pass(self):
        task = make_task("repair_shelter", 42)
        bridge = SimulatedConstructionBridge(task)
        audit = grade_task(task, bridge.snapshot())
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["matched_cells"], audit["target_cell_count"])
        self.assertIn("fixture", audit["failures"][0])
        bridge.blocks.update(task.expected)
        self.assertFalse(grade_task(task, bridge.snapshot())["passed"])

    async def test_status_is_bounded_and_reports_facts_not_model_supplied_ownership(self):
        task, bridge, targets = await self.owned_mistake()
        inspected = await bridge.execute_tool({"name": "inspect", "args": {"position": position(targets[0])}})
        self.assertTrue(inspected["data"]["own_block_repair"]["eligible"])
        self.assertTrue(inspected["data"]["diggable"])
        self.assertFalse(inspected["data"]["natural_obstruction"])
        self.assertFalse(inspected["data"]["protected"])
        self.assertEqual(inspected["data"]["own_block_repair"]["desired"], "air")
        state = await bridge.get_state()
        facts = state["constructionPractice"]["construction_status"]
        self.assertEqual(facts["repairable_count"], 2)
        self.assertTrue(facts["complete"])
        self.assertTrue(all(row["observed"] == "cobblestone" and row["desired"] == "air"
                            for row in facts["repairable_owned_mismatches"]))
        self.assertEqual(facts["owned_placement_count"], 2)
        self.assertTrue(all(row["placed_block"] == "cobblestone" and row["repair_eligible"]
                            for row in facts["owned_placements"]))

    async def test_physical_diggability_is_not_ordinary_permission_to_demolish(self):
        task, bridge, targets = await self.owned_mistake()
        cobble = targets[0]
        chest = coordinates(task.fixtures[0]["position"])
        for p in (cobble, chest):
            observed = await bridge.execute_tool({"name": "inspect", "args": {"position": position(p)}})
            self.assertTrue(observed["data"]["diggable"])
            self.assertFalse(observed["data"]["natural_obstruction"])
            before = bridge.snapshot()
            dig = await bridge.execute_tool({"name": "dig", "args": {"position": position(p)}})
            self.assertFalse(dig["success"])
            if p == cobble:
                self.assertEqual(dig["data"]["error_code"], "REPAIR_REQUIRED")
                self.assertTrue(dig["data"]["own_block_repair"]["eligible"])
                self.assertEqual(dig["data"]["own_block_repair"]["desired"], "air")
                self.assertEqual(dig["data"]["own_block_repair"]["available_tool"], "repair_batch")
                self.assertIn("physically breakable", dig["message"])
                self.assertIn("must be chosen explicitly", dig["message"])
            self.assertEqual(bridge.snapshot(), before)

    async def test_unrelated_unowned_build_still_rejects_ordinary_dig_as_unsafe(self):
        task = make_task("shelter", 42)
        bridge = SimulatedConstructionBridge(task)
        await self.register(bridge)
        p = next(p for p, block in task.expected.items() if block == "air")
        bridge.blocks[p] = "cobblestone"  # External/player build, no backend placement receipt.
        before = bridge.snapshot()
        result = await bridge.execute_tool({"name": "dig", "args": {"position": position(p)}})
        self.assertEqual(result["data"]["error_code"], "UNSAFE_BLOCK")
        self.assertNotIn("own_block_repair", result["data"])
        self.assertEqual(bridge.snapshot(), before)

    async def test_owned_repair_required_feedback_survives_compaction_without_automatic_repair(self):
        from agent.tool_agent import compact
        task, bridge, targets = await self.owned_mistake()
        action = {"name": "dig_batch", "args": {"positions": [position(targets[0])]}}
        before = bridge.snapshot()
        result = await bridge.execute_tool(action)
        self.assertEqual(result["data"]["error_code"], "REPAIR_REQUIRED")
        facts = compact({"action": action, "result": result})["result"]["data"]["own_block_repair"]
        self.assertTrue(facts["eligible"])
        self.assertEqual(facts["desired"], "air")
        self.assertEqual(facts["available_tool"], "repair_batch")
        self.assertEqual(bridge.snapshot(), before)
        self.assertEqual([event["kind"] for event in bridge.events], ["place", "place"])


class ModelLoopIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Fake-model plumbing tests, never reported as real Jarvis competence."""
    async def evaluate_fake(self, task_name, full_goals):
        task = make_task(task_name, 42)
        all_targets = list(task.expected)
        targets = all_targets if full_goals else all_targets[:1]
        goal = {"kind": "regions_match", "regions": [{
            "min": position((task.minimum[0], task.minimum[1], task.minimum[2])),
            "max": position((task.maximum[0], task.minimum[1], task.maximum[2])) if full_goals else position(targets[0]),
            "block": "cobblestone", "mode": "solid"}], "must_change": task_name != "partial_row"}
        model_decision = {"type": "act", "goals": [goal], "reason": "Fake-model plumbing test only.",
                          "actions": [{"name": "place_batch", "args": {
                              "item": "cobblestone", "positions": [position(p) for p in targets]}}]}

        class FakeLocalClient:
            def __init__(self, model, stream):
                self.calls, self.stream = 0, stream
                self.client = SimpleNamespace(close=AsyncMock())
                self.responses = [model_decision, {"requirements": []},
                                  {"covers_request": True, "reason": "Fake reviewer approves for this plumbing test.", "missing_outcomes": []},
                                  {"type": "act", "reason": "Fake model reselects its original targets from grounded facts.",
                                   "actions": model_decision["actions"]}]

            async def generate_response(self, messages, **kwargs):
                self.calls += 1
                response = json.dumps(self.responses.pop(0))
                self.stream.write(json.dumps({"call": self.calls, "messages": messages, "response": response}) + "\n")
                return response

        with tempfile.TemporaryDirectory() as directory, patch("practice.construction.ROOT", Path(directory)), \
                patch("practice.construction.LocalLoggedClient", FakeLocalClient), redirect_stdout(io.StringIO()):
            report = await run(SimpleNamespace(model="fake-no-service", episodes=1, seed=42,
                timeout=3, max_steps=3, task=task_name))
            artifacts = next((Path(directory) / "data" / "construction-practice").iterdir())
            self.assertTrue((artifacts / "report.json").is_file())
            self.assertTrue((artifacts / "tools.jsonl").is_file())
            self.assertTrue((artifacts / "model.jsonl").is_file())
            self.assertTrue((artifacts / "experience" / "experience.sqlite3").is_file())
            self.assertFalse((Path(directory) / "data" / "memory").exists())
        return report, model_decision

    async def test_model_chooses_complete_contract_and_exact_action_without_substitution(self):
        report, decision = await self.evaluate_fake("partial_row", True)
        case = report["cases"][0]
        self.assertTrue(case["passed"])
        self.assertEqual(case["result"]["data"]["goals"], decision["goals"])
        self.assertEqual(case["result"]["data"]["trace"][0]["action"], decision["actions"][0])
        self.assertEqual(report["inference_calls"], 4)
        self.assertFalse(report["weight_training"])
        self.assertFalse(report["normal_world_access"])
        self.assertFalse(report["paid_apis"])
        self.assertTrue(report["suite_execution_complete"])
        self.assertTrue(report["all_tasks_passed"])
        self.assertEqual((report["passed_count"], report["total_count"]), (1, 1))

    async def test_oracle_rejects_weak_model_goal_even_if_model_review_approves(self):
        report, decision = await self.evaluate_fake("negative_floor", False)
        case = report["cases"][0]
        self.assertFalse(case["passed"])
        self.assertFalse(case["result"]["verified"])
        self.assertFalse(case["independent_audit"]["passed"])
        self.assertEqual(case["independent_audit"]["matched_cells"], 1)
        self.assertEqual(case["result"]["data"]["goals"], decision["goals"])
        self.assertGreater(len(case["independent_audit"]["mismatches"]), 0)
        self.assertTrue(report["suite_execution_complete"])
        self.assertFalse(report["all_tasks_passed"])
        self.assertEqual((report["passed_count"], report["total_count"]), (0, 1))

    async def test_deadline_keeps_partial_artifacts_without_hidden_retries(self):
        instances = []

        class SlowLocalClient:
            def __init__(self, model, stream):
                self.calls, self.stream = 0, stream
                self.client = SimpleNamespace(close=AsyncMock())
                instances.append(self)

            async def generate_response(self, messages, **kwargs):
                self.calls += 1
                try:
                    await asyncio.Future()
                finally:
                    self.stream.write(json.dumps({"call": self.calls, "messages": messages, "error": "CancelledError"}) + "\n")
                    self.stream.flush()

        with tempfile.TemporaryDirectory() as directory, patch("practice.construction.ROOT", Path(directory)), \
                patch("practice.construction.LocalLoggedClient", SlowLocalClient), redirect_stdout(io.StringIO()):
            report = await run(SimpleNamespace(model="fake-no-service", episodes=2, seed=42,
                timeout=0.05, max_steps=3, task="negative_floor"))
            artifacts = next((Path(directory) / "data" / "construction-practice").iterdir())
            saved = json.loads((artifacts / "report.json").read_text())
            self.assertEqual(instances[0].calls, 1)
            instances[0].client.close.assert_awaited_once()
            self.assertFalse(saved["all_tasks_passed"])
            self.assertFalse(saved["suite_execution_complete"])
            self.assertEqual(saved["evaluated_count"], 1)
            self.assertEqual(saved["unrun_count"], 1)
            self.assertIn("after", saved["cases"][0])
            self.assertFalse(saved["cases"][0]["result"]["verified"])
            self.assertEqual(len((artifacts / "model.jsonl").read_text().splitlines()), 1)
            self.assertTrue((artifacts / "episode-1-layout.json").exists())
            self.assertTrue((artifacts / "experience" / "experience.sqlite3").exists())
            self.assertEqual(report["inference_calls"], 1)


if __name__ == "__main__":
    unittest.main()
