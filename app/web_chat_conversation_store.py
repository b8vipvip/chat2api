"""W-02 owner-scoped Web Chat conversation persistence.

This store owns only browser conversation projections. It must never decide a
Worker/Broker request terminal, API key entitlement, or model provenance.
SQLite is a single-host interim store, not an encrypted multi-instance ledger.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from pathlib import Path
from typing import Any


MAX_CONVERSATIONS_PER_USER = 200


class ConversationConflict(Exception):
    """Concurrent edit, tombstone, or idempotency-key reuse."""


class ConversationLimit(Exception):
    """Account conversation cap reached."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(document: dict[str, Any]) -> str:
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WebChatConversationStore:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "web_chat_conversations.sqlite3"
        self._init_lock = threading.Lock()
        with self._init_lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_chat_conversations (
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    model TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    last_mutation_id TEXT NOT NULL DEFAULT '',
                    last_mutation_digest TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(user_id, conversation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_web_chat_owner_updated
                    ON web_chat_conversations(user_id, deleted_at, updated_at DESC, conversation_id DESC);
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _public(row: sqlite3.Row, *, include_messages: bool = True) -> dict[str, Any]:
        result = {
            "id": row["conversation_id"],
            "title": row["title"],
            "model": row["model"],
            "revision": row["revision"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        if include_messages:
            result["messages"] = json.loads(row["messages_json"])
        return result

    def list(self, user_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        with self._connect() as db:
            total = db.execute(
                "SELECT count(*) FROM web_chat_conversations WHERE user_id=? AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()[0]
            rows = db.execute(
                """SELECT * FROM web_chat_conversations
                   WHERE user_id=? AND deleted_at IS NULL
                   ORDER BY updated_at DESC, conversation_id DESC LIMIT ? OFFSET ?""",
                (user_id, limit, offset),
            ).fetchall()
        return {
            "data": [self._public(row, include_messages=False) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM web_chat_conversations
                   WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL""",
                (user_id, conversation_id),
            ).fetchone()
        return self._public(row) if row else None

    def put(
        self,
        user_id: str,
        conversation_id: str,
        *,
        title: str,
        model: str,
        messages: list[dict[str, Any]],
        expected_revision: int,
        mutation_id: str,
    ) -> dict[str, Any]:
        document = {"title": title, "model": model, "messages": messages}
        digest = _digest(document)
        now = _utcnow()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM web_chat_conversations WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            ).fetchone()
            if existing is not None:
                if existing["last_mutation_id"] == mutation_id:
                    if existing["last_mutation_digest"] != digest:
                        raise ConversationConflict("mutation_id reused with different content")
                    if existing["deleted_at"] is not None:
                        raise ConversationConflict("conversation already deleted")
                    return self._public(existing)
                if existing["deleted_at"] is not None:
                    raise ConversationConflict("deleted conversations cannot be resurrected")
                if existing["revision"] != expected_revision:
                    raise ConversationConflict("conversation changed on another device")
                db.execute(
                    """UPDATE web_chat_conversations
                       SET title=?, model=?, messages_json=?, revision=revision+1,
                           updated_at=?, last_mutation_id=?, last_mutation_digest=?
                       WHERE user_id=? AND conversation_id=?""",
                    (title, model, json.dumps(messages, ensure_ascii=False), now,
                     mutation_id, digest, user_id, conversation_id),
                )
            else:
                if expected_revision != 0:
                    raise ConversationConflict("conversation not found at expected revision")
                count = db.execute(
                    "SELECT count(*) FROM web_chat_conversations WHERE user_id=? AND deleted_at IS NULL",
                    (user_id,),
                ).fetchone()[0]
                if count >= MAX_CONVERSATIONS_PER_USER:
                    raise ConversationLimit("conversation limit exceeded")
                db.execute(
                    """INSERT INTO web_chat_conversations
                       (user_id, conversation_id, title, model, messages_json, revision,
                        created_at, updated_at, last_mutation_id, last_mutation_digest)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (user_id, conversation_id, title, model, json.dumps(messages, ensure_ascii=False),
                     1, now, now, mutation_id, digest),
                )
            row = db.execute(
                "SELECT * FROM web_chat_conversations WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            ).fetchone()
            return self._public(row)

    def delete(self, user_id: str, conversation_id: str, *, expected_revision: int) -> bool:
        """Tombstones prevent stale offline clients from silently recreating a deleted chat."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision, deleted_at FROM web_chat_conversations WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            ).fetchone()
            if row is None:
                return False
            if row["deleted_at"] is not None:
                return True  # A retried DELETE remains idempotent for its owner.
            if row["revision"] != expected_revision:
                raise ConversationConflict("conversation changed on another device")
            db.execute(
                """UPDATE web_chat_conversations SET deleted_at=?, messages_json='[]',
                   revision=revision+1, updated_at=? WHERE user_id=? AND conversation_id=?""",
                (_utcnow(), _utcnow(), user_id, conversation_id),
            )
            return True
