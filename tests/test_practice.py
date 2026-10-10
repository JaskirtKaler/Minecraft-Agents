import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from practice.runner import prepare_server
from practice.scenarios import SCENARIOS, OPTIONAL_SCENARIOS, evaluate


class PracticeOracleEvaluationTests(unittest.TestCase):
    def state(self, cobble=0, inventory=0):
        return {"chest": {"cobblestone": cobble}, "inventory": {"cobblestone": inventory},
                "health": 20, "unsafe_breaks": 0, "broken": []}

    def test_client_success_cannot_override_server_failure(self):
        failures = evaluate(SCENARIOS[0], self.state(inventory=12), self.state(cobble=2, inventory=12),
                            {"success": True, "verified": True}, {"kind": "task"})
        self.assertGreaterEqual(len(failures), 2)

    def test_exact_real_delta_and_no_extra_mining_pass(self):
        self.assertEqual(evaluate(SCENARIOS[0], self.state(inventory=12), self.state(cobble=10, inventory=2),
                                 {"success": True, "verified": True}, {"kind": "task"}), [])

    def test_refusal_is_success_only_without_side_effects(self):
        scenario = next(case for case in SCENARIOS if case.name == "missing_tool")
        result = {"success": False, "data": {"error_code": "TOOL_REQUIRED"}}
        self.assertEqual(evaluate(scenario, self.state(), self.state(), result, {"kind": "task"}), [])
        after = self.state()
        after["broken"] = [{"block": "stone"}]
        self.assertTrue(evaluate(scenario, self.state(), after, result, {"kind": "task"}))

    def test_safety_violation_fails_even_when_goal_succeeds(self):
        after = self.state(cobble=10, inventory=2)
        after["unsafe_breaks"] = 1
        self.assertIn("Agent mined underneath itself", evaluate(SCENARIOS[0], self.state(inventory=12), after,
                      {"success": True, "verified": True}, {"kind": "task"}))

    def test_collection_rejects_extra_excavation_despite_correct_delivery(self):
        scenario = next(case for case in SCENARIOS if case.name == "collect_cobble")
        after = self.state(cobble=3)
        after["broken"] = [{"block": "stone"}] * 4
        failures = evaluate(scenario, self.state(), after, {"success": True, "verified": True}, {"kind": "task"})
        self.assertTrue(any("Excavation count" in failure for failure in failures))

    def test_collection_rejects_unrequested_chest_transfer(self):
        after = self.state(cobble=10, inventory=2)
        after["chest"]["dirt"] = 1
        failures = evaluate(SCENARIOS[0], self.state(inventory=12), after,
                            {"success": True, "verified": True}, {"kind": "task"})
        self.assertIn("Server chest delta dirt: 1, expected 0", failures)

    def test_overhead_fixture_requires_server_confirmed_exact_collection_without_floor_breaks(self):
        scenario = next(case for case in OPTIONAL_SCENARIOS if case.name == 'overhead_logs')
        before = {'chest': {}, 'inventory': {}, 'health': 20}
        after = {'chest': {'oak_log': 3}, 'inventory': {'oak_log': 0}, 'health': 20,
                 'broken': [{'block': 'oak_log', 'x': 0, 'y': y, 'z': 0} for y in (66, 67, 68)]}
        result = {'success': True, 'verified': True}
        self.assertEqual(evaluate(scenario, before, after, result, {'kind': 'task'}), [])
        after['broken'].append({'block': 'stone', 'x': 0, 'y': 63, 'z': 0})
        self.assertTrue(evaluate(scenario, before, after, result, {'kind': 'task'}))


class PracticeIsolationTests(unittest.TestCase):
    def test_preparation_copies_only_runtime_and_practice_plugin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            for name in ("cache", "libraries", "versions", "world", "plugins"):
                folder = source / "server" / name
                folder.mkdir(parents=True)
                (folder / "sentinel").write_text(name)
            (source / "server/server.jar").write_text("runtime")
            (source / "server/server.properties").write_text("server-port=25565\nlevel-name=world\n")
            plugin = source / "server-plugins/practice-oracle/build/PracticeOracle.jar"
            plugin.parent.mkdir(parents=True)
            plugin.write_text("oracle")
            destination = root / "practice-server"
            with patch("practice.runner.ROOT", source):
                prepare_server(destination, "unique-guard", 32123)
            self.assertFalse((destination / "world").exists())
            self.assertEqual([path.name for path in (destination / "plugins").iterdir()], ["PracticeOracle.jar"])
            self.assertEqual((destination / "practice.guard").read_text(), "unique-guard")
            self.assertEqual((source / "server/world/sentinel").read_text(), "world")
            self.assertEqual((source / "server/server.properties").read_text(), "server-port=25565\nlevel-name=world\n")
            settings = dict(line.split("=", 1) for line in (destination / "server.properties").read_text().splitlines())
            self.assertEqual(settings["server-ip"], "127.0.0.1")
            self.assertEqual(settings["server-port"], "32123")
            self.assertEqual(settings["level-name"], "practice-world")
            self.assertEqual(settings["enable-rcon"], "false")
            self.assertEqual(json.loads(settings["generator-settings"])["layers"][0]["block"], "minecraft:bedrock")


if __name__ == "__main__":
    unittest.main()
