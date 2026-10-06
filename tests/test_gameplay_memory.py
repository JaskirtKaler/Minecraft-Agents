import asyncio
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent.bridge import MineflayerBridge
from agent.config import Config
from agent.world_memory import WorldMemory
from tests.test_world_memory import checkpoint


class GameplayMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_preserves_exact_memory_and_unacknowledged_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = WorldMemory(Config(memory_dir=directory, graphiti_enabled=False))
            started = asyncio.Event()

            async def slow_ingest(_):
                started.set()
                await asyncio.Future()

            backend = SimpleNamespace(start=AsyncMock(), ingest=AsyncMock(side_effect=slow_ingest), close=AsyncMock())
            memory.graphiti = backend
            memory.observe(checkpoint())
            memory.start()
            await asyncio.wait_for(started.wait(), 0.5)
            await memory.pause()
            self.assertIsNone(memory.worker)
            self.assertTrue(memory.store.pending())
            self.assertEqual(memory.status()["graphiti"], "paused for gameplay")
            memory.record_task("get 2 cobblestone", {"name": "mine_resource"}, {
                "success": True, "verified": True, "message": "Collected 2 cobblestone.",
            }, checkpoint())
            self.assertIn("cobblestone", await memory.context(checkpoint()))
            backend.ingest.side_effect = None
            memory.resume()
            for _ in range(50):
                if not memory.store.pending():
                    break
                await asyncio.sleep(0)
            self.assertFalse(memory.store.pending())
            await memory.close()
            memory.resume()
            self.assertTrue(memory.worker.done(), "Closing must prevent a new graph worker.")

    async def test_repeatedly_failed_episode_does_not_starve_new_task_result(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = WorldMemory(Config(memory_dir=directory, graphiti_enabled=False))
            memory.observe(checkpoint())
            first = memory.store.pending()[0]["id"]
            memory.store.fail(first, "model busy")
            memory.record_task("mine 2 logs", {"name": "mine_logs"}, {"success": True, "verified": True}, checkpoint())
            self.assertNotEqual(memory.store.pending()[0]["id"], first)
            await memory.close()

    async def test_bridge_timeout_requests_cancellation_without_leaking_future(self):
        bridge = MineflayerBridge()
        bridge.active_client = SimpleNamespace(send=AsyncMock())
        with self.assertRaises(asyncio.TimeoutError):
            await bridge.execute_task({"name": "mine_resource"}, timeout=0.005)
        self.assertEqual(bridge.pending_requests, {})
        sent = [json.loads(call.args[0]) for call in bridge.active_client.send.call_args_list]
        self.assertEqual([message["type"] for message in sent], ["execute_task", "cancel_task"])

    async def test_knowledge_reply_does_not_replace_live_state(self):
        bridge = MineflayerBridge()
        bridge.latest_state = {"ready": True}
        future = asyncio.get_running_loop().create_future()
        bridge.pending_requests["facts"] = future
        await bridge._route_message({"type": "knowledge_response", "id": "facts", "data": {"name": "stone"}})
        self.assertEqual(await future, {"name": "stone"})
        self.assertEqual(bridge.latest_state, {"ready": True})


if __name__ == "__main__":
    unittest.main()
