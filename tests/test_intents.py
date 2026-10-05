import unittest

from agent.intents import is_explicit_task_request, parse_resource_task


class ResourceTaskIntentTests(unittest.TestCase):
    def test_mine_and_give_to_requester(self):
        task = parse_resource_task("Please mine 8 oak logs and drop them to me", "Pilot6117")
        self.assertEqual(task, {
            "name": "mine_and_give",
            "args": {"item": "oak_log", "count": 8, "max_distance": 48, "recipient": "Pilot6117"},
        })

    def test_mine_wood_maps_to_oak_logs(self):
        task = parse_resource_task("get 10 blocks of oak wood")
        self.assertEqual(task["name"], "mine_logs")
        self.assertEqual(task["args"]["item"], "oak_log")
        self.assertEqual(task["args"]["count"], 10)

    def test_delivery_to_named_player(self):
        task = parse_resource_task("give 3 spruce logs to Alex")
        self.assertEqual(task["name"], "give_item")
        self.assertEqual(task["args"]["recipient"], "Alex")

    def test_complex_task_is_not_partially_executed(self):
        task = parse_resource_task("get 10 oak logs then craft a chest")
        self.assertIsNone(task)

    def test_ordinary_chat_is_not_a_task(self):
        self.assertIsNone(parse_resource_task("is everything okay?"))
        self.assertFalse(is_explicit_task_request("is everything okay?", "AI_Agent"))

    def test_explicit_task_is_admitted_to_planner(self):
        self.assertTrue(is_explicit_task_request("Can you craft a chest?", "AI_Agent"))


if __name__ == "__main__":
    unittest.main()
