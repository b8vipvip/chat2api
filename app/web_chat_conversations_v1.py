"""Durable, owner-scoped persistence for the Mode 1 Web Chat conversation list."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .user_commerce import USER_SESSION_COOKIE
from .chat_tool_references_v1 import normalize_tool_references

_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie"}
_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_REQUEST_ID_RE = re.compile(r"^req_[A-Za-z0-9_-]{8,120}$")
_TOOL_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TOOL_EVENT_STATUSES = frozenset({"running", "completed", "failed", "cancelled", "unknown"})
_MAX_TOOL_EVENTS_PER_MESSAGE = 6
_MAX_TOOL_EVENT_TEXT = 1200
_FILE_ID_RE = re.compile(r"^file_[A-Za-z0-9]{8,64}$")
_ALLOWED_DOCUMENT_MIME_TYPES = frozenset({
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
})
_MAX_CONVERSATIONS_RETURNED = 100
_MAX_MESSAGES = 30
_MAX_MESSAGE_CHARS = 8000
_MAX_MESSAGE_PARTS = 8
_MAX_ATTACHMENTS_PER_MESSAGE = 4
_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
_MAX_CONVERSATION_BYTES = 512 * 1024
_MAX_SEARCH_QUERY_CHARS = 120
_MAX_SEARCH_RESULTS = 100
_CONVERSATION_EXPORT_FORMAT = "chat2api.web-chat-conversation"
_MAX_RETENTION_DAYS = 3650


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_string(value: Any, field: str, limit: int, *, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{field} is invalid")
    return value


def _normalize_content(value: Any, role: str) -> tuple[str | list[dict[str, Any]], bool]:
    if isinstance(value, str):
        if len(value) > _MAX_MESSAGE_CHARS:
            raise ValueError("message content is too large")
        return value, False
    if role != "user" or not isinstance(value, list) or len(value) > _MAX_MESSAGE_PARTS:
        raise ValueError("message content is invalid")
    parts: list[dict[str, Any]] = []
    attachments = 0
    for part in value:
        if not isinstance(part, dict):
            raise ValueError("message content part is invalid")
        kind = part.get("type")
        if kind == "text":
            text = part.get("text")
            if not isinstance(text, str) or len(text) > _MAX_MESSAGE_CHARS:
                raise ValueError("message text part is invalid")
            parts.append({"type": "text", "text": text})
        elif kind == "image_url":
            image = part.get("image_url")
            url = image if isinstance(image, str) else (
                image.get("url") if isinstance(image, dict) else None
            )
            if not isinstance(url, str) or not _FILE_ID_RE.fullmatch(url):
                raise ValueError("image attachment reference is invalid")
            attachments += 1
            parts.append({"type": "image_url", "image_url": {"url": url}})
        elif kind == "file":
            file_id = part.get("file_id")
            mime_type = str(part.get("mime_type") or "").lower()
            filename = part.get("filename", "document")
            size = part.get("size", 0)
            if not isinstance(file_id, str) or not _FILE_ID_RE.fullmatch(file_id):
                raise ValueError("document attachment reference is invalid")
            if mime_type not in _ALLOWED_DOCUMENT_MIME_TYPES:
                raise ValueError("document attachment type is invalid")
            if not isinstance(filename, str) or len(filename) > 180:
                raise ValueError("document filename is invalid")
            filename = "".join("_" if ord(char) < 32 or char in "/\\:" else char for char in filename)
            if type(size) is not int or size < 0 or size > _MAX_DOCUMENT_BYTES:
                raise ValueError("document attachment size is invalid")
            attachments += 1
            parts.append({
                "type": "file", "file_id": file_id, "filename": filename,
                "mime_type": mime_type, "size": size,
            })
        else:
            raise ValueError("message content part type is unsupported")
    if attachments > _MAX_ATTACHMENTS_PER_MESSAGE or not parts:
        raise ValueError("message attachment count is invalid")
    return parts, attachments > 0


def _normalize_tool_events(value: Any, role: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if role != "assistant" or not isinstance(value, list) or len(value) > _MAX_TOOL_EVENTS_PER_MESSAGE:
        raise ValueError("message tool activity is invalid")
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("message tool activity item is invalid")
        tool_name = _bounded_string(item.get("toolName", "tool"), "tool name", 80, default="tool")
        call_id = _bounded_string(item.get("callId", ""), "tool call ID", 128)
        result_id = _bounded_string(item.get("resultId", ""), "tool result ID", 128)
        status = _bounded_string(item.get("status", "unknown"), "tool status", 20, default="unknown")
        arguments = _bounded_string(item.get("arguments", ""), "tool arguments", _MAX_TOOL_EVENT_TEXT)
        output = _bounded_string(item.get("output", ""), "tool output", _MAX_TOOL_EVENT_TEXT)
        if (not tool_name or not (call_id or result_id)
            or (call_id and not _TOOL_EVENT_ID_RE.fullmatch(call_id))
            or (result_id and not _TOOL_EVENT_ID_RE.fullmatch(result_id))
            or status not in _TOOL_EVENT_STATUSES):
            raise ValueError("message tool activity fields are invalid")
        normalized = {
            "toolName": tool_name, "callId": call_id, "resultId": result_id,
            "status": status, "arguments": arguments, "output": output,
        }
        references = normalize_tool_references(item.get("references"))
        if references:
            normalized["references"] = references
        rows.append(normalized)
    return rows


def normalize_conversation(
    conversation_id: str,
    raw: Any,
    *,
    accounts: Any,
    telemetry: Any,
    user_id: str,
    file_store: Any = None,
) -> dict[str, Any]:
    if not _CONVERSATION_ID_RE.fullmatch(conversation_id) or not isinstance(raw, dict):
        raise ValueError("conversation is invalid")
    supplied_id = raw.get("id")
    if supplied_id != conversation_id:
        raise ValueError("conversation ID does not match its route")
    title = _bounded_string(raw.get("title", "新对话"), "conversation title", 80, default="新对话")
    model = _bounded_string(raw.get("model", ""), "conversation model", 120)
    api_key_id = _bounded_string(raw.get("apiKeyId", ""), "conversation API Key ID", 128)
    if api_key_id and not accounts.owns_key(user_id, api_key_id):
        raise LookupError("conversation API Key does not belong to this user")
    messages_raw = raw.get("messages", [])
    if not isinstance(messages_raw, list) or len(messages_raw) > _MAX_MESSAGES:
        raise ValueError("conversation message count is invalid")
    messages: list[dict[str, Any]] = []
    seen_message_ids: set[str] = set()
    has_attachments = False
    for message in messages_raw:
        if not isinstance(message, dict):
            raise ValueError("conversation message is invalid")
        message_id = message.get("id")
        role = message.get("role")
        if not isinstance(message_id, str) or not _MESSAGE_ID_RE.fullmatch(message_id):
            raise ValueError("conversation message ID is invalid")
        if message_id in seen_message_ids:
            raise ValueError("conversation message ID is duplicated")
        seen_message_ids.add(message_id)
        if role not in {"user", "assistant"}:
            raise ValueError("conversation message role is invalid")
        pending = message.get("pending", False)
        if type(pending) is not bool:
            raise ValueError("conversation message pending flag is invalid")
        content, has_message_attachments = _normalize_content(message.get("content", ""), role)
        if has_message_attachments:
            if file_store is None or not isinstance(content, list):
                raise LookupError("conversation attachment service is unavailable")
            for part in content:
                if part.get("type") == "image_url":
                    image = part.get("image_url")
                    file_id = str(image.get("url") or "") if isinstance(image, dict) else ""
                    item = file_store.get(file_id)
                    if (
                        item is None or item.purpose != "vision"
                        or item.mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}
                        or not accounts.owns_key(user_id, str(item.owner_key_id or ""))
                    ):
                        raise LookupError("image attachment does not belong to this user")
                elif part.get("type") == "file":
                    item = file_store.get(str(part.get("file_id") or ""))
                    if (
                        item is None or item.purpose != "assistants"
                        or item.mime_type not in _ALLOWED_DOCUMENT_MIME_TYPES
                        or not accounts.owns_key(user_id, str(item.owner_key_id or ""))
                    ):
                        raise LookupError("document attachment does not belong to this user")
                    # Stored metadata is authoritative; never persist a
                    # browser-provided filename, MIME type or byte count.
                    part["filename"] = str(item.filename or "document")[:180]
                    part["mime_type"] = str(item.mime_type or "").lower()
                    part["size"] = int(item.size)
        tool_events = _normalize_tool_events(message.get("toolEvents"), role)
        message_references = normalize_tool_references(
            [
                *(message.get("references") if isinstance(message.get("references"), list) else []),
                *(
                    reference
                    for event in tool_events
                    for reference in event.get("references", [])
                ),
            ] if role == "assistant" else []
        )
        if not content and not (role == "assistant" and pending) and not tool_events:
            raise ValueError("conversation message content is empty")
        if role == "assistant" and isinstance(content, list):
            raise ValueError("assistant message content must be text")
        has_attachments = has_attachments or has_message_attachments
        created_at = _bounded_string(message.get("createdAt", ""), "message createdAt", 80, default="")
        request_id = _bounded_string(message.get("requestId", ""), "message requestId", 140, default="")
        if request_id:
            if not _REQUEST_ID_RE.fullmatch(request_id):
                raise ValueError("message request ID is invalid")
            telemetry_row = telemetry.get(request_id) if telemetry is not None else None
            if telemetry_row:
                owner_key_id = str(telemetry_row.get("api_key_id") or "")
                if not owner_key_id or not accounts.owns_key(user_id, owner_key_id):
                    raise LookupError("message request does not belong to this user")
        recovery_status = _bounded_string(message.get("recoveryStatus", ""), "message recovery status", 40)
        last_event_id = message.get("lastEventId", 0)
        if type(last_event_id) is not int or last_event_id < 0 or last_event_id > 9_007_199_254_740_991:
            raise ValueError("conversation message last event ID is invalid")
        normalized_message = {
            "id": message_id,
            "role": role,
            "content": content,
            "createdAt": created_at or _now(),
            "requestId": request_id,
            "pending": pending,
            "recoveryStatus": recovery_status,
            "lastEventId": last_event_id,
        }
        if tool_events:
            normalized_message["toolEvents"] = tool_events
        if message_references:
            normalized_message["references"] = message_references
        messages.append(normalized_message)
    if has_attachments and not api_key_id:
        raise ValueError("conversations with attachments must retain their owning API Key")
    created_at = _bounded_string(raw.get("createdAt", ""), "conversation createdAt", 80, default="") or _now()
    updated_at = _bounded_string(raw.get("updatedAt", ""), "conversation updatedAt", 80, default="") or created_at
    normalized = {
        "id": conversation_id,
        "title": title,
        "model": model,
        "apiKeyId": api_key_id,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "messages": messages,
    }
    if len(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > _MAX_CONVERSATION_BYTES:
        raise ValueError("conversation is too large")
    return normalized


class WebChatConversationStoreV1:
    """SQLite-backed conversation rows with per-user ownership and optimistic revisions."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "web_chat_conversations_v1.sqlite3"
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise RuntimeError("Web Chat conversation database cannot be a symbolic link")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise RuntimeError("Web Chat conversation database must be a private regular file")
            if os.name != "nt" and hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > 3:
                raise RuntimeError("Web Chat conversation database version is newer than this application")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS web_chat_conversations (
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    updated_at TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
                    payload TEXT NOT NULL,
                    PRIMARY KEY (user_id, conversation_id)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_web_chat_conversations_owner_updated
                ON web_chat_conversations(user_id, updated_at DESC)
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS web_chat_file_references (
                    user_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    ref_count INTEGER NOT NULL CHECK (ref_count > 0),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, file_id)
                )
            """)
            if version < 2:
                # Backfill references from pre-v2 conversation rows before
                # enabling deletion guards. Without this migration, existing
                # saved attachments could be deleted as if they were orphans.
                connection.execute("BEGIN IMMEDIATE")
                try:
                    counts: dict[tuple[str, str], int] = {}
                    rows = connection.execute(
                        "SELECT user_id, updated_at, deleted, payload FROM web_chat_conversations"
                    ).fetchall()
                    for stored in rows:
                        if bool(stored["deleted"]):
                            continue
                        try:
                            payload = json.loads(str(stored["payload"]))
                        except (TypeError, ValueError, json.JSONDecodeError) as error:
                            raise RuntimeError(
                                "Stored Web Chat conversation cannot be migrated for attachment references"
                            ) from error
                        if not isinstance(payload, dict):
                            raise RuntimeError(
                                "Stored Web Chat conversation has an invalid attachment migration shape"
                            )
                        conversation_refs: set[str] = set()
                        messages = payload.get("messages", [])
                        if isinstance(messages, list):
                            for message in messages:
                                content = message.get("content") if isinstance(message, dict) else None
                                if not isinstance(content, list):
                                    continue
                                for part in content:
                                    if not isinstance(part, dict):
                                        continue
                                    if part.get("type") == "file":
                                        file_id = part.get("file_id")
                                    elif part.get("type") == "image_url":
                                        image = part.get("image_url")
                                        file_id = image.get("url") if isinstance(image, dict) else None
                                    else:
                                        file_id = None
                                    if isinstance(file_id, str) and _FILE_ID_RE.fullmatch(file_id):
                                        conversation_refs.add(file_id)
                        user_id = str(stored["user_id"] or "")
                        for file_id in conversation_refs:
                            key = (user_id, file_id)
                            counts[key] = counts.get(key, 0) + 1
                    for (user_id, file_id), count in counts.items():
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO web_chat_file_references
                                (user_id, file_id, ref_count, updated_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (user_id, file_id, count, _now()),
                        )
                    connection.execute("PRAGMA user_version=2")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            else:
                connection.execute("PRAGMA user_version=2")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS web_chat_policy (
                    policy_id INTEGER PRIMARY KEY CHECK (policy_id = 1),
                    retention_days INTEGER NOT NULL DEFAULT 0
                        CHECK (retention_days BETWEEN 0 AND 3650),
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute(
                "INSERT OR IGNORE INTO web_chat_policy(policy_id, retention_days, updated_at) "
                "VALUES (1, 0, ?)",
                (_now(),),
            )
            connection.execute("PRAGMA user_version=3")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload"]))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Stored Web Chat conversation is unreadable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("Stored Web Chat conversation has an invalid shape")
        return {
            **payload,
            "revision": int(row["revision"]),
            "deleted": bool(row["deleted"]),
        }

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT revision, deleted, payload
                FROM web_chat_conversations
                WHERE user_id=?
                ORDER BY updated_at DESC, conversation_id ASC
                LIMIT ?
                """,
                (user_id, _MAX_CONVERSATIONS_RETURNED),
            ).fetchall()
            return [self._decode(row) for row in rows if row is not None]

    def retention_policy(self) -> dict[str, Any]:
        with self.lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT retention_days, updated_at FROM web_chat_policy WHERE policy_id=1"
            ).fetchone()
            return {
                "retention_days": int(row["retention_days"]) if row else 0,
                "updated_at": str(row["updated_at"]) if row else _now(),
                "tombstones_retained": True,
            }

    def set_retention_days(self, retention_days: int) -> dict[str, Any]:
        if type(retention_days) is not int or not 0 <= retention_days <= _MAX_RETENTION_DAYS:
            raise ValueError("retention_days must be an integer between 0 and 3650")
        now = _now()
        with self.lock, closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO web_chat_policy(policy_id, retention_days, updated_at)
                   VALUES (1, ?, ?)
                   ON CONFLICT(policy_id) DO UPDATE SET
                       retention_days=excluded.retention_days,
                       updated_at=excluded.updated_at""",
                (retention_days, now),
            )
        return self.retention_policy()

    def expire_old_conversations(self, limit: int = 500) -> int:
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("retention batch limit is invalid")
        policy = self.retention_policy()
        days = int(policy["retention_days"])
        if days == 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        expired = 0
        with self.lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT user_id, conversation_id, revision, deleted, payload
                   FROM web_chat_conversations
                   WHERE deleted=0 AND updated_at<?
                   ORDER BY updated_at ASC, user_id ASC, conversation_id ASC
                   LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
            for stored in rows:
                current = self._decode(stored)
                if current is None or current.get("deleted"):
                    continue
                user_id = str(stored["user_id"] or "")
                conversation_id = str(stored["conversation_id"] or "")
                now = _now()
                self._adjust_file_references(
                    connection, user_id, self._file_ids(current), set(), now,
                )
                tombstone = {
                    "id": conversation_id,
                    "title": "",
                    "model": "",
                    "apiKeyId": "",
                    "createdAt": str(current.get("createdAt") or now),
                    "updatedAt": now,
                    "messages": [],
                }
                changed = connection.execute(
                    """UPDATE web_chat_conversations
                       SET revision=revision+1, updated_at=?, deleted=1, payload=?
                       WHERE user_id=? AND conversation_id=? AND revision=? AND deleted=0""",
                    (
                        now,
                        json.dumps(tombstone, ensure_ascii=False, separators=(",", ":")),
                        user_id,
                        conversation_id,
                        int(stored["revision"]),
                    ),
                ).rowcount
                expired += int(changed == 1)
            connection.commit()
        return expired

    def get_for_user(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        with self.lock, closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT revision, deleted, payload
                   FROM web_chat_conversations
                   WHERE user_id=? AND conversation_id=?""",
                (user_id, conversation_id),
            ).fetchone()
            decoded = self._decode(row)
            if decoded is None or decoded.get("deleted"):
                return None
            return decoded

    def search_for_user(
        self, user_id: str, query: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized = query.strip()
        if not normalized or len(normalized) > _MAX_SEARCH_QUERY_CHARS:
            raise ValueError("search query is invalid")
        if type(limit) is not int or not 1 <= limit <= _MAX_SEARCH_RESULTS:
            raise ValueError("search result limit is invalid")
        # instr() treats %, _ and backslashes literally; unlike LIKE it cannot
        # turn user input into an unbounded wildcard scan pattern.
        with self.lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT revision, deleted, payload
                   FROM web_chat_conversations
                   WHERE user_id=? AND deleted=0
                     AND instr(lower(payload), lower(?)) > 0
                   ORDER BY updated_at DESC, conversation_id ASC
                   LIMIT ?""",
                (user_id, normalized, limit),
            ).fetchall()
            return [self._decode(row) for row in rows if row is not None]

    @staticmethod
    def _file_ids(conversation: dict[str, Any] | None) -> set[str]:
        found: set[str] = set()
        for message in (conversation or {}).get("messages", []):
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "file":
                    file_id = part.get("file_id")
                elif part.get("type") == "image_url":
                    image = part.get("image_url")
                    file_id = image.get("url") if isinstance(image, dict) else None
                else:
                    file_id = None
                if isinstance(file_id, str) and _FILE_ID_RE.fullmatch(file_id):
                    found.add(file_id)
        return found

    @staticmethod
    def _adjust_file_references(
        connection: sqlite3.Connection, user_id: str,
        old_ids: set[str], new_ids: set[str], updated_at: str,
    ) -> None:
        for file_id in old_ids - new_ids:
            row = connection.execute(
                "SELECT ref_count FROM web_chat_file_references WHERE user_id=? AND file_id=?",
                (user_id, file_id),
            ).fetchone()
            if row is None or int(row["ref_count"]) <= 1:
                connection.execute(
                    "DELETE FROM web_chat_file_references WHERE user_id=? AND file_id=?",
                    (user_id, file_id),
                )
            else:
                connection.execute(
                    "UPDATE web_chat_file_references SET ref_count=ref_count-1, updated_at=? WHERE user_id=? AND file_id=?",
                    (updated_at, user_id, file_id),
                )
        for file_id in new_ids - old_ids:
            connection.execute(
                """
                INSERT INTO web_chat_file_references(user_id, file_id, ref_count, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(user_id, file_id) DO UPDATE SET
                    ref_count=web_chat_file_references.ref_count+1,
                    updated_at=excluded.updated_at
                """,
                (user_id, file_id, updated_at),
            )

    def file_reference_count(self, file_id: str, user_id: str | None = None) -> int:
        with self.lock, closing(self._connect()) as connection:
            if user_id is None:
                row = connection.execute(
                    "SELECT COALESCE(SUM(ref_count), 0) AS count FROM web_chat_file_references WHERE file_id=?",
                    (file_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT ref_count AS count FROM web_chat_file_references WHERE user_id=? AND file_id=?",
                    (user_id, file_id),
                ).fetchone()
            return int(row["count"] or 0) if row else 0

    def referenced_file_ids(self) -> set[str]:
        with self.lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT file_id FROM web_chat_file_references WHERE ref_count > 0"
            ).fetchall()
            return {str(row["file_id"]) for row in rows}

    @contextmanager
    def file_reference_snapshot_guard(self):
        """Hold the reference DB write lock while an orphan scan deletes files."""
        with self.lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    "SELECT file_id FROM web_chat_file_references WHERE ref_count > 0"
                ).fetchall()
                yield {str(row["file_id"]) for row in rows}
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @contextmanager
    def file_deletion_guard(self, file_id: str):
        """Serialize a file unlink against conversation reference transactions."""
        with self.lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT COALESCE(SUM(ref_count), 0) AS count "
                    "FROM web_chat_file_references WHERE file_id=?",
                    (file_id,),
                ).fetchone()
                yield not bool(row and int(row["count"] or 0) > 0)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def upsert(
        self, user_id: str, conversation: dict[str, Any], expected_revision: int,
        file_store: Any = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        conversation_id = str(conversation["id"])
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT revision, deleted, payload
                FROM web_chat_conversations WHERE user_id=? AND conversation_id=?
                """,
                (user_id, conversation_id),
            ).fetchone()
            current = self._decode(row)
            current_revision = int(current["revision"]) if current else 0
            if current and current["deleted"]:
                connection.execute("ROLLBACK")
                return False, current
            if current_revision != expected_revision:
                connection.execute("ROLLBACK")
                return False, current
            # Re-check file existence after acquiring SQLite's write lock.
            # A different server process may have deleted a file after the
            # route's initial metadata validation but before this transaction.
            new_file_ids = self._file_ids(conversation)
            if new_file_ids:
                if file_store is None:
                    connection.execute("ROLLBACK")
                    raise LookupError("conversation attachment service is unavailable")
                for file_id in new_file_ids:
                    item = file_store.get(file_id)
                    if item is None:
                        connection.execute("ROLLBACK")
                        raise LookupError("conversation attachment no longer exists")
                    try:
                        metadata = (file_store.root / item.path).lstat()
                    except OSError as error:
                        connection.execute("ROLLBACK")
                        raise LookupError("conversation attachment content is unavailable") from error
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1
                        or metadata.st_size != item.size
                    ):
                        connection.execute("ROLLBACK")
                        raise LookupError("conversation attachment content is unavailable")
            revision = current_revision + 1
            saved = dict(conversation)
            if current:
                saved["createdAt"] = str(current.get("createdAt") or saved["createdAt"])
            saved["updatedAt"] = _now()
            serialized = json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
            self._adjust_file_references(
                connection, user_id,
                self._file_ids(current if current and not current.get("deleted") else None),
                self._file_ids(saved),
                saved["updatedAt"],
            )
            connection.execute(
                """
                INSERT INTO web_chat_conversations
                    (user_id, conversation_id, revision, updated_at, deleted, payload)
                VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(user_id, conversation_id) DO UPDATE SET
                    revision=excluded.revision,
                    updated_at=excluded.updated_at,
                    deleted=0,
                    payload=excluded.payload
                """,
                (user_id, conversation_id, revision, saved["updatedAt"], serialized),
            )
            connection.execute("COMMIT")
            return True, {**saved, "revision": revision, "deleted": False}

    def delete(
        self, user_id: str, conversation_id: str, expected_revision: int,
    ) -> tuple[bool, dict[str, Any] | None]:
        with self.lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT revision, deleted, payload
                FROM web_chat_conversations WHERE user_id=? AND conversation_id=?
                """,
                (user_id, conversation_id),
            ).fetchone()
            current = self._decode(row)
            current_revision = int(current["revision"]) if current else 0
            if current and current["deleted"]:
                connection.execute("ROLLBACK")
                return True, current
            if current_revision != expected_revision:
                connection.execute("ROLLBACK")
                return False, current
            revision = current_revision + 1
            now = _now()
            self._adjust_file_references(
                connection, user_id,
                self._file_ids(current if current and not current.get("deleted") else None),
                set(), now,
            )
            if current:
                payload = {
                    "id": conversation_id,
                    "title": "",
                    "model": "",
                    "apiKeyId": "",
                    "createdAt": str(current.get("createdAt") or now),
                    "updatedAt": now,
                    "messages": [],
                }
            else:
                payload = {
                    "id": conversation_id,
                    "title": "",
                    "model": "",
                    "apiKeyId": "",
                    "createdAt": now,
                    "updatedAt": now,
                    "messages": [],
                }
            connection.execute(
                """
                INSERT INTO web_chat_conversations
                    (user_id, conversation_id, revision, updated_at, deleted, payload)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(user_id, conversation_id) DO UPDATE SET
                    revision=excluded.revision,
                    updated_at=excluded.updated_at,
                    deleted=1,
                    payload=excluded.payload
                """,
                (user_id, conversation_id, revision, now, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            )
            connection.execute("COMMIT")
            return True, {**payload, "revision": revision, "deleted": True}


def install_web_chat_conversations_v1(app: FastAPI) -> None:
    if getattr(app.state, "web_chat_conversations_v1_installed", False):
        return
    store = WebChatConversationStoreV1(Path(app.state.settings.data_dir))
    app.state.web_chat_conversations = store

    def current_user(request: Request) -> tuple[Any, dict[str, Any]]:
        accounts = getattr(request.app.state, "user_accounts", None)
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE)) if accounts else None
        if not user:
            raise HTTPException(status_code=401, detail="user login required", headers=_PRIVATE_HEADERS)
        return accounts, user

    @app.get("/api/user/chat/conversations")
    async def list_user_chat_conversations(request: Request):
        _, user = current_user(request)
        rows = store.list_for_user(str(user["user_id"]))
        return JSONResponse({"data": rows}, headers=_PRIVATE_HEADERS)

    @app.get("/api/user/chat/conversations/search")
    async def search_user_chat_conversations(request: Request):
        _, user = current_user(request)
        query = str(request.query_params.get("q") or "")
        try:
            limit = int(request.query_params.get("limit") or "50")
            rows = store.search_for_user(str(user["user_id"]), query, limit)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="search query or limit is invalid", headers=_PRIVATE_HEADERS)
        return JSONResponse({"data": rows}, headers=_PRIVATE_HEADERS)

    @app.get("/api/user/chat/conversations/{conversation_id}/export")
    async def export_user_chat_conversation(conversation_id: str, request: Request):
        _, user = current_user(request)
        if not _CONVERSATION_ID_RE.fullmatch(conversation_id):
            raise HTTPException(status_code=404, detail="conversation not found", headers=_PRIVATE_HEADERS)
        row = store.get_for_user(str(user["user_id"]), conversation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="conversation not found", headers=_PRIVATE_HEADERS)
        exported = dict(row)
        exported.pop("revision", None)
        exported.pop("deleted", None)
        return JSONResponse(
            {
                "format": _CONVERSATION_EXPORT_FORMAT,
                "version": 1,
                "exportedAt": _now(),
                "conversation": exported,
            },
            headers={
                **_PRIVATE_HEADERS,
                "Content-Disposition": f'attachment; filename="chat2api-conversation-{conversation_id}.json"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/user/chat/conversations/import")
    async def import_user_chat_conversation(request: Request):
        accounts, user = current_user(request)
        raw_body = await request.body()
        if len(raw_body) > _MAX_CONVERSATION_BYTES + 8192:
            raise HTTPException(status_code=413, detail="conversation import is too large", headers=_PRIVATE_HEADERS)
        try:
            body = json.loads(raw_body)
        except (TypeError, ValueError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON body", headers=_PRIVATE_HEADERS)
        if (
            not isinstance(body, dict)
            or body.get("format") != _CONVERSATION_EXPORT_FORMAT
            or type(body.get("version")) is not int
            or body.get("version") != 1
            or not isinstance(body.get("conversation"), dict)
        ):
            raise HTTPException(status_code=422, detail="conversation export format is unsupported", headers=_PRIVATE_HEADERS)
        conversation_id = str(body["conversation"].get("id") or "")
        file_store = getattr(request.app.state, "file_store", None)
        if file_store is None:
            raise HTTPException(status_code=503, detail="file service unavailable", headers=_PRIVATE_HEADERS)
        with file_store.lock:
            try:
                conversation = normalize_conversation(
                    conversation_id, body["conversation"],
                    accounts=accounts, telemetry=request.app.state.telemetry,
                    user_id=str(user["user_id"]), file_store=file_store,
                )
                saved, row = store.upsert(
                    str(user["user_id"]), conversation, 0, file_store,
                )
            except LookupError:
                raise HTTPException(status_code=404, detail="conversation resource not found", headers=_PRIVATE_HEADERS)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error), headers=_PRIVATE_HEADERS)
        if not saved:
            return JSONResponse(
                {"detail": "conversation ID already exists", "conversation": row},
                status_code=409, headers=_PRIVATE_HEADERS,
            )
        return JSONResponse({"conversation": row}, status_code=201, headers=_PRIVATE_HEADERS)

    @app.put("/api/user/chat/conversations/{conversation_id}")
    async def save_user_chat_conversation(conversation_id: str, request: Request):
        accounts, user = current_user(request)
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON body", headers=_PRIVATE_HEADERS)
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="conversation payload must be an object", headers=_PRIVATE_HEADERS)
        expected_revision = body.get("expected_revision")
        if type(expected_revision) is not int or expected_revision < 0:
            raise HTTPException(status_code=422, detail="expected_revision is invalid", headers=_PRIVATE_HEADERS)
        file_store = getattr(request.app.state, "file_store", None)
        if file_store is None:
            raise HTTPException(status_code=503, detail="file service unavailable", headers=_PRIVATE_HEADERS)
        # Serialize attachment validation/reference increments with user/API
        # file deletion in this process. The SQLite conversation transaction
        # then commits the new reference counts atomically with the revision.
        with file_store.lock:
            try:
                conversation = normalize_conversation(
                    conversation_id, body.get("conversation"),
                    accounts=accounts, telemetry=request.app.state.telemetry,
                    user_id=str(user["user_id"]),
                    file_store=file_store,
                )
            except LookupError:
                raise HTTPException(status_code=404, detail="conversation resource not found", headers=_PRIVATE_HEADERS)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error), headers=_PRIVATE_HEADERS)
            try:
                saved, row = store.upsert(
                    str(user["user_id"]), conversation, expected_revision, file_store,
                )
            except LookupError:
                raise HTTPException(status_code=404, detail="conversation attachment no longer exists", headers=_PRIVATE_HEADERS)
        if not saved:
            return JSONResponse(
                {"detail": "conversation revision conflict", "conversation": row},
                status_code=409, headers=_PRIVATE_HEADERS,
            )
        return JSONResponse({"conversation": row}, headers=_PRIVATE_HEADERS)

    @app.delete("/api/user/chat/conversations/{conversation_id}")
    async def delete_user_chat_conversation(conversation_id: str, request: Request):
        _, user = current_user(request)
        raw_revision = request.query_params.get("expected_revision")
        try:
            expected_revision = int(raw_revision) if raw_revision is not None else -1
        except (TypeError, ValueError):
            expected_revision = -1
        if expected_revision < 0:
            raise HTTPException(status_code=422, detail="expected_revision is invalid", headers=_PRIVATE_HEADERS)
        if not _CONVERSATION_ID_RE.fullmatch(conversation_id):
            raise HTTPException(status_code=404, detail="conversation not found", headers=_PRIVATE_HEADERS)
        deleted, row = store.delete(str(user["user_id"]), conversation_id, expected_revision)
        if not deleted:
            return JSONResponse(
                {"detail": "conversation revision conflict", "conversation": row},
                status_code=409, headers=_PRIVATE_HEADERS,
            )
        return JSONResponse({"conversation": row}, headers=_PRIVATE_HEADERS)

    app.state.web_chat_conversations_v1_installed = True
