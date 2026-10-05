"""Headless chat-only controller-loop contracts, with no live I/O."""

import asyncio
import signal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import main as controller


class FakeBridge:
    def __init__(self, active_client=None):
        self.active_client = active_client
        self.event_callbacks = []


class AsyncioProxy:
    """Patch only the controller's asyncio reference while retaining its API."""

    def __init__(self, sleep):
        self._sleep = sleep

    async def sleep(self, *args, **kwargs):
        return await self._sleep(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(asyncio, name)


async def wait_until(predicate, attempts=100):
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached before the test yielded control")


class ChatOnlyLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_only_waits_for_connection_then_stays_alive_without_terminal_input(self):
        bridge = FakeBridge()
        wait_started = asyncio.Event()
        permit_connection_check = asyncio.Event()

        async def controlled_sleep(_seconds):
            # The first sleep is the controller's "wait for bot" loop. After
            # connection, any sleep used to keep headless mode alive must also
            # remain cancellable rather than returning and spinning.
            if not permit_connection_check.is_set():
                wait_started.set()
                await permit_connection_check.wait()
                return
            await asyncio.Future()

        proxy = AsyncioProxy(controlled_sleep)
        graph = SimpleNamespace()
        terminal_input = MagicMock(side_effect=AssertionError("chat-only must not read stdin"))
        to_thread = AsyncMock(side_effect=AssertionError("chat-only must not schedule stdin"))

        with patch.object(controller, "MinecraftAgentGraph", return_value=graph) as graph_class, patch.object(
            controller, "asyncio", proxy
        ), patch("builtins.input", terminal_input), patch.object(controller.asyncio, "to_thread", to_thread):
            loop_task = asyncio.create_task(controller.run_agent_loop(bridge, memory=None, chat_only=True))
            try:
                await asyncio.wait_for(wait_started.wait(), timeout=0.5)
                self.assertEqual(
                    bridge.event_callbacks,
                    [],
                    "the chat handler must not be installed before a bot connects",
                )

                bridge.active_client = object()
                permit_connection_check.set()
                await wait_until(lambda: len(bridge.event_callbacks) == 1)

                graph_class.assert_called_once_with(bridge, memory=None)
                self.assertTrue(callable(bridge.event_callbacks[0]))
                self.assertFalse(loop_task.done(), "chat-only mode must remain alive until cancelled")
                terminal_input.assert_not_called()
                to_thread.assert_not_called()
            finally:
                loop_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await loop_task

    async def test_normal_mode_keeps_terminal_cli_path_and_quit_behavior(self):
        bridge = FakeBridge(active_client=object())
        graph = SimpleNamespace()
        terminal_input = MagicMock(name="input")
        to_thread = AsyncMock(return_value="quit")

        with patch.object(controller, "MinecraftAgentGraph", return_value=graph) as graph_class, patch(
            "builtins.input", terminal_input
        ), patch.object(controller.asyncio, "to_thread", to_thread):
            # Omitting the new flag verifies the legacy interactive default.
            await controller.run_agent_loop(bridge, memory=None)

        graph_class.assert_called_once_with(bridge, memory=None)
        to_thread.assert_awaited_once_with(terminal_input, "Agent Objective > ")
        self.assertEqual(len(bridge.event_callbacks), 1)

    async def test_chat_only_sigterm_cancels_main_and_closes_server_and_memory(self):
        """The supervised launcher can stop the headless controller cleanly."""
        signal_loop = SimpleNamespace(
            add_signal_handler=MagicMock(),
            remove_signal_handler=MagicMock(),
        )
        proxy = AsyncioProxy(asyncio.sleep)
        proxy.get_running_loop = MagicMock(return_value=signal_loop)
        server = SimpleNamespace(close=MagicMock(), wait_closed=AsyncMock())
        bridge = SimpleNamespace(
            state_callbacks=[],
            event_callbacks=[],
            start=AsyncMock(return_value=server),
        )
        memory = SimpleNamespace(
            observe=MagicMock(),
            record_event=MagicMock(),
            start=MagicMock(),
            close=AsyncMock(),
        )
        controller_settings = SimpleNamespace(
            memory_enabled=True,
            ws_host="127.0.0.1",
            ws_port=8765,
        )
        loop_started = asyncio.Event()

        async def wait_for_cancellation(*_args, **_kwargs):
            loop_started.set()
            await asyncio.Future()

        with patch.object(controller, "asyncio", proxy), patch.object(
            controller, "config", controller_settings
        ), patch.object(controller, "MineflayerBridge", return_value=bridge) as bridge_class, patch.object(
            controller, "WorldMemory", return_value=memory
        ) as memory_class, patch.object(
            controller, "run_agent_loop", AsyncMock(side_effect=wait_for_cancellation)
        ) as run_loop:
            main_task = asyncio.create_task(controller.main(chat_only=True))
            await asyncio.wait_for(loop_started.wait(), timeout=0.5)

            signal_loop.add_signal_handler.assert_called_once()
            registered_signal, cancel_controller = signal_loop.add_signal_handler.call_args.args
            self.assertEqual(registered_signal, signal.SIGTERM)
            cancel_controller()

            with self.assertRaises(asyncio.CancelledError):
                await main_task

        bridge_class.assert_called_once_with(host="127.0.0.1", port=8765)
        memory_class.assert_called_once_with(controller_settings)
        self.assertEqual(bridge.state_callbacks, [memory.observe])
        self.assertEqual(bridge.event_callbacks, [memory.record_event])
        memory.start.assert_called_once()
        run_loop.assert_awaited_once_with(bridge, memory, chat_only=True)
        server.close.assert_called_once()
        server.wait_closed.assert_awaited_once()
        memory.close.assert_awaited_once()
        signal_loop.remove_signal_handler.assert_called_once_with(signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
