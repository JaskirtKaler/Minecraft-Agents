import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.config import Config
from agent.world_memory import WorldMemory


def checkpoint(session="first"):
    return {
        "ready": True, "observedAt": "2026-10-05T08:00:00Z",
        "world": {"id": "test-world", "dimension": "overworld", "sessionId": session},
        "stats": {"position": {"x": 1, "y": 64, "z": 2}},
        "inventory": [{"name": "oak_log", "count": 3}], "nearbyKeyBlocks": {},
    }


class WorldMemoryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_recovery_works_with_graph_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Config(memory_dir=directory, graphiti_enabled=False)
            first = WorldMemory(settings)
            first.start()
            first.observe(checkpoint())
            first.record_task("mine 3 oak logs", {"name": "mine_logs"}, {
                "success": True, "verified": True, "message": "Collected 3 oak logs.",
            }, checkpoint())
            await first.close()
            second = WorldMemory(settings)
            try:
                second.start()
                second.observe(checkpoint("second"))
                restored = await second.context(checkpoint("second"), "progress")
                self.assertIn("oak_log", restored)
                self.assertIn("mine 3 oak logs", restored)
                self.assertEqual(second.status()["graphiti"], "disabled")
                self.assertEqual(Path(second.status()["directory"]), Path(directory))
            finally:
                await second.close()

    async def test_failed_extraction_keeps_outbox_and_checkpoints_for_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Config(memory_dir=directory, graphiti_enabled=False)
            first = WorldMemory(settings)
            first.observe(checkpoint())
            pending_before = first.store.pending(limit=1)
            self.assertTrue(pending_before)
            attempted = asyncio.Event()

            async def fail_ingest(episode):
                attempted.set()
                raise ConnectionError("Local model offline")

            backend = SimpleNamespace(start=AsyncMock(), ingest=AsyncMock(side_effect=fail_ingest),
                                      close=AsyncMock(), search=AsyncMock(return_value=""))
            first.graphiti = backend
            first.start()
            await asyncio.wait_for(attempted.wait(), timeout=1)
            # Let wait_for deliver the failed ingest to the worker's exception handler.
            for _ in range(5):
                await asyncio.sleep(0)
            self.assertIn("offline", first.last_error)
            pending_after = first.store.pending(limit=1)
            self.assertEqual(pending_before[0]["id"], pending_after[0]["id"])
            await first.close()
            backend.close.assert_awaited_once()
            second = WorldMemory(settings)
            try:
                self.assertEqual(second.store.pending(limit=1)[0]["id"], pending_before[0]["id"])
                self.assertIn("oak_log", await second.context(checkpoint()))
            finally:
                await second.close()

    async def test_ingestion_acknowledges_success_without_blocking_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = WorldMemory(Config(memory_dir=directory, graphiti_enabled=False))
            memory.observe(checkpoint())
            backend = SimpleNamespace(start=AsyncMock(), ingest=AsyncMock(), close=AsyncMock())
            memory.graphiti = backend
            memory.start()
            try:
                for _ in range(25):
                    if not memory.store.pending(limit=1):
                        break
                    await asyncio.sleep(0)
                self.assertFalse(memory.store.pending(limit=1))
                backend.ingest.assert_awaited()
                self.assertTrue(memory.graph_ready)
            finally:
                await memory.close()


if __name__ == "__main__":
    unittest.main()
