"""Controller, bridge, and graph-memory contracts without live Minecraft I/O.

These tests deliberately use only in-memory fakes.  They exercise the path
where bridge state observations are handed to ``WorldMemory.observe`` and the
controller records task outcome semantics, without creating a WorldMemory
database or starting Graphiti.
"""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agent import graph as graph_module
from agent.bridge import MineflayerBridge
from agent.graph import MinecraftAgentGraph
from main import execute_recorded_task, run_planner_objective


class RecordingMemory:
    """Small synchronous WorldMemory-shaped recorder used by controller tests."""

    def __init__(self, contexts=()):
        self.observations = []
        self.task_records = []
        self.context_calls = []
        self._contexts = iter(contexts)

    def observe(self, state):
        self.observations.append(state)

    def record_task(self, objective, task, result, state, requester=None):
        self.task_records.append({
            "objective": objective,
            "task": task,
            "result": result,
            "state": state,
            "requester": requester,
        })

    async def context(self, state, objective):
        self.context_calls.append((state, objective))
        return next(self._contexts, "")


def planner_state(objective="collect oak logs"):
    return {
        "objective": objective,
        "bot_state": {},
        "code": "",
        "execution_result": None,
        "error_trace": "",
        "retry_count": 0,
        "rag_info": "",
        "memory_context": "",
        "status": "started",
    }


class ControllerMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_write_failure_does_not_change_verified_task_result(self):
        state = {"ready": True}
        result = {"success": True, "verified": True, "message": "Collected 2 logs."}
        memory = SimpleNamespace(record_task=Mock(side_effect=OSError("disk full")))
        bridge = SimpleNamespace(get_state=AsyncMock(return_value=state),
                                 execute_task=AsyncMock(return_value=result), latest_state=state)
        with self.assertLogs("MainController", level="ERROR"):
            returned = await execute_recorded_task(SimpleNamespace(memory=memory), bridge,
                                                   "mine 2 logs", {"name": "mine_logs"})
        self.assertIs(returned, result)
        memory.record_task.assert_called_once()

    async def test_verified_typed_task_is_recorded_with_postcondition_result(self):
        before = {"ready": True, "inventory": []}
        after = {"ready": True, "inventory": [{"name": "oak_log", "count": 3}]}
        result = {
            "success": True,
            "verified": True,
            "message": "Collected exactly 3 oak logs.",
            "data": {"requested": 3, "before": 0, "after": 3},
        }
        task = {"name": "mine_logs", "args": {"item": "oak_log", "count": 3}}
        memory = RecordingMemory()
        bridge = SimpleNamespace(
            get_state=AsyncMock(return_value=before),
            execute_task=AsyncMock(return_value=result),
            latest_state=after,
        )
        agent_graph = SimpleNamespace(memory=memory)

        returned = await execute_recorded_task(
            agent_graph, bridge, "mine 3 oak logs", task, requester="Alex"
        )

        self.assertIs(returned, result)
        bridge.get_state.assert_awaited_once()
        bridge.execute_task.assert_awaited_once_with(task)
        self.assertEqual(len(memory.task_records), 1)
        saved = memory.task_records[0]
        self.assertEqual(saved["objective"], "mine 3 oak logs")
        self.assertEqual(saved["task"], task)
        self.assertIs(saved["result"], result)
        self.assertTrue(saved["result"]["success"])
        self.assertTrue(saved["result"]["verified"])
        self.assertIs(saved["state"], after)
        self.assertEqual(saved["requester"], "Alex")

    async def test_interrupted_typed_task_is_persisted_as_unverified_before_reraising(self):
        before = {"ready": True, "world": {"sessionId": "before-interruption"}}
        task = {"name": "give_item", "args": {"item": "oak_log", "count": 2, "recipient": "Alex"}}
        memory = RecordingMemory()
        bridge = SimpleNamespace(
            get_state=AsyncMock(return_value=before),
            execute_task=AsyncMock(side_effect=TimeoutError("node request timed out")),
            latest_state={},
        )
        agent_graph = SimpleNamespace(memory=memory)

        with self.assertRaisesRegex(TimeoutError, "node request timed out"):
            await execute_recorded_task(agent_graph, bridge, "give 2 logs to Alex", task, requester="Alex")

        self.assertEqual(len(memory.task_records), 1)
        saved = memory.task_records[0]
        self.assertEqual(saved["objective"], "give 2 logs to Alex")
        self.assertEqual(saved["task"], task)
        self.assertFalse(saved["result"]["success"])
        self.assertFalse(saved["result"]["verified"])
        self.assertIn("Interrupted/unknown outcome", saved["result"]["message"])
        # The only reliable observation on an interrupted request is the one
        # captured before dispatch; a stale/empty latest_state is not used.
        self.assertIs(saved["state"], before)
        self.assertEqual(saved["requester"], "Alex")

    async def test_raw_planner_completion_is_recorded_as_unverified(self):
        final = {
            **planner_state("build a shelter"),
            "status": "unverified",
            "bot_state": {"ready": True, "position": {"x": 2, "y": 64, "z": 8}},
        }
        latest = {"ready": True, "world": {"sessionId": "fresh-state"}}
        memory = RecordingMemory()
        bridge = SimpleNamespace(latest_state=latest)
        agent_graph = SimpleNamespace(
            graph=SimpleNamespace(ainvoke=AsyncMock(return_value=final)),
            memory=memory,
            bridge=bridge,
        )

        returned = await run_planner_objective(agent_graph, "build a shelter", requester="Alex")

        self.assertIs(returned, final)
        agent_graph.graph.ainvoke.assert_awaited_once()
        self.assertEqual(len(memory.task_records), 1)
        saved = memory.task_records[0]
        self.assertIsNone(saved["task"])
        self.assertTrue(saved["result"]["success"], "raw bridge success is a completed attempt")
        self.assertFalse(saved["result"]["verified"], "raw generated JavaScript is never task proof")
        self.assertEqual(saved["result"]["status"], "unverified")
        self.assertIn("not verified", saved["result"]["message"])
        self.assertIs(saved["state"], latest)
        self.assertEqual(saved["requester"], "Alex")


class BridgeStateRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_state_message_types_refresh_cache_and_memory_observer(self):
        bridge = MineflayerBridge()
        memory = RecordingMemory()
        bridge.state_callbacks.append(memory.observe)

        update = {"ready": True, "observedAt": "2026-10-05T10:00:00Z"}
        response = {"ready": True, "observedAt": "2026-10-05T10:00:01Z"}
        execution_state = {"ready": True, "observedAt": "2026-10-05T10:00:02Z"}
        loop = asyncio.get_running_loop()

        await bridge._route_message({"type": "state_update", "data": update})
        self.assertIs(bridge.latest_state, update)

        state_future = loop.create_future()
        bridge.pending_requests["state-request"] = state_future
        await bridge._route_message({
            "type": "state_response", "id": "state-request", "data": response,
        })
        self.assertIs(await state_future, response)
        self.assertIs(bridge.latest_state, response)

        execution_future = loop.create_future()
        bridge.pending_requests["task-request"] = execution_future
        task_result = {"success": True, "verified": True}
        await bridge._route_message({
            "type": "execution_result",
            "id": "task-request",
            "result": task_result,
            "currentState": execution_state,
        })
        self.assertIs(await execution_future, task_result)
        self.assertIs(bridge.latest_state, execution_state)
        self.assertEqual(memory.observations, [update, response, execution_state])

    async def test_late_response_and_execution_checkpoint_are_still_recorded(self):
        """A timed-out caller must not make a later world checkpoint disappear."""
        bridge = MineflayerBridge()
        memory = RecordingMemory()
        bridge.state_callbacks.append(memory.observe)
        late_state = {"ready": True, "observedAt": "2026-10-05T10:01:00Z"}
        late_execution_state = {"ready": True, "observedAt": "2026-10-05T10:01:01Z"}

        # No pending future emulates a request already removed by _request's
        # timeout cleanup. The response still contains useful state evidence.
        await bridge._route_message({
            "type": "state_response", "id": "expired-state-request", "data": late_state,
        })
        self.assertIs(bridge.latest_state, late_state)

        await bridge._route_message({
            "type": "execution_result",
            "id": "expired-task-request",
            "result": {"success": True, "verified": True},
            "currentState": late_execution_state,
        })
        self.assertIs(bridge.latest_state, late_execution_state)
        self.assertEqual(memory.observations, [late_state, late_execution_state])


class GraphMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_observe_node_adds_memory_context_for_the_live_state(self):
        observed = {"ready": True, "world": {"sessionId": "session-1"}}
        memory = RecordingMemory(["- verified oak grove at 12,64,-3"])
        graph_agent = object.__new__(MinecraftAgentGraph)
        graph_agent.bridge = SimpleNamespace(get_state=AsyncMock(return_value=observed))
        graph_agent.memory = memory
        state = planner_state("find wood")

        returned = await graph_agent.observe_node(state)

        self.assertIs(returned, state)
        graph_agent.bridge.get_state.assert_awaited_once()
        self.assertEqual(memory.context_calls, [(observed, "find wood")])
        self.assertIs(state["bot_state"], observed)
        self.assertEqual(state["memory_context"], "- verified oak grove at 12,64,-3")
        self.assertEqual(state["status"], "observed")

    async def test_memory_lookup_failure_retains_fresh_live_observation(self):
        observed = {"ready": True, "world": {"sessionId": "session-1"}, "position": {"x": 4}}
        memory = RecordingMemory()
        memory.context = AsyncMock(side_effect=ConnectionError("local model is offline"))
        graph_agent = object.__new__(MinecraftAgentGraph)
        graph_agent.bridge = SimpleNamespace(get_state=AsyncMock(return_value=observed))
        graph_agent.memory = memory
        state = planner_state("find wood")

        returned = await graph_agent.observe_node(state)

        self.assertIs(returned, state)
        graph_agent.bridge.get_state.assert_awaited_once()
        memory.context.assert_awaited_once_with(observed, "find wood")
        self.assertIs(state["bot_state"], observed)
        self.assertEqual(state["status"], "observed")
        self.assertEqual(state["memory_context"], "")

    async def test_retry_reobserves_world_and_refreshes_memory_before_next_attempt(self):
        first_state = {"ready": True, "world": {"sessionId": "session-1"}, "position": {"x": 0}}
        second_state = {"ready": True, "world": {"sessionId": "session-2"}, "position": {"x": 9}}
        memory = RecordingMemory(["first memory", "fresh memory"])
        bridge = SimpleNamespace(
            get_state=AsyncMock(side_effect=[first_state, second_state]),
            execute_code=AsyncMock(side_effect=[
                {"success": False, "errorStack": "first attempt failed"},
                {"success": True},
            ]),
        )
        graph_agent = object.__new__(MinecraftAgentGraph)
        graph_agent.bridge = bridge
        graph_agent.memory = memory
        graph_agent.nebius = SimpleNamespace(
            generate_response=AsyncMock(side_effect=["// first attempt", "// corrected attempt"]),
        )
        graph_agent.jarvis = SimpleNamespace(query_knowledge=AsyncMock(return_value="retry guidance"))
        graph_agent.tavily = SimpleNamespace(search_minecraft_wiki=AsyncMock(return_value=""))
        graph_agent.graph = graph_agent._build_graph()

        # The first sandbox attempt fails. The conditional graph edge must return
        # to observe, rather than reusing the first state/memory snapshot.
        with patch.object(graph_module.config, "max_retries", 2), patch.object(
            graph_module.config, "enable_tavily", False
        ):
            final = await graph_agent.graph.ainvoke(planner_state("collect wood"))

        self.assertEqual(bridge.get_state.await_count, 2)
        self.assertEqual(memory.context_calls, [
            (first_state, "collect wood"),
            (second_state, "collect wood"),
        ])
        self.assertEqual(bridge.execute_code.await_count, 2)
        self.assertEqual(final["status"], "unverified")
        self.assertIs(final["bot_state"], second_state)
        self.assertEqual(final["memory_context"], "fresh memory")
        graph_agent.jarvis.query_knowledge.assert_awaited_once()

    async def test_no_code_fails_and_terminates_at_retry_limit_without_execution(self):
        graph_agent = object.__new__(MinecraftAgentGraph)
        graph_agent.bridge = SimpleNamespace(execute_code=AsyncMock())
        state = planner_state("collect wood")

        with patch.object(graph_module.config, "max_retries", 1):
            returned = await graph_agent.execute_code_node(state)
            route = graph_agent.should_retry_or_end(returned)

        self.assertIs(returned, state)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["retry_count"], 1)
        self.assertEqual(state["error_trace"], "No code was generated.")
        self.assertEqual(route, "max_retries_reached")
        graph_agent.bridge.execute_code.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
