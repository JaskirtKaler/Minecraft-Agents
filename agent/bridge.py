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

logger = logging.getLogger("BridgeServer")
logging.basicConfig(level=logging.INFO)

class MineflayerBridge:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self.active_client: Optional[WebSocketServerProtocol] = None
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.latest_state: Dict[str, Any] = {}
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

        elif msg_type == "knowledge_response":
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

    async def execute_task(self, task: Dict[str, Any], timeout: float = 240.0) -> Dict[str, Any]:
        """Runs an allowlisted, verified Mineflayer skill on the Node bot."""
        return await self._request({"type": "execute_task", "task": task}, timeout=timeout)

    async def get_knowledge(self, subject: str) -> Dict[str, Any]:
        return await self._request({"type": "get_knowledge", "subject": subject}, timeout=10)

    async def cancel_task(self):
        if self.active_client:
            await self.active_client.send(json.dumps({"type": "cancel_task"}))

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
            if payload.get("type") in {"execute_task", "execute_code"}:
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
