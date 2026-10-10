"""
WebSocket Bridge Server (Python side)
Communicates with the Node.js Mineflayer Bot process.
"""

import asyncio
import json
import uuid
import logging
from typing import Dict, Any, Optional, Callable
import websockets
from websockets.server import WebSocketServerProtocol
from agent.observations import validate_observation

logger = logging.getLogger("BridgeServer")
logging.basicConfig(level=logging.INFO)

class MineflayerBridge:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self.active_client: Optional[WebSocketServerProtocol] = None
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.latest_state: Dict[str, Any] = {}
        self._construction_task_id = None
        self.event_callbacks: list[Callable[[Dict[str, Any]], None]] = []
        self.state_callbacks: list[Callable[[Dict[str, Any]], None]] = []

    def _update_state(self, state: Dict[str, Any]):
        self.latest_state = state
        for callback in self.state_callbacks:
            try:
                callback(state)
            except Exception:
                logger.exception("Error saving bot state")

    async def start(self):
        """Starts the WebSocket server."""
        logger.info(f"Starting Mineflayer WebSocket bridge on {self.host}:{self.port}...")
        server = await websockets.serve(self._handle_client, self.host, self.port)
        logger.info("WebSocket bridge server running and awaiting bot connection.")
        return server

    async def _handle_client(self, websocket: WebSocketServerProtocol, path: str = ""):
        """Handles incoming WebSocket connection from Node.js Mineflayer bot."""
        logger.info(f"Mineflayer Bot connected from {websocket.remote_address}")
        self.active_client = websocket

        try:
            async for raw_message in websocket:
                try:
                    message = json.loads(raw_message)
                    await self._route_message(message)
                except json.JSONDecodeError:
                    logger.error(f"Received malformed JSON message: {raw_message}")
        except websockets.exceptions.ConnectionClosedError:
            logger.warning("Mineflayer Bot disconnected.")
        finally:
            if self.active_client is websocket:
                self.active_client = None
                self._construction_task_id = None
                for future in self.pending_requests.values():
                    if not future.done():
                        future.set_exception(ConnectionError("Mineflayer bot disconnected during request."))
                self.pending_requests.clear()

    async def _route_message(self, message: Dict[str, Any]):
        """Routes incoming messages from Node.js bot to pending futures or state cache."""
        msg_type = message.get("type")
        msg_id = message.get("id")

        if msg_type == "state_update":
            self._update_state(message.get("data", {}))
            logger.debug("Received background state update from bot.")

        elif msg_type in {"observation_response", "navigation_response", "construction_response"}:
            future = self.pending_requests.pop(msg_id, None)
            if future and not future.done():
                if message.get('error'):
                    future.set_exception(ValueError(message['error'].get('message', 'Observation request failed.')))
                else:
                    future.set_result(message.get('data', {}))

        elif msg_type in {"knowledge_response", "operation_status"}:
            future = self.pending_requests.pop(msg_id, None)
            if future and not future.done():
                future.set_result(message.get("data", {}))

        elif msg_type == "state_response":
            self._update_state(message.get("data", {}))
            future = self.pending_requests.pop(msg_id, None)
            if future and not future.done():
                future.set_result(message.get("data", {}))

        elif msg_type == "execution_result":
            # A late result can still contain an important world checkpoint,
            # even if the requesting command already timed out.
            if message.get("currentState"):
                self._update_state(message.get("currentState", self.latest_state))
            future = self.pending_requests.pop(msg_id, None)
            if future and not future.done():
                future.set_result(message.get("result", {}))

        elif msg_type == "event":
            logger.info(f"[Bot Event: {message.get('event')}] {message.get('data')}")
            for callback in self.event_callbacks:
                try:
                    callback(message)
                except Exception as e:
                    logger.error(f"Error in event callback: {e}")

    async def get_state(self, timeout: float = 10.0) -> Dict[str, Any]:
        """Requests current game state from Mineflayer bot."""
        return await self._request({"type": "get_state"}, timeout=timeout)

    async def get_observation(self, horizontal_radius: int = 3, vertical_radius: int = 2,
                              timeout: float = 10.0) -> Dict[str, Any]:
        """Read a bounded terrain/motion snapshot without executing tools or inference."""
        for value, maximum, name in ((horizontal_radius, 5, 'horizontal_radius'),
                                     (vertical_radius, 3, 'vertical_radius')):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f'{name} must be an integer from 0 to {maximum}.')
        payload = await self._request({'type': 'get_observation', 'options': {
            'horizontalRadius': horizontal_radius, 'verticalRadius': vertical_radius
        }}, timeout=timeout)
        observation = validate_observation(payload)
        self._update_state(observation['state'])
        return observation

    async def execute_code(
        self,
        code: str,
        timeout: float = 45.0,
        execution_timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Sends a JavaScript code snippet to the bot execution sandbox."""
        payload: Dict[str, Any] = {"type": "execute_code", "code": code}
        if execution_timeout_ms is not None:
            payload["timeoutMs"] = execution_timeout_ms
        return await self._request(payload, timeout=timeout)

    async def get_navigation_frame(self, timeout: float = 10.0):
        """Experimental perception, refused by bots outside disposable RL worlds."""
        from rl.navigation import validate_frame
        frame = validate_frame(await self._request({'type': 'get_navigation_frame'}, timeout=timeout))
        self._update_state(frame['observation']['state'])
        return frame

    async def execute_rl_step(self, action: int, frame, timeout: float = 10.0):
        """One gated navigation option, guarded against stale sessions/positions."""
        if type(action) is not int or not 0 <= action <= 4:
            raise ValueError('RL action must be an integer from 0 to 4.')
        from rl.navigation import validate_frame
        frame = validate_frame(frame)
        if frame['backend'] != 'mineflayer':
            raise ValueError('Synthetic frames cannot authorize Minecraft actions.')
        return await self._request({'type': 'rl_step', 'step': {
            'action': action, 'expectedWorld': frame['observation']['state']['world'],
            'expectedPosition': frame['observation']['terrain']['center']
        }}, timeout=timeout)

    async def execute_task(self, task: Dict[str, Any], timeout: float = 240.0) -> Dict[str, Any]:
        """Runs an allowlisted, verified Mineflayer skill on the Node bot."""
        return await self._request({"type": "execute_task", "task": task}, timeout=timeout)

    async def get_knowledge(self, subject: str) -> Dict[str, Any]:
        return await self._request({"type": "get_knowledge", "subject": subject}, timeout=10)

    async def execute_tool(self, tool: Dict[str, Any], timeout: float | None = None) -> Dict[str, Any]:
        from agent.tool_timing import tool_timeout, POLICY
        if timeout is None:
            timeout = tool_timeout(tool) + POLICY['rpcGraceMs'] / 1000
        # Controller metadata is separate from model-controlled tool args.
        return await self._request({"type": "execute_tool", "tool": tool,
                                    "constructionTaskId": self._construction_task_id}, timeout=timeout)

    async def set_construction_contract(self, contract):
        """Install/clear a trusted frozen outcome, never a model-callable tool."""
        # Forget the token before a revocation request: a failed/disconnected
        # clear must never let the next tool reuse stale repair authority.
        self._construction_task_id = None
        ack = await self._request({'type': 'set_construction_contract', 'contract': contract}, timeout=10)
        if contract is not None:
            if ack.get('active') is not True or ack.get('task_id') != contract['task_id']:
                raise RuntimeError('Construction contract was not acknowledged; no repair authority granted.')
            self._construction_task_id = contract['task_id']
        return ack

    async def construction_status(self):
        return await self._request({'type': 'get_construction_status',
                                    'constructionTaskId': self._construction_task_id}, timeout=10)

    async def wait_for_operation_idle(self, operation_id=None):
        """Passive handshake: never retry an unsettled or unrelated operation."""
        from agent.tool_timing import POLICY
        if not isinstance(operation_id, str) or not operation_id:
            raise RuntimeError('Missing operation identity; cannot authorize continuation')
        async with asyncio.timeout(POLICY['settleMs'] / 1000):
            while True:
                status = await self._request({'type': 'get_operation_status'}, timeout=5)
                if status.get('busy') is False and status.get('active') is False:
                    return status
                if status.get('busy') is not True or status.get('operation_id') != operation_id:
                    raise RuntimeError('Operation status is unknown or belongs to another operation')
                await asyncio.sleep(.25)

    async def cancel_task(self):
        self._construction_task_id = None
        if self.active_client:
            try:
                await self.active_client.send(json.dumps({"type": "cancel_task"}))
            except Exception:
                # Disconnection already revokes the Node lease. Never lose
                # the caller's partial trace by masking its original failure.
                logger.warning('Could not send cancellation on the closing bridge.', exc_info=True)

    async def _request(self, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """Send one correlated request and always discard its future on timeout."""
        if not self.active_client:
            raise ConnectionError("No active Mineflayer bot connected to the WebSocket bridge.")

        req_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = future

        request = {**payload, "id": req_id}
        try:
            await self.active_client.send(json.dumps(request))
            return await asyncio.wait_for(future, timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if payload.get("type") in {"execute_task", "execute_code", "execute_tool", "rl_step"}:
                await self.cancel_task()
            raise
        finally:
            self.pending_requests.pop(req_id, None)

    async def send_chat(self, text: str):
        """Sends a chat message to the game via Mineflayer."""
        if not self.active_client:
            raise ConnectionError("No active Mineflayer bot connected.")

        await self.active_client.send(json.dumps({
            "type": "chat",
            "text": text
        }))
