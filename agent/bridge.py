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
            self.active_client = None

    async def _route_message(self, message: Dict[str, Any]):
        """Routes incoming messages from Node.js bot to pending futures or state cache."""
        msg_type = message.get("type")
        msg_id = message.get("id")

        if msg_type == "state_update":
            self.latest_state = message.get("data", {})
            logger.debug("Received background state update from bot.")

        elif msg_type == "state_response" and msg_id in self.pending_requests:
            future = self.pending_requests.pop(msg_id)
            if not future.done():
                future.set_result(message.get("data", {}))

        elif msg_type == "execution_result" and msg_id in self.pending_requests:
            future = self.pending_requests.pop(msg_id)
            if not future.done():
                self.latest_state = message.get("currentState", self.latest_state)
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
        if not self.active_client:
            raise ConnectionError("No active Mineflayer bot connected to the WebSocket bridge.")

        req_id = str(uuid.uuid4())
        future = asyncio.get_event_loop().create_future()
        self.pending_requests[req_id] = future

        await self.active_client.send(json.dumps({
            "type": "get_state",
            "id": req_id
        }))

        return await asyncio.wait_for(future, timeout=timeout)

    async def execute_code(self, code: str, timeout: float = 45.0) -> Dict[str, Any]:
        """Sends a JavaScript code snippet to the bot execution sandbox."""
        if not self.active_client:
            raise ConnectionError("No active Mineflayer bot connected to the WebSocket bridge.")

        req_id = str(uuid.uuid4())
        future = asyncio.get_event_loop().create_future()
        self.pending_requests[req_id] = future

        await self.active_client.send(json.dumps({
            "type": "execute_code",
            "id": req_id,
            "code": code
        }))

        return await asyncio.wait_for(future, timeout=timeout)

    async def send_chat(self, text: str):
        """Sends a chat message to the game via Mineflayer."""
        if not self.active_client:
            raise ConnectionError("No active Mineflayer bot connected.")

        await self.active_client.send(json.dumps({
            "type": "chat",
            "text": text
        }))
