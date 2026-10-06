import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agent.dialogue import classify_message, inventory_reply
from agent.intents import parse_resource_task
from main import execute_chat_objective


class DialogueTests(unittest.IsolatedAsyncioTestCase):
    def make_controller(self):
        result = {"success": True, "verified": True, "message": "Deposited exactly 3 cobblestone in the chest."}
        bridge = SimpleNamespace(
            get_state=AsyncMock(return_value={"ready": True}), latest_state={"ready": True},
            execute_task=AsyncMock(return_value=result), send_chat=AsyncMock(), cancel_task=AsyncMock(),
            get_knowledge=AsyncMock(return_value={
                "found": True, "name": "stone", "version": "1.20.1",
                "dropCandidates": [{"item": "cobblestone", "noSilkTouch": True}],
                "requiredTools": ["wooden_pickaxe"],
            }),
        )
        graph = SimpleNamespace(memory=None, graph=SimpleNamespace(ainvoke=AsyncMock()))
        return graph, bridge, asyncio.Lock()

    def test_cobblestone_and_chest_contract(self):
        task = parse_resource_task("get 3 cobble stone and drop it in the chest", "Alex")
        self.assertEqual(task["name"], "mine_and_deposit")
        self.assertEqual(task["args"]["item"], "cobblestone")
        self.assertEqual(parse_resource_task("put 2 oak logs in the chest")["name"], "deposit_item")

    async def test_missing_quantity_then_reply_preserves_destination(self):
        graph, bridge, lock = self.make_controller()
        await execute_chat_objective(graph, bridge, "get some cobble stone and drop it in the chest", "Alex", lock)
        bridge.execute_task.assert_not_awaited()
        self.assertIn("How many", bridge.send_chat.call_args.args[0])
        await execute_chat_objective(graph, bridge, "3", "Alex", lock)
        request = bridge.execute_task.call_args.args[0]
        self.assertEqual(request["name"], "mine_and_deposit")
        self.assertEqual(request["args"]["count"], 3)
        graph.graph.ainvoke.assert_not_awaited()

    async def test_correction_is_grounded_question_not_new_job(self):
        graph, bridge, lock = self.make_controller()
        await execute_chat_objective(graph, bridge, "when you mine stone it becomes cobblestone", "Alex", lock)
        bridge.get_knowledge.assert_awaited_once_with("stone")
        bridge.execute_task.assert_not_awaited()
        graph.graph.ainvoke.assert_not_awaited()
        self.assertIn("not starting another", bridge.send_chat.call_args_list[0].args[0])

    async def test_status_and_stop_respond_while_task_lock_is_held(self):
        graph, bridge, lock = self.make_controller()
        graph.dialogue = {"active": "get 3 cobblestone", "phase": "walking", "pending": {}, "last": {}}
        await lock.acquire()
        try:
            await asyncio.wait_for(execute_chat_objective(graph, bridge, "is something wrong?", "Alex", lock), 0.2)
            self.assertIn("walking", bridge.send_chat.call_args.args[0])
            await asyncio.wait_for(execute_chat_objective(graph, bridge, "Jarvis stop", "Alex", lock), 0.2)
            bridge.cancel_task.assert_awaited_once()
        finally:
            lock.release()

    async def test_unsupported_and_negated_requests_never_execute(self):
        for text in ["farm 8 wheat", "don't mine 3 cobblestone", "get 3 logs then craft a chest"]:
            graph, bridge, lock = self.make_controller()
            await execute_chat_objective(graph, bridge, text, "Alex", lock)
            bridge.execute_task.assert_not_awaited()
            graph.graph.ainvoke.assert_not_awaited()
            bridge.send_chat.assert_awaited()

    def test_knowledge_questions_do_not_become_get_commands(self):
        self.assertEqual(classify_message("how do I get cobblestone?")["kind"], "knowledge")
        self.assertEqual(classify_message("how do I farm wheat?")["subject"], "wheat")

    async def test_inventory_is_fresh_and_available_while_busy(self):
        graph, bridge, lock = self.make_controller()
        bridge.latest_state = {"ready": True, "inventory": []}  # Deliberately stale.
        bridge.get_state.return_value = {
            "ready": True, "username": "AI_Agent", "observedAt": "2026-10-06T18:00:00Z",
            "inventory": [{"name": "cobblestone", "count": 12, "slot": 41}],
            "equipment": {"held": {"name": "wooden_pickaxe"}, "offhand": "torch"},
        }
        await lock.acquire()
        try:
            with patch("main.asyncio.sleep", new_callable=AsyncMock) as pace:
                await asyncio.wait_for(execute_chat_objective(graph, bridge, "Jarvis inventory", "Pilot6117", lock), 0.2)
                self.assertEqual(pace.await_count, 2)
                pace.assert_awaited_with(0.6)
        finally:
            lock.release()
        bridge.get_state.assert_awaited_once()
        bridge.execute_task.assert_not_awaited()
        graph.graph.ainvoke.assert_not_awaited()
        replies = [call.args[0] for call in bridge.send_chat.call_args_list]
        self.assertTrue(any("cobblestone x12 [slot 41]" in reply for reply in replies))
        self.assertTrue(any("wooden_pickaxe" in reply and "offhand=torch" in reply for reply in replies))
        self.assertTrue(all(len(reply) <= 220 for reply in replies))

    def test_inventory_unavailable_is_not_empty_and_long_snapshot_is_chunked(self):
        self.assertIn("not an empty-inventory report", inventory_reply({"ready": False})[0])
        snapshot = {"ready": True, "inventory": [
            {"name": f"long_named_item_{slot}", "count": 64, "slot": slot} for slot in range(36)
        ]}
        replies = inventory_reply(snapshot)
        self.assertTrue(all(len(reply) <= 220 for reply in replies))
        for item in snapshot["inventory"]:
            self.assertIn(f"{item['name']} x64 [slot {item['slot']}]", " ".join(replies))
        for text in ["inventory", "show your inventory", "what are you carrying?", "Jarvis show me your inventory"]:
            self.assertEqual(classify_message(text)["kind"], "inventory")

    async def test_escape_is_a_typed_task_not_generated_code(self):
        graph, bridge, lock = self.make_controller()
        await execute_chat_objective(graph, bridge, "Hi Jarvis can you mine a staircase up 4 blocks?", "Alex", lock)
        bridge.execute_task.assert_awaited_once_with({"name": "escape_staircase", "args": {"rise": 4}})
        graph.graph.ainvoke.assert_not_awaited()
        self.assertEqual(classify_message("don't mine a staircase up 4 blocks")["kind"], "conversation")


if __name__ == "__main__":
    unittest.main()
