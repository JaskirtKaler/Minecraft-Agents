"""Addressed chat must select one agent and never schedule work for bot replies."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import main as controller
from agent.bridge import MineflayerBridge
from agent.chat_routing import addressed_command


class ChatRoutingTests(unittest.TestCase):
    def route(self, text, sender="Pilot6117", agent="AI_Agent_2"):
        return addressed_command(sender, text, bot_username=agent, allowed_players=["Pilot6117", "Friend"])

    def test_exact_case_insensitive_target_and_command_body(self):
        for text, expected in (("@AI_Agent_2 mine 3 oak logs", "mine 3 oak logs"),
                               ("  @ai_AGENT_2: inventory  ", "inventory"),
                               ("@AI_Agent_2 stop", "stop"),
                               ("@AI_Agent_2 16", "16")):
            with self.subTest(text=text):
                self.assertEqual(self.route(text, sender="pilot6117"), expected)

    def test_general_chat_other_agents_prefix_collisions_and_empty_commands_are_ignored(self):
        for text in ("hello", "status", "stop", "inventory", "@AI_Agent status", "@AI_Agent_20 status",
                     "hello @AI_Agent_2 stop", "@AI_Agent_2", "@AI_Agent_2: ", "@AI_Agent_2stop",
                     "@AI_Agent_2, stop", "<Pilot6117> @AI_Agent_2 stop"):
            with self.subTest(text=text):
                self.assertIsNone(self.route(text))

    def test_bot_messages_and_unlisted_players_cannot_trigger_even_addressed_commands(self):
        for sender in ("AI_Agent", "AI_Agent_2", "CustomBot", "Guest", None):
            with self.subTest(sender=sender):
                self.assertIsNone(self.route("@AI_Agent_2 stop", sender=sender))
        self.assertEqual(self.route("@AI_Agent_2 status", sender="Friend"), "status")
        self.assertIsNone(self.route(None))


class ChatRoutingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_to_two_controllers_schedules_only_selected_human_command(self):
        graphs = [SimpleNamespace(), SimpleNamespace()]
        bridges = [SimpleNamespace(active_client=object(), event_callbacks=[]) for _ in graphs]
        loops = []
        handler = AsyncMock()
        with patch.object(controller, "execute_chat_objective", handler):
            try:
                for name, graph, bridge in zip(("AI_Agent", "AI_Agent_2"), graphs, bridges):
                    settings = controller.config.model_copy(update={"mc_username": name, "mc_command_players": ["Pilot6117"]})
                    with patch.object(controller, "config", settings):
                        loops.append(asyncio.create_task(controller._run_agent_loop(graph, bridge, None, True)))
                        for _ in range(100):
                            if bridge.event_callbacks:
                                break
                            await asyncio.sleep(0)
                        self.assertTrue(bridge.event_callbacks)
                for sender, text in (("Pilot6117", "hello everyone"), ("Pilot6117", "@AI_Agent_2 status"),
                                     ("AI_Agent", "@AI_Agent_2 collect wood"),
                                     ("AI_Agent_2", "@AI_Agent stop"),
                                     ("Pilot6117", "@AI_Agent inventory")):
                    event = {"event": "chat", "data": {"username": sender, "message": text}}
                    for bridge in bridges:
                        bridge.event_callbacks[0](event)
                await asyncio.sleep(0)
                self.assertEqual(handler.await_count, 2)
                self.assertEqual([call.args[:4] for call in handler.await_args_list],
                                 [(graphs[1], bridges[1], "status", "Pilot6117"),
                                  (graphs[0], bridges[0], "inventory", "Pilot6117")])
            finally:
                for loop in loops:
                    loop.cancel()
                await asyncio.gather(*loops, return_exceptions=True)

    async def test_second_bot_cannot_replace_an_existing_bridge_client(self):
        bridge = MineflayerBridge()
        original = object()
        bridge.active_client = original
        duplicate = SimpleNamespace(close=AsyncMock())
        await bridge._handle_client(duplicate)
        self.assertIs(bridge.active_client, original)
        self.assertEqual(duplicate.close.await_args.kwargs["code"], 1008)


if __name__ == "__main__":
    unittest.main()
