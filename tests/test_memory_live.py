"""Opt-in local-model integration check; never connects to Minecraft.

RUN_MEMORY_LIVE_TEST=1 python -m unittest discover -s tests -p test_memory_live.py -v
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent.config import Config
from agent.graphiti_memory import GraphitiMemory, group_id
from agent.memory_store import WorldMemoryStore


@unittest.skipUnless(os.getenv("RUN_MEMORY_LIVE_TEST") == "1", "opt-in local Ollama/embedded DB test")
class LocalGraphitiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_restart_recall_and_replay(self):
        with tempfile.TemporaryDirectory(prefix="minecraft-memory-test-") as directory:
            settings = Config()
            state = {
                "ready": True, "username": "AI_Agent", "observedAt": datetime.now(timezone.utc).isoformat(),
                "world": {"id": "test-world", "dimension": "overworld", "sessionId": "test-session"},
                "stats": {"position": {"x": 1, "y": 64, "z": 2}},
                "inventory": [{"name": "oak_log", "count": 2}], "nearbyKeyBlocks": {},
            }
            store_path = Path(directory) / "checkpoints.sqlite3"
            with WorldMemoryStore(store_path) as store:
                store.observe(state)
                store.record_task("mine 2 oak logs", {
                    "name": "mine_logs", "args": {"item": "oak_log", "count": 2},
                }, {
                    "success": True, "verified": True,
                    "message": "AI_Agent verified collecting exactly 2 oak logs; its inventory increased by 2 oak logs.",
                    "data": {"item": "oak_log", "requested": 2, "before": 0, "after": 2},
                }, state)
                episode = next(item for item in store.pending(limit=20) if item["kind"] == "task_outcome")
            first = GraphitiMemory(Path(directory), settings)
            try:
                await first.start()
                await first.ingest(episode)
                rows, _, _ = await first.graph.driver.clone(database=group_id("test-world", "overworld")).execute_query(
                    "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) RETURN a.name AS source, r.fact AS fact, b.name AS target, r.invalid_at AS invalid_at, r.expired_at AS expired_at"
                )
                print("\nExtracted relationship facts:", rows)
                nodes, _, _ = await first.graph.driver.clone(database=group_id("test-world", "overworld")).execute_query(
                    "MATCH (n:Entity) RETURN n.name AS name, n.summary AS summary"
                )
                print("\nExtracted entity summaries:", nodes)
            finally:
                await first.close()

            second = GraphitiMemory(Path(directory), settings)
            try:
                await second.start()
                await second.ingest(episode)  # Replay must not extract twice.
                with WorldMemoryStore(store_path) as reopened_store:
                    self.assertIn("mine 2 oak logs", reopened_store.context(state))
                    self.assertTrue(reopened_store.acknowledge(episode["id"]))
                scope = group_id("test-world", "overworld")
                rows, _, _ = await second.graph.driver.clone(database=scope).execute_query(
                    "MATCH (e:Episodic) RETURN count(e) AS count"
                )
                self.assertEqual(rows[0]["count"], 1)
                recalled = await second.search("AI_Agent collected oak logs", state)
                print("\nRecalled after graph restart:", recalled)
                self.assertIn("oak", recalled.lower())
                self.assertEqual(await second.search("oak logs", {"world": {
                    "id": "different-world", "dimension": "overworld",
                }}), "")
            finally:
                await second.close()


if __name__ == "__main__":
    unittest.main()
