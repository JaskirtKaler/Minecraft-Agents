"""Crash-safe, world-scoped memory for the Minecraft agent.

This module deliberately keeps two kinds of memory separate:

* SQLite is the authoritative record for exact recent bot state, explicitly
  observed key blocks, task outcomes, and bot events.
* The ``outbox`` is a small, durable stream of *summaries* that a Graphiti
  worker may ingest later.  It is not necessary for restoring the bot after a
  restart, and it must be acknowledged only after the Graphiti write succeeds.

The store does not import Graphiti.  That keeps local gameplay usable when the
semantic graph, an embedding model, or FalkorDBLite is unavailable, while still
making graph ingestion retryable and idempotent.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_MEANINGFUL_POI_NAMES = {
    "chest",
    "trapped_chest",
    "ender_chest",
    "crafting_table",
    "furnace",
    "blast_furnace",
    "smoker",
    "nether_portal",
    "end_portal",
}

_KEY_BLOCK_NAMES = {
    "crafting_table",
    "furnace",
    "blast_furnace",
    "smoker",
    "chest",
    "trapped_chest",
    "ender_chest",
    "water",
    "lava",
    "wheat",
    "carrots",
    "potatoes",
    "beetroots",
    "nether_portal",
    "end_portal",
}

_MAX_POI_EPISODES_PER_OBSERVATION = 8


@dataclass(frozen=True)
class _Scope:
    """The application-owned namespace for a concrete Minecraft session."""

    world_id: str
    dimension: str
    session_id: str


class WorldMemoryStore:
    """Persist precise Minecraft observations and a retryable Graphiti outbox.

    ``world.id`` is the durable identity of the Minecraft save/server.  It is
    intentionally distinct from a player name, bot process, or WebSocket
    connection.  Every read that produces gameplay context is scoped to both
    that world and its dimension, so Nether/End facts never leak into an
    Overworld prompt (or into another world with a similarly named dimension).

    Graphiti consumers should use a pending row's stable ``id`` to derive the
    episode name ``minecraft:<id>`` and use :meth:`graph_group_id` for the
    Graphiti group.  Query that episode name before retrying an add.  This is
    safer than passing a fresh UUID directly to Graphiti's episode API and
    allows a process crash between graph write and SQLite acknowledgement.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._closed = False
        self._last_scope: _Scope | None = None
        self._connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        # WAL plus FULL synchronous commits make a completed mutation durable
        # before its caller is told about it.  WAL also permits a graph worker
        # to inspect the database while the controller is receiving state.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._initialize_schema()

    def __enter__(self) -> "WorldMemoryStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @staticmethod
    def graph_group_id(world_id: str, dimension: str) -> str:
        """Return the same safe, deterministic namespace used by the adapter."""
        material = json.dumps([world_id, dimension], separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()[:32]
        return f"minecraft_{digest}"

    def observe(self, state: Mapping[str, Any]) -> bool:
        """Store the exact latest state and return whether its session changed.

        A partial nearby-block scan is positive evidence only: entries observed
        in the scan are refreshed, but entries absent from it are never removed.
        ``record_event`` is the path for an explicit ``block_change`` removal.

        A first sighting, a new dimension, or a new session ID returns ``True``
        and creates exactly one join/transition outbox episode.  Repeated state
        snapshots from the same session return ``False`` and do not enter the
        semantic-graph outbox.
        """
        # A pre-spawn or dead client has identity metadata but no trustworthy
        # inventory/position scan.  Never replace the last useful checkpoint
        # with that transient placeholder.
        if isinstance(state, Mapping) and state.get("ready") is False:
            return False

        scope = self._scope_from_state(state, require_session=True)
        observed_at = self._observed_at(state)
        serialized_state = self._json_dumps(state)

        with self._lock, self._connection:
            self._ensure_open()
            previous = self._connection.execute(
                """
                SELECT session_id, observed_at, state_json
                FROM latest_snapshots
                WHERE world_id = ? AND dimension = ?
                """,
                (scope.world_id, scope.dimension),
            ).fetchone()
            session_changed = previous is None or previous["session_id"] != scope.session_id

            transition_kind: str | None = None
            previous_state: Mapping[str, Any] | None = None
            if session_changed:
                if previous is not None:
                    previous_state = self._json_loads(previous["state_json"])
                    self._connection.execute(
                        """
                        INSERT INTO snapshot_history
                            (world_id, dimension, session_id, observed_at, state_json, reason)
                        VALUES (?, ?, ?, ?, ?, 'session_transition')
                        """,
                        (
                            scope.world_id,
                            scope.dimension,
                            previous["session_id"],
                            previous["observed_at"],
                            previous["state_json"],
                        ),
                    )
                    self._connection.execute(
                        """
                        UPDATE memory_sessions
                        SET is_current = 0
                        WHERE world_id = ? AND dimension = ?
                        """,
                        (scope.world_id, scope.dimension),
                    )
                    transition_kind = "session_transition"
                else:
                    seen_elsewhere = self._connection.execute(
                        "SELECT 1 FROM latest_snapshots WHERE world_id = ? LIMIT 1",
                        (scope.world_id,),
                    ).fetchone()
                    transition_kind = "dimension_transition" if seen_elsewhere else "world_join"

            self._connection.execute(
                """
                INSERT INTO memory_sessions
                    (world_id, dimension, session_id, first_observed_at, last_observed_at, is_current)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(world_id, dimension, session_id) DO UPDATE SET
                    last_observed_at = excluded.last_observed_at,
                    is_current = 1
                """,
                (
                    scope.world_id,
                    scope.dimension,
                    scope.session_id,
                    observed_at,
                    observed_at,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO latest_snapshots
                    (world_id, dimension, session_id, observed_at, state_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(world_id, dimension) DO UPDATE SET
                    session_id = excluded.session_id,
                    observed_at = excluded.observed_at,
                    state_json = excluded.state_json
                """,
                (
                    scope.world_id,
                    scope.dimension,
                    scope.session_id,
                    observed_at,
                    serialized_state,
                ),
            )

            if transition_kind is not None:
                payload: dict[str, Any] = {
                    "event": transition_kind,
                    "observed_at": observed_at,
                    "snapshot": self._snapshot_summary(state),
                }
                if previous_state is not None:
                    payload["prior_snapshot"] = self._snapshot_summary(previous_state)
                self._enqueue(transition_kind, scope, observed_at, payload)

            poi_episodes = 0
            for block_name, x, y, z in self._iter_keyblocks(state):
                previous_block = self._upsert_keyblock(
                    scope,
                    block_name,
                    x,
                    y,
                    z,
                    present=True,
                    observed_at=observed_at,
                    source="state_scan",
                )
                if (
                    poi_episodes < _MAX_POI_EPISODES_PER_OBSERVATION
                    and self._is_meaningful_poi(block_name)
                    and self._is_new_or_changed_present_block(previous_block, block_name)
                ):
                    self._enqueue(
                        "poi_discovery",
                        scope,
                        observed_at,
                        {
                            "event": "poi_discovery",
                            "poi": block_name,
                            "position": {"x": x, "y": y, "z": z},
                            "source": "state_scan",
                        },
                    )
                    poi_episodes += 1

        self._last_scope = scope
        return session_changed

    def record_task(
        self,
        objective: str,
        task: Mapping[str, Any] | None,
        result: Mapping[str, Any] | Any,
        state: Mapping[str, Any],
        requester: str | None = None,
    ) -> str:
        """Append one task outcome and queue its concise graph episode.

        The stored result is exact JSON for audit/recovery.  The outbox payload
        intentionally carries a bounded explanation plus verification evidence;
        it never treats a mere successful function return as verified gameplay.
        """
        scope = self._scope_from_state(state, require_session=True)
        task_id = str(uuid.uuid4())
        # The outcome is reported now, possibly after a long action/timeout.
        # The preceding checkpoint's timestamp is not the completion timestamp.
        observed_at = self._now()
        task_value = dict(task) if isinstance(task, Mapping) else None
        result_value = dict(result) if isinstance(result, Mapping) else {"value": result}
        outcome = self._task_outcome(result_value)

        with self._lock, self._connection:
            self._ensure_open()
            self._connection.execute(
                """
                INSERT INTO task_journal
                    (id, world_id, dimension, session_id, observed_at, objective,
                     task_json, result_json, requester, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    scope.world_id,
                    scope.dimension,
                    scope.session_id,
                    observed_at,
                    str(objective),
                    self._json_dumps(task_value),
                    self._json_dumps(result_value),
                    str(requester) if requester is not None else None,
                    outcome,
                ),
            )
            self._enqueue(
                "task_outcome",
                scope,
                observed_at,
                {
                    "event": "task_outcome",
                    "agent": self._agent_name(state),
                    "objective": self._short(objective, 400),
                    "task": task_value,
                    "requester": self._short(requester, 80) if requester else None,
                    "outcome": outcome,
                    "verification": {
                        "success": result_value.get("success"),
                        "verified": result_value.get("verified"),
                        "semantics": self._verification_semantics(outcome),
                    },
                    "message": self._short(
                        result_value.get("message") or result_value.get("error") or "",
                        360,
                    ),
                    "evidence": self._task_evidence(result_value),
                },
            )

        self._last_scope = scope
        return task_id

    def record_event(self, message: Mapping[str, Any]) -> str:
        """Journal one bot event and update key-block truth when it is explicit.

        Events should include a complete ``state``/``world`` identity.  During a
        live process only, a missing identity falls back to the most recently
        observed scope; this compatibility path is deliberately unavailable
        after a restart so delayed, unscoped events cannot cross-contaminate a
        different world.
        """
        if not isinstance(message, Mapping):
            raise TypeError("event message must be a mapping")

        event_data = self._event_data(message)
        raw_kind = message.get("event") or message.get("kind") or event_data.get("event") or "event"
        kind = str(raw_kind)
        # Mineflayer's lifecycle event carries the complete state inside its
        # data envelope.  Route it through observe first: it creates the one
        # durable join/transition episode and de-duplicates the immediately
        # following state_update from the bridge.
        joined_state = event_data.get("currentState")
        if kind.lower() == "world_join" and isinstance(joined_state, Mapping):
            self.observe(joined_state)

        scope = self._scope_from_event(message, event_data)
        observed_at = self._observed_at(message, event_data, joined_state if isinstance(joined_state, Mapping) else {})
        event_id = str(uuid.uuid4())

        with self._lock, self._connection:
            self._ensure_open()
            self._connection.execute(
                """
                INSERT INTO event_journal
                    (id, world_id, dimension, session_id, observed_at, kind, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    scope.world_id,
                    scope.dimension,
                    scope.session_id,
                    observed_at,
                    kind,
                    self._json_dumps(message),
                ),
            )

            lowered_kind = kind.lower()
            if lowered_kind == "block_change":
                change = self._block_change(event_data)
                if change is not None:
                    block_name, x, y, z = change
                    present = self._is_key_block(block_name)
                    previous_block = self._upsert_keyblock(
                        scope,
                        block_name,
                        x,
                        y,
                        z,
                        present=present,
                        observed_at=observed_at,
                        source="block_change",
                    )
                    if present and self._is_meaningful_poi(block_name):
                        if self._is_new_or_changed_present_block(previous_block, block_name):
                            self._enqueue(
                                "poi_discovery",
                                scope,
                                observed_at,
                                {
                                    "event": "poi_discovery",
                                    "poi": block_name,
                                    "position": {"x": x, "y": y, "z": z},
                                    "source": "block_change",
                                },
                            )
                    elif self._was_present_meaningful_poi(previous_block):
                        self._enqueue(
                            "poi_removed",
                            scope,
                            observed_at,
                            {
                                "event": "poi_removed",
                                "poi": previous_block["block_name"],
                                "position": {"x": x, "y": y, "z": z},
                                "replacement": block_name,
                                "source": "block_change",
                            },
                        )
            elif lowered_kind == "death":
                self._enqueue(
                    "death",
                    scope,
                    observed_at,
                    {
                        "event": "death",
                        "message": self._short(
                            event_data.get("message") or event_data.get("reason") or "bot died",
                            300,
                        ),
                        "snapshot": self._snapshot_summary(message.get("state") or event_data),
                    },
                )

        self._last_scope = scope
        return event_id

    def context(self, state: Mapping[str, Any], limit: int = 8) -> str:
        """Return a timestamped historical summary for one world/dimension only."""
        try:
            scope = self._scope_from_state(state, require_session=False)
        except (TypeError, ValueError):
            # A live caller may ask for context while the bridge has only a
            # not-ready state.  It is safe to use the most recently observed
            # scope in this process; after a restart there is no such fallback.
            if self._last_scope is None:
                raise
            scope = self._last_scope
        item_limit = max(0, int(limit))

        with self._lock:
            self._ensure_open()
            latest = self._connection.execute(
                """
                SELECT session_id, observed_at, state_json
                FROM latest_snapshots
                WHERE world_id = ? AND dimension = ?
                """,
                (scope.world_id, scope.dimension),
            ).fetchone()
            prior = None
            if latest is not None:
                prior = self._connection.execute(
                    """
                    SELECT session_id, observed_at, state_json
                    FROM snapshot_history
                    WHERE world_id = ? AND dimension = ? AND session_id != ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (scope.world_id, scope.dimension, latest["session_id"]),
                ).fetchone()
            tasks = self._connection.execute(
                """
                SELECT observed_at, objective, outcome, result_json, requester
                FROM task_journal
                WHERE world_id = ? AND dimension = ?
                ORDER BY observed_at DESC, rowid DESC
                LIMIT ?
                """,
                (scope.world_id, scope.dimension, item_limit),
            ).fetchall()
            candidate_blocks = self._connection.execute(
                """
                SELECT block_name, x, y, z, observed_at
                FROM keyblocks
                WHERE world_id = ? AND dimension = ? AND present = 1
                ORDER BY observed_at DESC
                LIMIT 128
                """,
                (scope.world_id, scope.dimension),
            ).fetchall()

        pois = [row for row in candidate_blocks if self._is_meaningful_poi(row["block_name"])][:item_limit]
        resources = [row for row in candidate_blocks if not self._is_meaningful_poi(row["block_name"])][:item_limit]
        if latest is None and not tasks and not pois and not resources:
            return (
                f"No durable memory for world '{scope.world_id}' "
                f"in dimension '{scope.dimension}'."
            )

        lines = [f"World memory: {scope.world_id} / {scope.dimension}"]
        if latest is not None:
            latest_state = self._json_loads(latest["state_json"])
            lines.append(
                f"Current snapshot [{latest['observed_at']}; session {latest['session_id']}]: "
                f"{self._format_snapshot(latest_state)}"
            )
        if prior is not None:
            prior_state = self._json_loads(prior["state_json"])
            lines.append(
                f"Previous session snapshot [{prior['observed_at']}; session {prior['session_id']}]: "
                f"{self._format_snapshot(prior_state)}"
            )

        if pois:
            lines.append(
                "Known POIs: "
                + "; ".join(
                    f"{row['block_name']} @ {row['x']}, {row['y']}, {row['z']} "
                    f"(seen {row['observed_at']})"
                    for row in pois
                )
            )
        elif latest is not None:
            lines.append("Known POIs: none recorded.")

        if resources:
            lines.append(
                "Known resources: "
                + "; ".join(
                    f"{row['block_name']} @ {row['x']}, {row['y']}, {row['z']} "
                    f"(seen {row['observed_at']})"
                    for row in resources
                )
            )

        if tasks:
            lines.append("Recent task outcomes:")
            for row in tasks:
                result = self._json_loads(row["result_json"])
                detail = self._short(
                    result.get("message") or result.get("error") or "",
                    180,
                )
                requester = f" for {row['requester']}" if row["requester"] else ""
                suffix = f" — {detail}" if detail else ""
                lines.append(
                    f"- [{row['observed_at']}] {row['outcome']}{requester}: "
                    f"{self._short(row['objective'], 280)}{suffix}"
                )
        return "\n".join(lines)

    def pending(self, limit: int = 1) -> list[dict[str, Any]]:
        """Return durable graph episodes awaiting acknowledgement, oldest first."""
        item_limit = max(0, int(limit))
        if item_limit == 0:
            return []
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT id, kind, world_id, dimension, payload_json, observed_at
                FROM outbox
                WHERE status = 'pending'
                ORDER BY rowid ASC
                LIMIT ?
                """,
                (item_limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "world_id": row["world_id"],
                "dimension": row["dimension"],
                "payload": self._json_loads(row["payload_json"]),
                "observed_at": row["observed_at"],
            }
            for row in rows
        ]

    def acknowledge(self, outbox_id: str) -> bool:
        """Mark a successfully ingested Graphiti episode as durable-complete."""
        with self._lock, self._connection:
            self._ensure_open()
            cursor = self._connection.execute(
                """
                UPDATE outbox
                SET status = 'acknowledged', acknowledged_at = ?, last_error = NULL
                WHERE id = ? AND status = 'pending'
                """,
                (self._now(), str(outbox_id)),
            )
        return cursor.rowcount == 1

    def fail(self, outbox_id: str, error: object) -> bool:
        """Record a failed graph attempt but retain the row for retry on restart."""
        with self._lock, self._connection:
            self._ensure_open()
            cursor = self._connection.execute(
                """
                UPDATE outbox
                SET attempts = attempts + 1,
                    last_error = ?,
                    last_attempt_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (self._short(error, 800), self._now(), str(outbox_id)),
            )
        return cursor.rowcount == 1

    def status(self) -> dict[str, int | str]:
        """Return compact health and durability counts without exposing content."""
        with self._lock:
            self._ensure_open()
            count = lambda query: int(self._connection.execute(query).fetchone()[0])
            return {
                "path": self.path,
                "worlds": count("SELECT COUNT(DISTINCT world_id) FROM latest_snapshots"),
                "snapshots": count("SELECT COUNT(*) FROM latest_snapshots"),
                "history_snapshots": count("SELECT COUNT(*) FROM snapshot_history"),
                "keyblocks": count("SELECT COUNT(*) FROM keyblocks"),
                "tasks": count("SELECT COUNT(*) FROM task_journal"),
                "events": count("SELECT COUNT(*) FROM event_journal"),
                "outbox": count("SELECT COUNT(*) FROM outbox"),
                "pending": count("SELECT COUNT(*) FROM outbox WHERE status = 'pending'"),
                "acknowledged": count("SELECT COUNT(*) FROM outbox WHERE status = 'acknowledged'"),
            }

    def close(self) -> None:
        """Flush and close the SQLite connection.  Calling this twice is safe."""
        with self._lock:
            if self._closed:
                return
            self._connection.commit()
            self._connection.close()
            self._closed = True

    def _initialize_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_sessions (
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL,
                    last_observed_at TEXT NOT NULL,
                    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
                    PRIMARY KEY (world_id, dimension, session_id)
                );

                CREATE TABLE IF NOT EXISTS latest_snapshots (
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (world_id, dimension)
                );

                CREATE TABLE IF NOT EXISTS snapshot_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    reason TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS keyblocks (
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    z INTEGER NOT NULL,
                    block_name TEXT NOT NULL,
                    present INTEGER NOT NULL CHECK (present IN (0, 1)),
                    observed_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    PRIMARY KEY (world_id, dimension, x, y, z)
                );

                CREATE TABLE IF NOT EXISTS task_journal (
                    id TEXT PRIMARY KEY,
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    task_json TEXT,
                    result_json TEXT NOT NULL,
                    requester TEXT,
                    outcome TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_journal (
                    id TEXT PRIMARY KEY,
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    world_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'acknowledged')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    last_attempt_at TEXT,
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_task_scope_time
                    ON task_journal (world_id, dimension, observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_keyblock_scope_seen
                    ON keyblocks (world_id, dimension, present, observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_outbox_pending
                    ON outbox (status, created_at);
                """
            )

    def _enqueue(
        self,
        kind: str,
        scope: _Scope,
        observed_at: str,
        payload: Mapping[str, Any],
    ) -> str:
        """Insert one bounded, stable episode row inside the caller transaction."""
        outbox_id = str(uuid.uuid4())
        concise = self._bounded_value(dict(payload))
        if not isinstance(concise, dict):  # Defensive: payload is declared Mapping.
            concise = {"facts": concise}
        concise.update(
            {
                # Graphiti consumers use this stable name to find an earlier
                # successful add before retrying a pending SQLite row.
                "episode_name": f"minecraft:{outbox_id}",
                "graph_group_id": self.graph_group_id(scope.world_id, scope.dimension),
                "world_id": scope.world_id,
                "dimension": scope.dimension,
                "session_id": scope.session_id,
            }
        )
        self._connection.execute(
            """
            INSERT INTO outbox
                (id, kind, world_id, dimension, session_id, payload_json,
                 observed_at, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                outbox_id,
                kind,
                scope.world_id,
                scope.dimension,
                scope.session_id,
                self._json_dumps(concise),
                observed_at,
                self._now(),
            ),
        )
        return outbox_id

    def _upsert_keyblock(
        self,
        scope: _Scope,
        block_name: str,
        x: int,
        y: int,
        z: int,
        *,
        present: bool,
        observed_at: str,
        source: str,
    ) -> sqlite3.Row | None:
        previous = self._connection.execute(
            """
            SELECT block_name, present, observed_at
            FROM keyblocks
            WHERE world_id = ? AND dimension = ? AND x = ? AND y = ? AND z = ?
            """,
            (scope.world_id, scope.dimension, x, y, z),
        ).fetchone()
        self._connection.execute(
            """
            INSERT INTO keyblocks
                (world_id, dimension, x, y, z, block_name, present,
                 observed_at, session_id, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(world_id, dimension, x, y, z) DO UPDATE SET
                block_name = excluded.block_name,
                present = excluded.present,
                observed_at = excluded.observed_at,
                session_id = excluded.session_id,
                source = excluded.source
            """,
            (
                scope.world_id,
                scope.dimension,
                x,
                y,
                z,
                str(block_name),
                1 if present else 0,
                observed_at,
                scope.session_id,
                source,
            ),
        )
        return previous

    @classmethod
    def _scope_from_state(cls, state: Mapping[str, Any], *, require_session: bool) -> _Scope:
        if not isinstance(state, Mapping):
            raise TypeError("state must be a mapping with world.id, dimension, and sessionId")
        world = state.get("world")
        world_data = world if isinstance(world, Mapping) else {}
        stats = state.get("stats")
        stats_data = stats if isinstance(stats, Mapping) else {}

        world_id = cls._identity_value(
            world_data.get("id"), state.get("world_id"), state.get("worldId")
        )
        dimension = cls._identity_value(
            world_data.get("dimension"),
            state.get("dimension"),
            stats_data.get("dimension"),
        )
        session_id = cls._identity_value(
            world_data.get("sessionId"),
            world_data.get("session_id"),
            state.get("sessionId"),
            state.get("session_id"),
        )
        if not world_id:
            raise ValueError("state.world.id is required for durable memory")
        if not dimension:
            raise ValueError("state.world.dimension is required for durable memory")
        if require_session and not session_id:
            raise ValueError("state.world.sessionId is required for durable memory")
        return _Scope(world_id, dimension, session_id or "")

    def _scope_from_event(self, message: Mapping[str, Any], data: Mapping[str, Any]) -> _Scope:
        state = message.get("state") or message.get("currentState")
        candidates: list[Mapping[str, Any]] = []
        if isinstance(state, Mapping):
            candidates.append(state)
        nested_state = data.get("currentState") or data.get("state")
        if isinstance(nested_state, Mapping):
            candidates.append(nested_state)
        candidates.extend((message, data))
        for candidate in candidates:
            try:
                return self._scope_from_state(candidate, require_session=True)
            except (TypeError, ValueError):
                continue
        if self._last_scope is not None:
            return self._last_scope
        raise ValueError(
            "event needs state.world.id, state.world.dimension, and state.world.sessionId; "
            "no live observed scope is available"
        )

    @staticmethod
    def _identity_value(*values: object) -> str | None:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @staticmethod
    def _event_data(message: Mapping[str, Any]) -> Mapping[str, Any]:
        candidate = message.get("data")
        if not isinstance(candidate, Mapping):
            candidate = message.get("payload")
        return candidate if isinstance(candidate, Mapping) else {}

    @classmethod
    def _observed_at(cls, *records: Mapping[str, Any]) -> str:
        for record in records:
            if not isinstance(record, Mapping):
                continue
            for key in ("observed_at", "observedAt", "timestamp", "time"):
                value = record.get(key)
                if value is None:
                    continue
                if isinstance(value, (int, float)):
                    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
                text = str(value).strip()
                if text:
                    return text
        return cls._now()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @classmethod
    def _iter_keyblocks(cls, state: Mapping[str, Any]) -> Iterable[tuple[str, int, int, int]]:
        raw_blocks = state.get("nearbyKeyBlocks")
        if raw_blocks is None:
            raw_blocks = state.get("nearby_key_blocks")
        if raw_blocks is None:
            raw_blocks = state.get("keyBlocks")

        if isinstance(raw_blocks, Mapping):
            groups = raw_blocks.items()
        elif isinstance(raw_blocks, list):
            groups = ((None, raw_blocks),)
        else:
            return

        for default_name, records in groups:
            if isinstance(records, Mapping):
                records_to_read: Iterable[object] = (records,)
            elif isinstance(records, (list, tuple)):
                records_to_read = records
            else:
                continue
            for record in records_to_read:
                if not isinstance(record, Mapping):
                    continue
                block_name = cls._block_name(record) or cls._identity_value(default_name)
                coordinates = cls._coordinates(record)
                if not block_name or coordinates is None:
                    continue
                yield block_name, *coordinates

    @classmethod
    def _block_change(cls, data: Mapping[str, Any]) -> tuple[str, int, int, int] | None:
        block_name: str | None = None
        for key in ("newBlock", "new_block", "new", "block", "block_name", "newName", "to"):
            if key not in data:
                continue
            block_name = cls._block_name(data.get(key))
            if block_name:
                break
        if not block_name:
            return None
        coordinates = cls._coordinates(data)
        if coordinates is None:
            for key in ("newBlock", "new_block", "new", "block"):
                candidate = data.get(key)
                if isinstance(candidate, Mapping):
                    coordinates = cls._coordinates(candidate)
                    if coordinates is not None:
                        break
        if coordinates is None:
            return None
        return block_name, *coordinates

    @staticmethod
    def _block_name(value: object) -> str | None:
        if isinstance(value, Mapping):
            for key in ("name", "block_name", "blockName", "id"):
                if value.get(key) is not None:
                    text = str(value[key]).strip()
                    if text:
                        return text
            return None
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _coordinates(cls, value: Mapping[str, Any]) -> tuple[int, int, int] | None:
        candidates: list[object] = [value.get("position"), value]
        for candidate in candidates:
            if isinstance(candidate, Mapping):
                values = (candidate.get("x"), candidate.get("y"), candidate.get("z"))
            elif isinstance(candidate, (list, tuple)) and len(candidate) >= 3:
                values = (candidate[0], candidate[1], candidate[2])
            else:
                continue
            if any(component is None for component in values):
                continue
            try:
                return tuple(math.floor(float(component)) for component in values)  # type: ignore[return-value]
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _display_position(value: object) -> str | None:
        """Render a player position without converting its fractional values."""
        if isinstance(value, Mapping):
            values = (value.get("x"), value.get("y"), value.get("z"))
        elif isinstance(value, (list, tuple)) and len(value) >= 3:
            values = (value[0], value[1], value[2])
        else:
            return None
        if any(component is None for component in values):
            return None
        return f"({values[0]}, {values[1]}, {values[2]})"

    @staticmethod
    def _normalise_block_name(name: object) -> str:
        return str(name).lower().removeprefix("minecraft:")

    @classmethod
    def _is_meaningful_poi(cls, block_name: object) -> bool:
        name = cls._normalise_block_name(block_name)
        return name in _MEANINGFUL_POI_NAMES or name.endswith("_bed")

    @classmethod
    def _is_key_block(cls, block_name: object) -> bool:
        name = cls._normalise_block_name(block_name)
        if name in _KEY_BLOCK_NAMES or cls._is_meaningful_poi(name):
            return True
        return name.endswith(("_log", "_stem", "_ore", "_bed"))

    @classmethod
    def _is_new_or_changed_present_block(cls, previous: sqlite3.Row | None, block_name: str) -> bool:
        return (
            previous is None
            or not bool(previous["present"])
            or cls._normalise_block_name(previous["block_name"])
            != cls._normalise_block_name(block_name)
        )

    @classmethod
    def _was_present_meaningful_poi(cls, previous: sqlite3.Row | None) -> bool:
        return bool(
            previous is not None
            and previous["present"]
            and cls._is_meaningful_poi(previous["block_name"])
        )

    @staticmethod
    def _task_outcome(result: Mapping[str, Any]) -> str:
        success = result.get("success")
        verified = result.get("verified")
        status = str(result.get("status") or "").lower()
        if status in {"unknown", "interrupted"}:
            return "unverified"
        if verified is True and success is True:
            return "verified"
        if success is False or result.get("error") or status in {"failure", "failed", "error"}:
            return "failure"
        return "unverified"

    @staticmethod
    def _verification_semantics(outcome: str) -> str:
        if outcome == "verified":
            return "The requested result was explicitly verified against game state."
        if outcome == "failure":
            return "The task failed or reported an execution error; no success is implied."
        return "No complete verified outcome is known; the attempt may be incomplete or interrupted."

    @classmethod
    def _task_evidence(cls, result: Mapping[str, Any]) -> dict[str, Any]:
        data = result.get("data")
        if not isinstance(data, Mapping):
            return {}
        useful_keys = (
            "item",
            "count",
            "requested",
            "requested_count",
            "before",
            "after",
            "beforeCount",
            "afterCount",
            "inventory_before",
            "inventory_after",
            "observed_before",
            "observed_after",
            "observed_mined",
            "collected",
            "delivered",
            "remaining",
            "blocks_dug",
            "max_distance",
            "recipient",
            "delivery",
            "mine",
            "give",
            "mined",
            "handoff",
            "error_code",
        )
        return {
            key: cls._bounded_value(data[key])
            for key in useful_keys
            if key in data
        }

    @classmethod
    def _snapshot_summary(cls, state: object) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            return {}
        stats = state.get("stats")
        stats_data = stats if isinstance(stats, Mapping) else {}
        position = stats_data.get("position") or state.get("position")
        inventory = state.get("inventory")
        summarized_inventory: list[dict[str, Any]] = []
        if isinstance(inventory, list):
            for item in inventory[:8]:
                if not isinstance(item, Mapping):
                    continue
                name = item.get("name")
                count = item.get("count")
                if name is not None:
                    summarized_inventory.append({"name": name, "count": count})
        summary: dict[str, Any] = {
            "agent": cls._agent_name(state),
            "position": position if isinstance(position, (Mapping, list, tuple)) else None,
            "health": stats_data.get("health", state.get("health")),
            "food": stats_data.get("food", state.get("food")),
            "standing_on": state.get("standingOn") or state.get("standing_on"),
            "inventory": summarized_inventory,
        }
        return {key: value for key, value in summary.items() if value not in (None, [], "")}

    @staticmethod
    def _agent_name(state: Mapping[str, Any]) -> str | None:
        value = state.get("username") or state.get("bot_username") or state.get("botUsername")
        text = str(value).strip() if value is not None else ""
        return text or None

    @classmethod
    def _format_snapshot(cls, state: Mapping[str, Any]) -> str:
        summary = cls._snapshot_summary(state)
        parts: list[str] = []
        position = summary.get("position")
        display_position = cls._display_position(position)
        if display_position is not None:
            parts.append(f"position={display_position}")
        if "health" in summary:
            parts.append(f"health={summary['health']}")
        if "food" in summary:
            parts.append(f"food={summary['food']}")
        if "standing_on" in summary:
            parts.append(f"standing_on={summary['standing_on']}")
        inventory = summary.get("inventory")
        if isinstance(inventory, list) and inventory:
            rendered = ", ".join(
                f"{item.get('name')}×{item.get('count')}" for item in inventory[:6]
            )
            parts.append(f"inventory={rendered}")
        return "; ".join(parts) if parts else "state captured"

    @classmethod
    def _bounded_value(cls, value: object, depth: int = 0) -> Any:
        """Keep graph payloads concise without mutating the exact SQLite journal."""
        # Preserve scalar types at every nesting depth: Graphiti extraction
        # needs real quantities and booleans for task verification semantics.
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return cls._short(value, 480)
        if depth >= 4:
            return cls._short(value, 180)
        if isinstance(value, Mapping):
            bounded: dict[str, Any] = {}
            items = list(value.items())
            for key, nested in items[:20]:
                bounded[cls._short(key, 80)] = cls._bounded_value(nested, depth + 1)
            if len(items) > 20:
                bounded["_truncated"] = f"{len(items) - 20} additional fields omitted"
            return bounded
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            bounded_list = [cls._bounded_value(item, depth + 1) for item in values[:16]]
            if len(values) > 16:
                bounded_list.append(f"… {len(values) - 16} additional values omitted")
            return bounded_list
        return cls._short(value, 480)

    @staticmethod
    def _short(value: object, limit: int) -> str:
        text = str(value).strip()
        return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"

    @staticmethod
    def _json_dumps(value: object) -> str:
        return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _json_loads(value: str) -> dict[str, Any]:
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {"value": decoded}

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("WorldMemoryStore is closed")
