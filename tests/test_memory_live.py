"""Opt-in isolated Graphiti/Ollama lifecycle test; never connects to Minecraft.

Run only against a local OpenAI-compatible memory endpoint:

``RUN_MEMORY_LIVE_TEST=1 python -m unittest tests.test_memory_live -v``

The test creates a temporary ``MEMORY_DIR`` and refuses a non-local endpoint,
so it cannot ingest normal world memory or spend hosted-provider credits.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock
from urllib.parse import urlparse

from agent.config import Config
from agent.experience import ExperienceLibrary
from agent.graphiti_memory import group_id
from agent.tool_agent import ModelToolAgent
from agent.world_memory import WorldMemory


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
LIVE_WORLD = "isolated-live-graphiti-world"
LIVE_DIMENSION = "minecraft:overworld"


def checkpoint(*, session="first-session", dimension=LIVE_DIMENSION, world_id=LIVE_WORLD):
    return {
        "ready": True,
        "username": "MemoryProbe",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "world": {"id": world_id, "dimension": dimension, "sessionId": session},
        "stats": {"position": {"x": 11.5, "y": 64, "z": -9.5}},
        "inventory": [{"name": "oak_log", "count": 2}],
        "nearbyKeyBlocks": {},
    }


@unittest.skipUnless(os.getenv("RUN_MEMORY_LIVE_TEST") == "1", "opt-in local Ollama/embedded DB test")
class LocalGraphitiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def timeout_from_env(name, default):
        try:
            return max(1.0, float(os.getenv(name, default)))
        except ValueError:
            return float(default)

    async def wait_until(self, predicate, label, timeout, memory):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.1)
        self.fail(
            f"Timed out waiting for {label} after {timeout:.0f}s; "
            f"memory status={memory.status()}"
        )

    def require_local_memory_endpoint(self, settings):
        endpoint = urlparse(settings.memory_base_url)
        if endpoint.hostname not in LOCAL_HOSTS:
            self.skipTest(
                "RUN_MEMORY_LIVE_TEST only permits a local MEMORY_BASE_URL; "
                "refusing hosted inference or normal-world ingestion."
            )
        self.assertTrue(settings.memory_model, "MEMORY_MODEL must name the local extraction model")
        self.assertTrue(settings.memory_embedding_model, "MEMORY_EMBEDDING_MODEL must name the local embedding model")

    async def episodic_count(self, graphiti, episode):
        """Count a stable episode name in its one world/dimension graph."""
        scope = group_id(episode["world_id"], episode["dimension"])
        driver = graphiti.graph.driver.clone(database=scope)
        if getattr(driver, "_init_task", None):
            await driver._init_task
        rows, _, _ = await driver.execute_query(
            "MATCH (e:Episodic {name: $name}) RETURN count(e) AS count",
            name=f"minecraft:{episode['id']}",
        )
        return rows[0]["count"]

    async def test_world_memory_worker_restart_scoped_graph_recall_and_planner_prompt(self):
        """Use the production WorldMemory worker, then a fake planner only.

        Graphiti/Ollama performs the intentional local extraction and search.
        The planner client is an in-process fake, so this never makes a
        Nebius/hosted action-planning call.
        """
        with tempfile.TemporaryDirectory(prefix="minecraft-memory-live-") as directory:
            settings = Config(memory_dir=directory, graphiti_enabled=True)
            self.require_local_memory_endpoint(settings)
            self.assertEqual(Path(directory), Path(settings.memory_dir))
            startup_timeout = self.timeout_from_env("MEMORY_LIVE_START_TIMEOUT", 90)
            # One compact join episode plus one compact task episode are ingested
            # serially. Bound the complete wait, but allow slower local models.
            ingest_timeout = self.timeout_from_env(
                "MEMORY_LIVE_INGEST_TIMEOUT",
                max(240, settings.memory_ingest_timeout * 2 + 60),
            )
            first = second = None
            library = None
            try:
                first = WorldMemory(settings)
                initial = checkpoint()
                first.observe(initial)
                first.record_task(
                    "mine 2 oak logs from the observed route",
                    {"name": "mine_logs", "args": {"item": "oak_log", "count": 2}},
                    {
                        "success": True,
                        "verified": True,
                        "message": "MemoryProbe verified collecting exactly 2 oak logs.",
                        "data": {"item": "oak_log", "requested": 2, "before": 0, "after": 2},
                    },
                    initial,
                    requester="MemoryTester",
                )
                task_episode = next(
                    episode for episode in first.store.pending(limit=20)
                    if episode["kind"] == "task_outcome"
                )
                self.assertGreater(first.store.status()["pending"], 0, "outbox must exist before background extraction")
                first.start()
                await self.wait_until(lambda: first.graph_ready, "Graphiti startup", startup_timeout, first)
                await self.wait_until(
                    lambda: first.store.status()["pending"] == 0,
                    "durable ingestion acknowledgement",
                    ingest_timeout,
                    first,
                )
                self.assertEqual(first.status()["last_error"], "")
                self.assertGreaterEqual(first.store.status()["acknowledged"], 2)
                await first.close()
                first = None

                # Reconstruct all memory objects from disk. Pause immediately
                # after startup so the test recalls the old graph without adding
                # a second extraction request for the new session transition.
                second = WorldMemory(settings)
                second.start()
                await self.wait_until(lambda: second.graph_ready, "Graphiti restart", startup_timeout, second)
                await second.pause()
                rejoined = checkpoint(session="second-session")
                second.observe(rejoined)

                # A process can die after Graphiti saves but before the SQLite
                # acknowledgement. Replaying the exact durable episode after a
                # restart must find its stable name and not extract a duplicate.
                self.assertEqual(await self.episodic_count(second.graphiti, task_episode), 1)
                await second.graphiti.ingest(task_episode)
                self.assertEqual(await self.episodic_count(second.graphiti, task_episode), 1)

                query = "where did MemoryProbe collect verified oak logs?"
                graph_recall = await asyncio.wait_for(second.graphiti.search(query, rejoined), timeout=15)
                self.assertIn("Graphiti historical memory", graph_recall)
                self.assertIn("oak", graph_recall.lower())
                restored = await second.context(rejoined, query)
                self.assertIn("mine 2 oak logs", restored, "SQLite task journal must survive restart")
                self.assertIn("Graphiti historical memory", restored, "graph recall must be added to controller context")
                self.assertIn("Previous session snapshot", restored)

                foreign = checkpoint(session="foreign-session", dimension="minecraft:the_nether")
                self.assertEqual(await asyncio.wait_for(second.graphiti.search(query, foreign), timeout=15), "")
                foreign_context = await second.context(foreign, query)
                self.assertNotIn("Graphiti historical memory", foreign_context)
                self.assertNotIn("mine 2 oak logs", foreign_context)
                foreign_world = checkpoint(
                    session="foreign-world-session",
                    world_id="isolated-live-graphiti-other-world",
                )
                self.assertEqual(
                    await asyncio.wait_for(second.graphiti.search(query, foreign_world), timeout=15),
                    "",
                )

                bridge = SimpleNamespace(
                    get_state=AsyncMock(return_value=rejoined),
                    execute_tool=AsyncMock(),
                    cancel_task=AsyncMock(),
                )
                client = SimpleNamespace(generate_response=AsyncMock(return_value=json.dumps({
                    "type": "answer", "message": "I will treat the remembered route as a live-world hint."
                })))
                learning = Path(directory) / "learning"
                library = ExperienceLibrary(learning)
                planner_settings = SimpleNamespace(
                    learning_dir=str(learning),
                    model_step_timeout=5,
                    planner_max_tokens=256,
                    agent_max_steps=1,
                    agent_timeout=20,
                    goal_review_enabled=False,
                    agent_reflection_delay=60,
                )
                agent = ModelToolAgent(bridge, client, memory=second, settings=planner_settings,
                                       library=library, auto_reflect=False)
                try:
                    result = await agent.run(query)
                finally:
                    await agent.aclose()
                self.assertEqual(result["status"], "answer")
                bridge.execute_tool.assert_not_awaited()
                planner_prompt = json.loads(client.generate_response.await_args.args[0][1]["content"])
                self.assertIn("Graphiti historical memory", planner_prompt["world_memory"])
                self.assertIn("oak", planner_prompt["world_memory"].lower())
            finally:
                if library:
                    library.close()
                if first:
                    await first.close()
                if second:
                    await second.close()


if __name__ == "__main__":
    unittest.main()
