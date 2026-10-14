"""Offline seam test for durable world memory, scoped graph recall, and planner prompts.

The fake graph is intentionally file-backed.  It proves the controller's
outbox/worker/restart path without loading Graphiti, Ollama, or any hosted
provider; ``test_memory_live.py`` covers the real local Graphiti stack.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent.config import Config
from agent.experience import ExperienceLibrary
from agent.graphiti_memory import group_id
from agent.tool_agent import ModelToolAgent
from agent.world_memory import WorldMemory


TARGET_WORLD = "durable-memory-test-world"
TARGET_DIMENSION = "minecraft:overworld"
TARGET_MARKER = "GRAPH_RECALL_TARGET__verified_oak_route"
FOREIGN_MARKER = "GRAPH_RECALL_FOREIGN__nether_only"


def state(*, dimension=TARGET_DIMENSION, session="first-session"):
    return {
        "ready": True,
        "username": "MemoryProbe",
        "observedAt": "2026-10-14T12:00:00Z",
        "world": {"id": TARGET_WORLD, "dimension": dimension, "sessionId": session},
        "stats": {"position": {"x": 11.5, "y": 64, "z": -9.5}},
        "inventory": [{"name": "oak_log", "count": 2}],
        "nearbyKeyBlocks": {},
    }


class FileGraphiti:
    """Tiny durable Graphiti-shaped backend with real world/dimension grouping."""

    def __init__(self, directory, settings):
        self.path = Path(directory) / "fake-graphiti-recall.json"
        self.settings = settings
        self.started = asyncio.Event()
        self.task_ingested = asyncio.Event()
        self.closed = False
        self.ingested = []
        self.searches = []

    def _read(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _write(self, value):
        self.path.write_text(json.dumps(value, sort_keys=True))

    async def start(self):
        self.started.set()

    async def ingest(self, episode):
        self.ingested.append(episode)
        scope = group_id(episode["world_id"], episode["dimension"])
        records = self._read()
        scoped = records.setdefault(scope, {})
        if episode["kind"] == "task_outcome":
            marker = TARGET_MARKER if episode["dimension"] == TARGET_DIMENSION else FOREIGN_MARKER
            scoped.setdefault(episode["id"], marker)
            self.task_ingested.set()
        self._write(records)

    async def search(self, query, current_state):
        world = current_state.get("world", {})
        scope = group_id(world.get("id", ""), world.get("dimension", ""))
        self.searches.append((query, scope))
        markers = list(self._read().get(scope, {}).values())
        if not markers:
            return ""
        return "Graphiti historical memory (fake; recheck live world):\n" + "\n".join(
            f"- {marker}" for marker in markers
        )

    async def close(self):
        self.closed = True


class DurableMemoryPlannerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def wait_until(self, predicate, label, timeout=1.5):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail(f"Timed out waiting for {label}")

    async def test_worker_restart_scopes_durable_graph_recall_and_injects_planner_prompt(self):
        with tempfile.TemporaryDirectory(prefix="minecraft-memory-pipeline-") as directory:
            settings = Config(memory_dir=directory, graphiti_enabled=True)
            first = second = None
            library = None
            with patch("agent.world_memory.GraphitiMemory", FileGraphiti):
                try:
                    first = WorldMemory(settings)
                    target = state()
                    foreign = state(dimension="minecraft:the_nether")
                    first.observe(target)
                    first.record_task(
                        "mine 2 oak logs from the observed route",
                        {"name": "mine_logs", "args": {"item": "oak_log", "count": 2}},
                        {
                            "success": True,
                            "verified": True,
                            "message": "Collected exactly 2 oak logs after checking the route.",
                            "data": {"item": "oak_log", "requested": 2, "before": 0, "after": 2},
                        },
                        target,
                        requester="MemoryTester",
                    )
                    first.observe(foreign)
                    first.record_task(
                        "mine 1 nether quartz",
                        {"name": "mine_resource", "args": {"item": "nether_quartz_ore", "count": 1}},
                        {"success": True, "verified": True, "message": "Foreign-dimension task."},
                        foreign,
                    )
                    self.assertGreater(first.store.status()["pending"], 0, "SQLite outbox must be durable before worker start")
                    first.start()
                    await self.wait_until(lambda: first.graph_ready, "first graph backend startup")
                    await self.wait_until(lambda: first.store.status()["pending"] == 0, "first outbox acknowledgement")
                    self.assertTrue(first.graphiti.task_ingested.is_set())
                    self.assertGreaterEqual(first.store.status()["acknowledged"], 2)
                    await first.close()
                    first = None

                    # A newly constructed WorldMemory must recover both the SQLite
                    # checkpoint and the separate graph file from the same directory.
                    second = WorldMemory(settings)
                    second.start()
                    await self.wait_until(lambda: second.graph_ready, "restarted graph backend startup")
                    # Do not spend a fake or real graph turn on the new join while
                    # testing recall; the existing graph is already durable.
                    await second.pause()
                    rejoined = state(session="second-session")
                    second.observe(rejoined)

                    target_context = await second.context(rejoined, "where was the verified oak route?")
                    self.assertIn("mine 2 oak logs", target_context)
                    self.assertIn(TARGET_MARKER, target_context)
                    self.assertNotIn(FOREIGN_MARKER, target_context)
                    self.assertIn("Previous session snapshot", target_context)

                    foreign_context = await second.context(foreign, "what happened in the nether?")
                    self.assertIn(FOREIGN_MARKER, foreign_context)
                    self.assertNotIn(TARGET_MARKER, foreign_context)
                    unknown = state(dimension="minecraft:the_end", session="third-session")
                    self.assertNotIn("GRAPH_RECALL_", await second.context(unknown, "memory"))

                    bridge = SimpleNamespace(
                        get_state=AsyncMock(return_value=rejoined),
                        execute_tool=AsyncMock(),
                        cancel_task=AsyncMock(),
                    )
                    client = SimpleNamespace(generate_response=AsyncMock(return_value=json.dumps({
                        "type": "answer", "message": "The saved route is historical; I would recheck it live."
                    })))
                    learning = Path(directory) / "learning"
                    library = ExperienceLibrary(learning)
                    planner_settings = SimpleNamespace(
                        learning_dir=str(learning),
                        model_step_timeout=2,
                        planner_max_tokens=256,
                        agent_max_steps=1,
                        agent_timeout=5,
                        goal_review_enabled=False,
                        agent_reflection_delay=60,
                    )
                    agent = ModelToolAgent(bridge, client, memory=second, settings=planner_settings,
                                           library=library, auto_reflect=False)
                    try:
                        result = await agent.run("where was the verified oak route?")
                    finally:
                        await agent.aclose()
                    self.assertEqual(result["status"], "answer")
                    bridge.execute_tool.assert_not_awaited()
                    prompt = json.loads(client.generate_response.await_args.args[0][1]["content"])
                    self.assertIn(TARGET_MARKER, prompt["world_memory"])
                    self.assertNotIn(FOREIGN_MARKER, prompt["world_memory"])
                    self.assertIn("Graphiti historical memory", prompt["world_memory"])
                finally:
                    if library:
                        library.close()
                    if first:
                        await first.close()
                    if second:
                        await second.close()


if __name__ == "__main__":
    unittest.main()
