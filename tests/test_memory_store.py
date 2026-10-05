import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agent.memory_store import WorldMemoryStore


def adapter_group_id(world_id, dimension):
    """Mirror agent.graphiti_memory.group_id without importing optional deps."""
    scope = json.dumps([world_id, dimension], separators=(",", ":"))
    return "minecraft_" + hashlib.sha256(scope.encode()).hexdigest()[:32]


def state(
    *,
    world_id="world-alpha",
    dimension="minecraft:overworld",
    session_id="session-1",
    position=(0, 64, 0),
    nearby_key_blocks=None,
    observed_at="2026-10-05T00:00:00Z",
):
    return {
        "username": "AI_Agent",
        "world": {
            "id": world_id,
            "dimension": dimension,
            "sessionId": session_id,
        },
        "observedAt": observed_at,
        "stats": {
            "position": {"x": position[0], "y": position[1], "z": position[2]},
            "health": 20,
            "food": 20,
        },
        "inventory": [{"name": "oak_log", "count": 2}],
        "nearbyKeyBlocks": nearby_key_blocks or {},
    }


class WorldMemoryStoreTests(unittest.TestCase):
    def test_interrupted_request_does_not_become_a_claim_of_objective_failure(self):
        self.assertEqual(WorldMemoryStore._task_outcome({
            "success": False, "verified": False, "status": "unknown",
        }), "unverified")

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "memory.sqlite3"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_persists_across_restart_and_keeps_prior_session_snapshot(self):
        first = state(
            session_id="session-one",
            position=(1, 64, 1),
            nearby_key_blocks={"chest": [{"x": 5, "y": 64, "z": -2}]},
            observed_at="2026-10-05T00:00:00Z",
        )
        second = state(
            session_id="session-two",
            position=(9, 70, 9),
            observed_at="2026-10-05T00:05:00Z",
        )
        store = WorldMemoryStore(self.path)
        self.assertTrue(store.observe(first))
        task_id = store.record_task(
            "gather two oak logs",
            {"name": "mine_logs", "args": {"item": "oak_log", "count": 2}},
            {
                "success": True,
                "verified": True,
                "message": "Inventory increased by two logs.",
                "data": {"requested": 2, "before": 0, "after": 2},
            },
            first,
            requester="Alex",
        )
        self.assertTrue(task_id)
        self.assertTrue(store.observe(second))
        pending_before_restart = store.pending(limit=20)
        self.assertTrue(any(item["kind"] == "task_outcome" for item in pending_before_restart))
        store.close()

        reopened = WorldMemoryStore(self.path)
        memory = reopened.context(second)
        self.assertIn("Previous session snapshot", memory)
        self.assertIn("position=(1, 64, 1)", memory)
        self.assertIn("gather two oak logs", memory)
        self.assertIn("chest @ 5, 64, -2", memory)
        self.assertGreaterEqual(reopened.status()["tasks"], 1)
        self.assertGreaterEqual(len(reopened.pending(limit=20)), len(pending_before_restart))
        reopened.close()

    def test_graph_group_matches_the_graphiti_adapter_namespace(self):
        self.assertEqual(
            WorldMemoryStore.graph_group_id("server name / test", "minecraft:the_nether"),
            adapter_group_id("server name / test", "minecraft:the_nether"),
        )

    def test_context_isolated_by_world_and_dimension(self):
        overworld = state(
            world_id="shared-world",
            dimension="minecraft:overworld",
            session_id="overworld-session",
            nearby_key_blocks={"chest": [{"x": 1, "y": 64, "z": 1}]},
        )
        nether = state(
            world_id="shared-world",
            dimension="minecraft:the_nether",
            session_id="nether-session",
            nearby_key_blocks={"furnace": [{"x": 2, "y": 70, "z": 2}]},
        )
        another_world = state(
            world_id="other-world",
            dimension="minecraft:overworld",
            session_id="other-session",
        )
        store = WorldMemoryStore(self.path)
        store.observe(overworld)
        store.observe(nether)
        store.observe(another_world)
        store.record_task(
            "overworld-only objective",
            None,
            {"success": True, "verified": True},
            overworld,
        )
        store.record_task(
            "nether-only objective",
            None,
            {"success": False, "verified": False, "message": "lava"},
            nether,
        )

        overworld_context = store.context(overworld)
        nether_context = store.context(nether)
        other_context = store.context(another_world)
        self.assertIn("overworld-only objective", overworld_context)
        self.assertNotIn("nether-only objective", overworld_context)
        self.assertIn("chest @ 1, 64, 1", overworld_context)
        self.assertNotIn("furnace @ 2, 70, 2", overworld_context)
        self.assertIn("nether-only objective", nether_context)
        self.assertNotIn("overworld-only objective", nether_context)
        self.assertIn("furnace @ 2, 70, 2", nether_context)
        self.assertNotIn("nether-only objective", other_context)
        store.close()

    def test_partial_scan_does_not_deplete_but_explicit_block_change_does(self):
        initial = state(
            nearby_key_blocks={
                "chest": [{"x": 3, "y": 65, "z": 4}],
                "oak_log": [{"x": -1.2, "y": 65, "z": 4}],
            },
            observed_at="2026-10-05T00:00:00Z",
        )
        partial_follow_up = state(
            nearby_key_blocks={},
            observed_at="2026-10-05T00:01:00Z",
        )
        store = WorldMemoryStore(self.path)
        self.assertTrue(store.observe(initial))
        self.assertFalse(store.observe(partial_follow_up))
        self.assertIn("chest @ 3, 65, 4", store.context(partial_follow_up))
        self.assertIn("oak_log @ -2, 65, 4", store.context(partial_follow_up))

        store.record_event(
            {
                "event": "block_change",
                "world": initial["world"],
                "observedAt": "2026-10-05T00:02:00Z",
                "data": {
                    "newBlock": {
                        "name": "minecraft:air",
                        "position": {"x": 3, "y": 65, "z": 4},
                    }
                },
            }
        )
        context = store.context(partial_follow_up)
        self.assertNotIn("chest @ 3, 65, 4", context)
        self.assertIn("Known POIs: none recorded.", context)
        self.assertIn("oak_log @ -2, 65, 4", context)
        self.assertTrue(any(item["kind"] == "poi_removed" for item in store.pending(limit=20)))
        store.close()

    def test_task_outcomes_keep_verified_failure_and_unverified_distinct(self):
        current = state()
        store = WorldMemoryStore(self.path)
        store.observe(current)
        store.record_task(
            "verified objective",
            {"name": "mine_logs"},
            {"success": True, "verified": True, "data": {"before": 0, "after": 3}},
            current,
        )
        store.record_task(
            "failed objective",
            {"name": "give_item"},
            {"success": False, "verified": False, "message": "recipient absent"},
            current,
        )
        store.record_task(
            "unverified objective",
            {"name": "planner"},
            {"success": True, "verified": False, "message": "code returned"},
            current,
        )
        store.record_task(
            "missing-success objective",
            {"name": "planner"},
            {"verified": True, "message": "missing success evidence"},
            current,
        )

        context = store.context(current, limit=8)
        self.assertIn("verified: verified objective", context)
        self.assertIn("failure: failed objective", context)
        self.assertIn("unverified: unverified objective", context)
        self.assertIn("unverified: missing-success objective", context)
        outcomes = {
            item["payload"]["outcome"]
            for item in store.pending(limit=20)
            if item["kind"] == "task_outcome"
        }
        self.assertEqual(outcomes, {"verified", "failure", "unverified"})
        store.close()

    def test_world_join_observes_embedded_state_and_unready_state_cannot_overwrite_it(self):
        current = state(position=(2.2, 64, -3.8), nearby_key_blocks={"oak_log": [{"x": 1, "y": 64, "z": 1}]})
        store = WorldMemoryStore(self.path)
        store.record_event({"event": "world_join", "data": {"currentState": current}})
        self.assertIn("position=(2.2, 64, -3.8)", store.context(current))
        self.assertIn("oak_log @ 1, 64, 1", store.context(current))

        unready = {
            "ready": False,
            "world": current["world"],
            "observedAt": "2026-10-05T00:01:00Z",
            "nearbyKeyBlocks": {},
        }
        self.assertFalse(store.observe(unready))
        self.assertIn("position=(2.2, 64, -3.8)", store.context(current))
        store.close()

    def test_failed_outbox_is_retried_and_acknowledgement_survives_restart(self):
        current = state()
        store = WorldMemoryStore(self.path)
        store.observe(current)
        pending = store.pending(limit=1)
        self.assertEqual(len(pending), 1)
        episode_id = pending[0]["id"]
        self.assertTrue(store.fail(episode_id, "FalkorDBLite temporarily unavailable"))
        retried = store.pending(limit=1)
        self.assertEqual(retried[0]["id"], episode_id)
        self.assertTrue(store.acknowledge(episode_id))
        self.assertFalse(any(item["id"] == episode_id for item in store.pending(limit=20)))
        self.assertEqual(store.status()["pending"], 0)
        store.close()

        reopened = WorldMemoryStore(self.path)
        self.assertFalse(any(item["id"] == episode_id for item in reopened.pending(limit=20)))
        self.assertGreaterEqual(reopened.status()["acknowledged"], 1)
        reopened.close()


if __name__ == "__main__":
    unittest.main()
