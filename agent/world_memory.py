"""Controller memory lifecycle: fast exact writes, slow Graphiti in background."""

import asyncio
import logging
from pathlib import Path

from agent.config import Config
from agent.graphiti_memory import GraphitiMemory
from agent.memory_store import WorldMemoryStore

logger = logging.getLogger("WorldMemory")
REPO_ROOT = Path(__file__).resolve().parents[1]


class WorldMemory:
    def __init__(self, settings: Config):
        directory = Path(settings.memory_dir).expanduser()
        self.directory = directory if directory.is_absolute() else REPO_ROOT / directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.store = WorldMemoryStore(self.directory / "checkpoints.sqlite3")
        self.settings = settings
        self.graphiti = GraphitiMemory(self.directory, settings) if settings.graphiti_enabled else None
        self.graph_ready = False
        self.last_error = ""
        self.worker = None
        self._wake = asyncio.Event()

    def start(self):
        if self.graphiti:
            self.worker = asyncio.create_task(self._ingestion_loop(), name="graphiti-world-memory")

    def observe(self, state: dict):
        if self.store.observe(state):
            logger.info("World session restored from %s", self.directory)
            logger.info("%s", self.store.context(state))
        self._wake.set()

    def record_event(self, event: dict):
        self.store.record_event(event)
        self._wake.set()

    def record_task(self, objective, task, result, state, requester=None):
        self.store.record_task(objective, task, result, state, requester=requester)
        self._wake.set()

    async def context(self, state: dict, query: str = "") -> str:
        context = self.store.context(state)
        if self.graph_ready and query:
            try:
                facts = await asyncio.wait_for(self.graphiti.search(query, state), timeout=3)
                if facts:
                    context += "\n" + facts
            except Exception as exc:
                logger.debug("Graph search unavailable; using exact checkpoint: %s", exc)
        return context

    def status(self) -> dict:
        return {**self.store.status(), "graphiti": "ready" if self.graph_ready else (
            "starting or retrying" if self.graphiti else "disabled"), "last_error": self.last_error,
            "directory": str(self.directory)}

    async def _ingestion_loop(self):
        while True:
            episode = None
            try:
                if not self.graph_ready:
                    await self.graphiti.start()
                    self.graph_ready = True
                    self.last_error = ""
                    logger.info("Graphiti ready: embedded local graph + %s + %s", self.settings.memory_model,
                                self.settings.memory_embedding_model)
                # Clear before querying, so a wake during ingestion isn't lost.
                self._wake.clear()
                pending = self.store.pending(limit=1)
                if not pending:
                    await self._wake.wait()
                    continue
                episode = pending[0]
                await asyncio.wait_for(self.graphiti.ingest(episode), self.settings.memory_ingest_timeout)
                self.store.acknowledge(episode["id"])
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)[:300]
                if episode:
                    self.store.fail(episode["id"], self.last_error)
                logger.warning("Graphiti deferred; exact memory is saved and episode will retry: %s", exc)
                await asyncio.sleep(30)

    async def close(self):
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        try:
            if self.graphiti:
                await self.graphiti.close()
        finally:
            self.store.close()
