import asyncio
import re
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from agent.graphiti_memory import GraphitiMemory, group_id


@contextmanager
def fake_graphiti_nodes():
    """Provide only the lazy import that ingest() needs; never import Graphiti."""
    nodes = types.ModuleType("graphiti_core.nodes")
    nodes.EpisodeType = SimpleNamespace(json="json")
    package = types.ModuleType("graphiti_core")
    package.__path__ = []
    with patch.dict(
        sys.modules,
        {"graphiti_core": package, "graphiti_core.nodes": nodes},
    ):
        yield nodes.EpisodeType


@contextmanager
def fake_search_recipe():
    """Supply the lazy search recipe import without initializing Graphiti packages."""
    configured = SimpleNamespace(limit=None, community_config=object())
    template = SimpleNamespace(model_copy=MagicMock(return_value=configured))
    recipes = types.ModuleType("graphiti_core.search.search_config_recipes")
    recipes.COMBINED_HYBRID_SEARCH_RRF = template
    search = types.ModuleType("graphiti_core.search")
    search.__path__ = []
    package = types.ModuleType("graphiti_core")
    package.__path__ = []
    with patch.dict(
        sys.modules,
        {
            "graphiti_core": package,
            "graphiti_core.search": search,
            "graphiti_core.search.search_config_recipes": recipes,
        },
    ):
        yield template, configured


class GraphitiMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # start() is deliberately never called: every collaborator below is in-memory.
        self.memory = GraphitiMemory(Path("/graphiti-test-not-created"), SimpleNamespace())

    @staticmethod
    def episode():
        return {
            "id": "milestone-0001",
            "world_id": "example server / unsafe name",
            "dimension": "minecraft:overworld",
            "payload": {"verified": True, "success": True, "item": "oak_log", "count": 4},
            "observed_at": "2026-10-05T08:00:00Z",
        }

    def test_group_id_is_stable_safe_and_isolates_world_and_dimension(self):
        overworld = group_id("server one", "minecraft:overworld")

        self.assertEqual(overworld, group_id("server one", "minecraft:overworld"))
        self.assertNotEqual(overworld, group_id("server one", "minecraft:the_nether"))
        self.assertNotEqual(overworld, group_id("server two", "minecraft:overworld"))
        self.assertRegex(overworld, r"^minecraft_[0-9a-f]{32}$")
        self.assertIsNotNone(re.fullmatch(r"[A-Za-z0-9_-]+", overworld))

    async def test_ingest_deduplicates_by_stable_name_without_fresh_uuid_and_saves_before_return(self):
        events = []
        responses = iter([
            ([], [], None),
            ([{"uuid": "existing-episode"}], [], None),
        ])

        async def query(*args, **kwargs):
            events.append("query")
            self.assertIn("MATCH (e:Episodic", args[0])
            return next(responses)

        async def add_episode(**kwargs):
            events.append("add_episode")
            return SimpleNamespace(episode=SimpleNamespace(uuid="created-episode"))

        async def save():
            events.append("save")

        driver = SimpleNamespace(execute_query=AsyncMock(side_effect=query))
        graph = SimpleNamespace(
            driver=SimpleNamespace(clone=MagicMock(return_value=driver)),
            add_episode=AsyncMock(side_effect=add_episode),
        )
        embedded = SimpleNamespace(client=SimpleNamespace(save=AsyncMock(side_effect=save)))
        self.memory.graph = graph
        self.memory.embedded = embedded

        episode = self.episode()
        expected_scope = group_id(episode["world_id"], episode["dimension"])
        with fake_graphiti_nodes() as episode_type:
            await self.memory.ingest(episode)
            # Simulate replay from a durable outbox after the graph commit succeeded.
            await self.memory.ingest(episode)

        self.assertEqual(events, ["query", "add_episode", "save", "query", "save"])
        graph.add_episode.assert_awaited_once()
        args = graph.add_episode.await_args.kwargs
        self.assertEqual(args["name"], "minecraft:milestone-0001")
        self.assertEqual(args["group_id"], expected_scope)
        self.assertEqual(args["source"], episode_type.json)
        self.assertNotIn("uuid", args, "Graphiti rejects an unseen caller-supplied UUID")
        self.assertEqual(args["reference_time"], datetime(2026, 10, 5, 8, tzinfo=timezone.utc))
        self.assertFalse(args["update_communities"])
        self.assertEqual(embedded.client.save.await_count, 2)

        graph.driver.clone.assert_called_with(database=expected_scope)
        for call in driver.execute_query.await_args_list:
            self.assertEqual(call.kwargs["name"], "minecraft:milestone-0001")
            self.assertEqual(call.kwargs["group_id"], expected_scope)

    async def test_search_is_scoped_to_current_world_and_filters_invalid_or_expired_facts(self):
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        events = []

        async def initialize_scope():
            events.append("index-ready")

        async def search_graph(*args, **kwargs):
            events.append("search")
            return SimpleNamespace(edges=edges, nodes=[], episodes=[])

        edges = [
            SimpleNamespace(
                fact="An oak grove was verified at 10,64,-4.",
                valid_at=now,
                created_at=now,
                invalid_at=None,
                expired_at=None,
            ),
            SimpleNamespace(
                fact="The old tree remains here.",
                valid_at=now,
                created_at=now,
                invalid_at=now,
                expired_at=None,
            ),
            SimpleNamespace(
                fact="The temporary route is usable.",
                valid_at=now,
                created_at=now,
                invalid_at=None,
                expired_at=now,
            ),
        ]
        driver = SimpleNamespace(_init_task=asyncio.create_task(initialize_scope()))
        self.memory.graph = SimpleNamespace(
            driver=SimpleNamespace(clone=MagicMock(return_value=driver)),
            search_=AsyncMock(side_effect=search_graph),
        )
        state = {"world": {"id": "server-a", "dimension": "minecraft:the_nether"}}
        expected_scope = group_id("server-a", "minecraft:the_nether")

        with fake_search_recipe() as (template, configured):
            result = await self.memory.search("where is wood?", state)

        self.memory.graph.driver.clone.assert_called_once_with(database=expected_scope)
        template.model_copy.assert_called_once_with(deep=True)
        self.assertEqual(configured.limit, 5)
        self.assertIsNone(configured.community_config)
        self.memory.graph.search_.assert_awaited_once_with(
            "where is wood?", group_ids=[expected_scope], config=configured
        )
        self.assertEqual(events, ["index-ready", "search"])
        self.assertIn("Graphiti historical memory", result)
        self.assertIn("oak grove", result)
        self.assertNotIn("old tree", result)
        self.assertNotIn("temporary route", result)

    async def test_search_keeps_verified_node_and_raw_milestone_when_no_edge_facts_exist(self):
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        result = SimpleNamespace(
            edges=[],
            nodes=[
                SimpleNamespace(
                    name="oak grove at 10,64,-4",
                    summary="Verified progress: mined 4 oak logs successfully.",
                    created_at=now,
                )
            ],
            episodes=[
                SimpleNamespace(
                    valid_at=now,
                    content='{"verified": true, "success": true, "item": "oak_log", "count": 4}',
                )
            ],
        )
        driver = SimpleNamespace(_init_task=None)
        self.memory.graph = SimpleNamespace(
            driver=SimpleNamespace(clone=MagicMock(return_value=driver)),
            search_=AsyncMock(return_value=result),
        )
        state = {"world": {"id": "server-a", "dimension": "minecraft:overworld"}}

        with fake_search_recipe():
            memory = await self.memory.search("what verified oak work was completed?", state)

        self.assertIn("oak grove at 10,64,-4", memory)
        self.assertIn("Verified progress: mined 4 oak logs successfully.", memory)
        self.assertIn('"verified": true', memory)
        self.assertIn('"count": 4', memory)
        self.memory.graph.search_.assert_awaited_once()

    async def test_search_skips_when_ingestion_owns_the_single_writer_lock(self):
        self.memory.graph = SimpleNamespace(search=AsyncMock())
        await self.memory._operation_lock.acquire()
        try:
            result = await self.memory.search(
                "where is wood?",
                {"world": {"id": "server-a", "dimension": "minecraft:overworld"}},
            )
        finally:
            self.memory._operation_lock.release()

        self.assertEqual(result, "")
        self.memory.graph.search.assert_not_awaited()

    async def test_close_saves_and_stops_embedded_server_then_closes_all_clients(self):
        events = []

        async def shutdown(*args, **kwargs):
            events.append("shutdown")

        async def close_graph():
            events.append("graph.close")

        async def close_embedded():
            events.append("embedded.close")

        async def close_api():
            events.append("api.close")

        embedded = SimpleNamespace(
            client=SimpleNamespace(shutdown=AsyncMock(side_effect=shutdown)),
            close=AsyncMock(side_effect=close_embedded),
        )
        graph = SimpleNamespace(close=AsyncMock(side_effect=close_graph))
        api = SimpleNamespace(close=AsyncMock(side_effect=close_api))
        lock_file = MagicMock()
        lock_file.close.side_effect = lambda: events.append("lock.close")
        self.memory.embedded = embedded
        self.memory.graph = graph
        self.memory.api = api
        self.memory._lock_file = lock_file

        await self.memory.close()

        embedded.client.shutdown.assert_awaited_once_with(save=True)
        graph.close.assert_awaited_once()
        embedded.close.assert_awaited_once()
        api.close.assert_awaited_once()
        lock_file.close.assert_called_once()
        self.assertEqual(
            events,
            ["shutdown", "graph.close", "embedded.close", "api.close", "lock.close"],
        )
        self.assertIsNone(self.memory.graph)
        self.assertIsNone(self.memory.embedded)
        self.assertIsNone(self.memory.api)
        self.assertIsNone(self.memory._lock_file)


if __name__ == "__main__":
    unittest.main()
