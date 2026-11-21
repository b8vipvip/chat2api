"""Durable, owner-scoped SSE event journal for Mode 1 Web Chat recovery.

Only bounded presentation events are retained. Raw DOM, credentials, Worker
metadata, and binary media payloads are never written to this journal.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .chat_stream_delivery_v194 import FINAL_CHUNK_CHARS
from .chat_tool_references_v1 import normalize_tool_references


_MAX_TEXT_CHARS = 2_000_000
_MAX_EVENT_CHARS = 1_000_000
_MAX_TOOL_TEXT_CHARS = 8_000
_MAX_EVENTS_PER_REQUEST = 50_000
_RETENTION_SECONDS = 7 * 24 * 60 * 60


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _json_payload(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > _MAX_EVENT_CHARS:
        raise ValueError("Web Chat recovery event exceeds the size limit")
    return encoded


class WebChatEventJournal:
    """SQLite-backed append-only event journal with an owner-scoped text summary."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "web_chat_events.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    async def open(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_chat_event_state (
                    request_id TEXT PRIMARY KEY,
                    owner_key_id TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL DEFAULT 0,
                    worker_text TEXT NOT NULL DEFAULT '',
                    delivered_text TEXT NOT NULL DEFAULT '',
                    worker_text_truncated INTEGER NOT NULL DEFAULT 0,
                    delivered_text_truncated INTEGER NOT NULL DEFAULT 0,
                    terminal_status TEXT NOT NULL DEFAULT '',
                    terminal_message TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS web_chat_events (
                    request_id TEXT NOT NULL,
                    owner_key_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (request_id, sequence),
                    FOREIGN KEY (request_id)
                        REFERENCES web_chat_event_state(request_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_web_chat_events_retention
                    ON web_chat_events(created_at);
                """
            )
            cutoff = time.time() - _RETENTION_SECONDS
            connection.execute("DELETE FROM web_chat_events WHERE created_at < ?", (cutoff,))
            connection.execute(
                """
                DELETE FROM web_chat_event_state
                WHERE updated_at < ?
                  AND NOT EXISTS (
                    SELECT 1 FROM web_chat_events
                    WHERE web_chat_events.request_id = web_chat_event_state.request_id
                  )
                """,
                (cutoff,),
            )
            connection.commit()

    @staticmethod
    def _worker_record(event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        event_type = str(event.get("type") or "")
        if event_type == "chat.delta":
            delta = _text(event.get("delta"), 65_536)
            return ("worker.delta", {"delta": delta}) if delta else None
        if event_type == "chat.snapshot":
            snapshot = _text(event.get("text"), _MAX_TEXT_CHARS)
            return ("worker.snapshot", {"text": snapshot}) if snapshot else None
        if event_type in {"chat.tool.call", "chat.tool.result"}:
            field = "arguments" if event_type == "chat.tool.call" else "output"
            payload: dict[str, Any] = {
                "tool_name": _text(event.get("tool_name"), 80) or "tool",
                "call_id": _text(event.get("call_id"), 128),
                "result_id": _text(event.get("result_id"), 128),
                "status": _text(event.get("status"), 24),
                field: _text(event.get(field), _MAX_TOOL_TEXT_CHARS),
            }
            references = normalize_tool_references(event.get("references"))
            if references:
                payload["references"] = references
            return event_type, payload
        if event_type in {"chat.completed", "image.completed"}:
            terminal_text = event.get("text")
            return "worker.terminal", {
                "status": "completed",
                "kind": "image" if event_type == "image.completed" else "chat",
                "text": _text(terminal_text, 50_000) if event_type == "chat.completed" else "",
                "text_chars": len(terminal_text) if isinstance(terminal_text, str) else 0,
            }
        if event_type in {"chat.error", "chat.cancelled", "image.error", "image.cancelled"}:
            return "worker.terminal", {
                "status": "cancelled" if event_type in {"chat.cancelled", "image.cancelled"} else "error",
                "kind": "image" if event_type.startswith("image.") else "chat",
                "message": _text(event.get("error") or event.get("reason"), 500),
            }
        return None

    async def append_worker_event(
        self, request_id: str, owner_key_id: str, event: dict[str, Any],
    ) -> int | None:
        if not request_id or not owner_key_id or not isinstance(event, dict):
            return None
        record = self._worker_record(event)
        if record is None:
            return None
        rows = await asyncio.to_thread(
            self._append_records, request_id, owner_key_id, [record],
        )
        return rows[0] if rows else None

    async def append_delivery(
        self, request_id: str, owner_key_id: str, final_text: str,
    ) -> dict[str, Any]:
        if not request_id or not owner_key_id:
            raise ValueError("Web Chat event ownership is required")
        text = _text(final_text, _MAX_TEXT_CHARS)
        records = [
            ("delivery.delta", {"delta": text[start:start + FINAL_CHUNK_CHARS]})
            for start in range(0, len(text), FINAL_CHUNK_CHARS)
        ]
        sequences = await asyncio.to_thread(
            self._append_records, request_id, owner_key_id, records,
        ) if records else []
        return {
            "chunk_event_ids": sequences,
            "terminal_event_id": 0,
            "text_chars": len(text),
        }

    async def append_delivery_terminal(
        self, request_id: str, owner_key_id: str, text_chars: int,
    ) -> int:
        if not request_id or not owner_key_id:
            raise ValueError("Web Chat event ownership is required")
        rows = await asyncio.to_thread(
            self._append_records, request_id, owner_key_id,
            [("delivery.completed", {"text_chars": max(0, int(text_chars))})],
        )
        return rows[0]

    async def append_terminal(
        self, request_id: str, owner_key_id: str, status: str, message: str = "",
    ) -> int | None:
        event_type = {
            "error": "delivery.error",
            "cancelled": "delivery.cancelled",
            "stalled": "delivery.stalled",
        }.get(str(status or "").lower())
        if not event_type or not request_id or not owner_key_id:
            return None
        rows = await asyncio.to_thread(
            self._append_records,
            request_id,
            owner_key_id,
            [(event_type, {"status": status, "message": _text(message, 500)})],
        )
        return rows[0] if rows else None

    def _append_records(
        self,
        request_id: str,
        owner_key_id: str,
        records: list[tuple[str, dict[str, Any]]],
    ) -> list[int]:
        if not records:
            return []
        now = time.time()
        encoded = [(kind, _json_payload(payload)) for kind, payload in records]
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT * FROM web_chat_event_state WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if state is not None and str(state["owner_key_id"]) != owner_key_id:
                connection.rollback()
                raise PermissionError("Web Chat event request owner mismatch")
            if state is not None and str(state["terminal_status"] or ""):
                # The first durable delivery terminal is absorbing. Do not
                # append late Worker retries, delivery chunks, or competing
                # terminal markers after a client may have observed finality.
                connection.rollback()
                return []
            if state is None:
                connection.execute(
                    """
                    INSERT INTO web_chat_event_state(request_id, owner_key_id, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (request_id, owner_key_id, now),
                )
                last_sequence = 0
                worker_text = ""
                delivered_text = ""
                worker_truncated = False
                delivered_truncated = False
                terminal_status = ""
                terminal_message = ""
            else:
                last_sequence = int(state["last_sequence"])
                worker_text = str(state["worker_text"] or "")
                delivered_text = str(state["delivered_text"] or "")
                worker_truncated = bool(state["worker_text_truncated"])
                delivered_truncated = bool(state["delivered_text_truncated"])
                terminal_status = str(state["terminal_status"] or "")
                terminal_message = str(state["terminal_message"] or "")

            sequences: list[int] = []
            for (event_type, payload_json), (_, payload) in zip(encoded, records):
                last_sequence += 1
                sequences.append(last_sequence)
                connection.execute(
                    """
                    INSERT INTO web_chat_events(
                        request_id, owner_key_id, sequence, event_type, payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (request_id, owner_key_id, last_sequence, event_type, payload_json, now),
                )
                if event_type == "worker.delta":
                    addition = _text(payload.get("delta"), 65_536)
                    remaining = max(0, _MAX_TEXT_CHARS - len(worker_text))
                    worker_text += addition[:remaining]
                    worker_truncated = worker_truncated or len(addition) > remaining
                elif event_type == "worker.snapshot":
                    snapshot = _text(payload.get("text"), _MAX_TEXT_CHARS)
                    if snapshot.startswith(worker_text):
                        worker_text = snapshot
                    elif worker_text.startswith(snapshot):
                        pass
                    else:
                        # Conflicting snapshots are evidence only; do not
                        # regress or splice unrelated provisional text.
                        pass
                elif event_type == "worker.terminal":
                    if payload.get("status") == "completed" and payload.get("kind") == "chat":
                        terminal_text = _text(payload.get("text"), 50_000)
                        if terminal_text:
                            # The terminal text is the Worker controller's final
                            # body. Persist a bounded forensic copy before the
                            # server ACK, but never promote it to delivered_text.
                            worker_text = terminal_text
                            worker_truncated = int(payload.get("text_chars") or 0) > len(terminal_text)
                elif event_type == "delivery.delta":
                    addition = _text(payload.get("delta"), FINAL_CHUNK_CHARS)
                    remaining = max(0, _MAX_TEXT_CHARS - len(delivered_text))
                    delivered_text += addition[:remaining]
                    delivered_truncated = delivered_truncated or len(addition) > remaining
                elif event_type == "delivery.completed":
                    if not terminal_status:
                        terminal_status = "completed"
                        terminal_message = ""
                elif event_type in {"delivery.error", "delivery.cancelled", "delivery.stalled"}:
                    # The first durable terminal outcome is authoritative.
                    # Late retries or delayed terminal messages must not
                    # rewrite a completed/error/cancelled/stalled request.
                    if not terminal_status:
                        terminal_status = str(payload.get("status") or event_type.removeprefix("delivery."))
                        terminal_message = _text(payload.get("message"), 500)

            connection.execute(
                """
                UPDATE web_chat_event_state
                SET last_sequence = ?, worker_text = ?, delivered_text = ?,
                    worker_text_truncated = ?, delivered_text_truncated = ?,
                    terminal_status = ?, terminal_message = ?, updated_at = ?
                WHERE request_id = ? AND owner_key_id = ?
                """,
                (
                    last_sequence, worker_text, delivered_text,
                    int(worker_truncated), int(delivered_truncated),
                    terminal_status, terminal_message, now, request_id, owner_key_id,
                ),
            )
            connection.execute(
                """
                DELETE FROM web_chat_events
                WHERE request_id = ? AND sequence <= ?
                """,
                (request_id, max(0, last_sequence - _MAX_EVENTS_PER_REQUEST)),
            )
            connection.commit()
        return sequences

    async def events_after(
        self, request_id: str, owner_key_id: str, after_sequence: int,
        limit: int = 128,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._events_after, request_id, owner_key_id, after_sequence, limit,
        )

    def _events_after(
        self, request_id: str, owner_key_id: str, after_sequence: int,
        limit: int,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 512))
        with closing(self._connect()) as connection:
            state = connection.execute(
                """
                SELECT * FROM web_chat_event_state
                WHERE request_id = ? AND owner_key_id = ?
                """,
                (request_id, owner_key_id),
            ).fetchone()
            if state is None:
                return {"events": [], "first_event_id": None, "last_event_id": 0}
            first = connection.execute(
                """
                SELECT MIN(sequence) AS first_sequence
                FROM web_chat_events
                WHERE request_id = ? AND owner_key_id = ?
                """,
                (request_id, owner_key_id),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT sequence, event_type, payload
                FROM web_chat_events
                WHERE request_id = ? AND owner_key_id = ? AND sequence > ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (request_id, owner_key_id, max(0, int(after_sequence)), bounded_limit),
            ).fetchall()
        events = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload"]))
            except (ValueError, TypeError):
                payload = {}
            events.append({
                "id": int(row["sequence"]),
                "type": str(row["event_type"]),
                "data": payload if isinstance(payload, dict) else {},
            })
        return {
            "events": events,
            "first_event_id": int(first["first_sequence"]) if first and first["first_sequence"] is not None else None,
            "last_event_id": int(state["last_sequence"]),
            "terminal_status": str(state["terminal_status"] or ""),
        }

    async def snapshot(self, request_id: str, owner_key_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._snapshot, request_id, owner_key_id)

    def _snapshot(self, request_id: str, owner_key_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT last_sequence, worker_text, delivered_text,
                       worker_text_truncated, delivered_text_truncated,
                       terminal_status, terminal_message, updated_at
                FROM web_chat_event_state
                WHERE request_id = ? AND owner_key_id = ?
                """,
                (request_id, owner_key_id),
            ).fetchone()
        if row is None:
            return {
                "last_event_id": 0, "worker_partial_text": "",
                "delivered_text": "", "terminal_status": "",
                "terminal_message": "", "worker_text_truncated": False,
                "delivered_text_truncated": False,
            }
        return {
            "last_event_id": int(row["last_sequence"]),
            "worker_partial_text": str(row["worker_text"] or ""),
            "delivered_text": str(row["delivered_text"] or ""),
            "terminal_status": str(row["terminal_status"] or ""),
            "terminal_message": str(row["terminal_message"] or ""),
            "worker_text_truncated": bool(row["worker_text_truncated"]),
            "delivered_text_truncated": bool(row["delivered_text_truncated"]),
        }
