import unittest

from agent.dialogue import classify_message
from agent.intents import parse_resource_task, parse_resource_plan, PlanError
from main import execute_chat_objective
import test_dialogue


class ResourcePlanTests(unittest.IsolatedAsyncioTestCase):
    def test_live_stacked_request_retains_all_resources_and_chest(self):
        text = "Can you get 10 blocks of oak wood logs and 10 dirt as well as 10 cobble stone and add it to the chest"
        self.assertIsNone(parse_resource_task(text, "Pilot6117"))
        task = parse_resource_plan(text, "Pilot6117")
        self.assertEqual(task["name"], "execute_plan")
        steps = task["args"]["steps"]
        self.assertEqual([step["args"]["item"] for step in steps], ["oak_log", "dirt", "cobblestone"] * 2)
        self.assertEqual([step["args"]["count"] for step in steps], [10] * 6)
        self.assertEqual([step["name"] for step in steps], ["mine_logs", "mine_resource", "mine_resource"] + ["deposit_item"] * 3)
        self.assertTrue(all(step["args"].get("collection_mode") == "ensure_inventory" for step in steps[:3]))

    def test_each_resource_keeps_its_quantity(self):
        plan = parse_resource_plan("get 2 oak logs, 4 dirt and 7 cobblestone and put them in the chest")
        self.assertEqual([step["args"]["count"] for step in plan["args"]["steps"]], [2, 4, 7, 2, 4, 7])
        plan = parse_resource_plan("mine 2 oak logs and 3 cobblestone")
        self.assertEqual(len(plan["args"]["steps"]), 2)
        self.assertNotIn("collection_mode", plan["args"]["steps"][0]["args"])

    def test_duplicate_goals_merge_without_double_counting(self):
        plan = parse_resource_plan("get 2 oak logs and 3 oak logs and put them in the chest")
        self.assertEqual([step["args"]["count"] for step in plan["args"]["steps"]], [5, 5])

    def test_unsupported_or_ambiguous_batches_never_partially_parse(self):
        for text in ["get 2 logs and 3 wheat and put them in the chest", "get 0 logs and 3 dirt",
                     "get 1000 logs and 2 cobblestone", "get 60 logs and 60 logs", "get 2 logs and 3 dirt then craft a chest",
                     "get 2 logs and 3 dirt and give them to Alex", "get 2 logs and build 3 dirt walls"]:
            with self.subTest(text=text), self.assertRaises(PlanError):
                parse_resource_plan(text)
            self.assertEqual(classify_message(text)["kind"], "unsupported")
            self.assertIsNone(parse_resource_task(text))
        self.assertIsNone(parse_resource_task("get 2 logs and dirt and put it in the chest"))
        self.assertIsNone(parse_resource_task("get 2 logs and wheat"))
        self.assertIsNone(parse_resource_task("get 2 logs and attack a zombie"))

    async def test_unknown_batch_never_calls_bot_or_model(self):
        graph, bridge, lock = test_dialogue.DialogueTests().make_controller()
        await execute_chat_objective(graph, bridge, "get 2 logs and 3 wheat and put them in the chest", "Alex", lock)
        bridge.execute_task.assert_not_awaited()
        graph.graph.ainvoke.assert_not_awaited()
        self.assertIn("Nothing has started", bridge.send_chat.call_args.args[0])

    async def test_supported_batch_goes_through_recorded_typed_tool(self):
        graph, bridge, lock = test_dialogue.DialogueTests().make_controller()
        await execute_chat_objective(graph, bridge, "get 2 logs and 3 dirt and put them in the chest", "Alex", lock)
        bridge.execute_task.assert_awaited_once()
        self.assertEqual(bridge.execute_task.call_args.args[0]["name"], "execute_plan")
        graph.graph.ainvoke.assert_not_awaited()

    def test_acknowledgement_is_not_an_order_or_override(self):
        self.assertEqual(classify_message("I verified the Deposit thank you!")["kind"], "conversation")
        self.assertEqual(parse_resource_task("get 2 dirt")["name"], "mine_resource")


if __name__ == "__main__":
    unittest.main()
