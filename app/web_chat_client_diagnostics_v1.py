"""Authenticated, privacy-bounded Web Chat client diagnostic events and export API."""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import threading
import time
import zipfile
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .admin_auth import SESSION_COOKIE, same_origin_admin_request
from .private_storage import (
    ensure_private_directory,
    open_private_regular_file,
    read_private_bytes,
    write_private_bytes_atomic,
)
from .user_commerce import USER_SESSION_COOKIE


_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie"}
_EVENT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_REQUEST_ID_RE = re.compile(r"^req_[A-Za-z0-9_-]{8,120}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_ALLOWED_DETAIL_KEYS = frozenset({
    "phase", "feature", "code", "reason", "model_id", "request_id",
    "model_count", "file_count", "image_count", "message_count",
    "duration_ms", "http_status", "retryable", "connection_state",
    "sync_revision", "client_version", "browser", "platform", "status",
})
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)
_STORE_NAME = "web_chat_client_diagnostics"
_STORE_FILE = "events.jsonl"
_MAX_ITEMS = 20_000
_MAX_FILE_BYTES = 16 * 1024 * 1024
_COMPACT_ITEMS = 10_000


class ClientDiagnosticEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_event_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    event: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    level: Literal["info", "warning", "error"] = "info"
    occurred_at: str = Field(default="", max_length=64)
    session_id: str = Field(default="", max_length=80)
    request_id: str | None = Field(default=None, max_length=120)
    model_id: str | None = Field(default=None, max_length=120)
    phase: str | None = Field(default=None, max_length=80)
    duration_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    http_status: int | None = Field(default=None, ge=100, le=599)
    error_name: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=500)
    client_version: str = Field(default="web-chat-v1", max_length=40)
    details: dict[str, Any] = Field(default_factory=dict)


class ClientDiagnosticBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[ClientDiagnosticEvent] = Field(min_length=1, max_length=30)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str | None, *, field: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=field + " must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail=field + " must include a timezone")
    return parsed.astimezone(timezone.utc)


def _safe_text(value: Any, maximum: int = 240) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\x00", "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", text)
    return text[:maximum]


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(-2_147_483_648, min(2_147_483_647, value))
    if isinstance(value, float):
        return value if value == value and abs(value) < 1e12 else None
    if isinstance(value, str):
        return _safe_text(value, 120)
    return None


def _safe_details(value: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    result: dict[str, str | int | float | bool | None] = {}
    for key, raw in list(value.items())[:24]:
        name = str(key or "").strip().lower()
        if name not in _ALLOWED_DETAIL_KEYS:
            continue
        scalar = _safe_scalar(raw)
        if scalar is not None:
            result[name] = scalar
    return result


def _normalize_event(event: ClientDiagnosticEvent, user_id: str) -> dict[str, Any]:
    occurred_at = _parse_datetime(event.occurred_at, field="occurred_at") if event.occurred_at else None
    request_id = str(event.request_id or "").strip()
    model_id = str(event.model_id or "").strip()
    session_id = str(event.session_id or "").strip()
    return {
        "recorded_at": _iso(),
        "client_occurred_at": _iso(occurred_at),
        "user_id": user_id,
        "client_event_id": event.client_event_id,
        "event": event.event,
        "level": event.level,
        "session_id": session_id if _SESSION_ID_RE.fullmatch(session_id) else "",
        "request_id": request_id if _REQUEST_ID_RE.fullmatch(request_id) else "",
        "model_id": model_id if _MODEL_ID_RE.fullmatch(model_id) else "",
        "phase": _safe_text(event.phase, 80),
        "duration_ms": event.duration_ms,
        "http_status": event.http_status,
        "error_name": _safe_text(event.error_name, 100),
        "error_message": _safe_text(event.error_message, 240),
        "client_version": _safe_text(event.client_version, 40),
        "details": _safe_details(event.details),
    }


class WebChatClientDiagnosticStore:
    """Single-process, bounded JSONL journal. Prompt/response bodies are never accepted."""

    def __init__(self, data_dir: Path | str) -> None:
        self.directory = Path(data_dir) / _STORE_NAME
        ensure_private_directory(self.directory)
        self.path = self.directory / _STORE_FILE
        self.lock = asyncio.Lock()
        self.rate_lock = threading.RLock()
        self.items: deque[dict[str, Any]] = deque()
        self.event_ids: set[str] = set()
        self.rate_buckets: dict[str, deque[float]] = {}
        self._load()

    def _remember(self, row: dict[str, Any]) -> None:
        event_id = str(row.get("client_event_id") or "")
        if event_id and event_id in self.event_ids:
            return
        if len(self.items) >= _MAX_ITEMS:
            old = self.items.popleft()
            self.event_ids.discard(str(old.get("client_event_id") or ""))
        self.items.append(row)
        if event_id:
            self.event_ids.add(event_id)

    def _load(self) -> None:
        raw = read_private_bytes(self.path)
        if not raw:
            return
        for line in raw.splitlines()[-_MAX_ITEMS:]:
            try:
                row = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(row, dict) and isinstance(row.get("recorded_at"), str):
                self._remember(row)
        if len(raw) > _MAX_FILE_BYTES:
            self._compact_sync()

    def allow_batch(self, user_id: str, count: int) -> bool:
        now = time.monotonic()
        with self.rate_lock:
            bucket = self.rate_buckets.setdefault(user_id, deque())
            while bucket and now - bucket[0] >= 60:
                bucket.popleft()
            if len(bucket) + count > 180:
                return False
            bucket.extend([now] * count)
            if len(self.rate_buckets) > 10_000:
                cutoff = now - 60
                for key, values in list(self.rate_buckets.items()):
                    while values and values[0] < cutoff:
                        values.popleft()
                    if not values:
                        self.rate_buckets.pop(key, None)
            return True

    def _append_sync(self, row: dict[str, Any]) -> None:
        payload = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        fd = open_private_regular_file(
            self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode=0o600,
        )
        with os.fdopen(fd, "ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            size = os.fstat(handle.fileno()).st_size
        self._remember(row)
        if size > _MAX_FILE_BYTES:
            self._compact_sync()

    def _compact_sync(self) -> None:
        rows = list(self.items)[-_COMPACT_ITEMS:]
        payload = b"".join(
            (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            for row in rows
        )
        write_private_bytes_atomic(self.path, payload)
        self.items = deque(rows)
        self.event_ids = {str(row.get("client_event_id") or "") for row in rows if row.get("client_event_id")}

    async def append_many(self, rows: list[dict[str, Any]]) -> int:
        added = 0
        async with self.lock:
            for row in rows:
                event_id = str(row.get("client_event_id") or "")
                if event_id and event_id in self.event_ids:
                    continue
                self._append_sync(row)
                added += 1
        return added

    async def query(
        self, *, limit: int, since: datetime | None = None,
        until: datetime | None = None, user_id: str = "",
    ) -> list[dict[str, Any]]:
        async with self.lock:
            snapshot = list(self.items)
        rows: list[dict[str, Any]] = []
        for row in reversed(snapshot):
            if user_id and str(row.get("user_id") or "") != user_id:
                continue
            recorded = _parse_datetime(str(row.get("recorded_at") or ""), field="recorded_at")
            if since and (recorded is None or recorded < since):
                continue
            if until and (recorded is None or recorded > until):
                continue
            rows.append(dict(row))
            if len(rows) >= limit:
                break
        return rows

    async def retained_count(self) -> int:
        async with self.lock:
            return len(self.items)


def install_web_chat_client_diagnostics_v1(app: FastAPI) -> FastAPI:
    if getattr(app.state, "web_chat_client_diagnostics_v1_installed", False):
        return app
    store = WebChatClientDiagnosticStore(app.state.settings.data_dir)
    app.state.web_chat_client_diagnostics = store

    def admin_actor(request: Request) -> str:
        sessions = getattr(request.app.state, "admin_sessions", None)
        token = request.cookies.get(SESSION_COOKIE)
        if sessions is None or not sessions.authenticate(token):
            raise HTTPException(status_code=401, detail="Administrator login required", headers=_PRIVATE_HEADERS)
        identity = getattr(sessions, "identity", None)
        username = identity(token) if callable(identity) else request.app.state.settings.admin_username
        control = getattr(request.app.state, "control_plane", None)
        if control is not None and not control.permitted(str(username), request.url.path, request.method):
            raise HTTPException(status_code=403, detail="Administrator permission denied", headers=_PRIVATE_HEADERS)
        return str(username)

    @app.post("/api/user/chat/client-logs")
    async def ingest_web_chat_client_logs(request: Request) -> Response:
        if not same_origin_admin_request(request):
            raise HTTPException(status_code=403, detail="Same-origin request required", headers=_PRIVATE_HEADERS)
        accounts = getattr(request.app.state, "user_accounts", None)
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE)) if accounts is not None else None
        if not user:
            raise HTTPException(status_code=401, detail="user login required", headers=_PRIVATE_HEADERS)
        try:
            declared = int(request.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > 64 * 1024:
            raise HTTPException(status_code=413, detail="Diagnostic batch is too large", headers=_PRIVATE_HEADERS)
        raw = await request.body()
        if len(raw) > 64 * 1024:
            raise HTTPException(status_code=413, detail="Diagnostic batch is too large", headers=_PRIVATE_HEADERS)
        try:
            batch = ClientDiagnosticBatch.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="Invalid diagnostic event batch", headers=_PRIVATE_HEADERS) from exc
        user_id = str(user.get("user_id") or "")
        if not _USER_ID_RE.fullmatch(user_id):
            raise HTTPException(status_code=401, detail="user identity unavailable", headers=_PRIVATE_HEADERS)
        if not store.allow_batch(user_id, len(batch.events)):
            raise HTTPException(status_code=429, detail="Client diagnostic rate limit exceeded", headers=_PRIVATE_HEADERS)
        rows = [_normalize_event(event, user_id) for event in batch.events]
        accepted = await store.append_many(rows)
        return JSONResponse({"accepted": accepted}, headers=_PRIVATE_HEADERS)

    @app.get("/api/admin/diagnostics/web-chat-client-logs")
    async def list_web_chat_client_logs(
        request: Request,
        limit: int = Query(default=200, ge=1, le=2000),
        since: str | None = None,
        until: str | None = None,
        user_id: str = "",
    ) -> Response:
        admin_actor(request)
        start = _parse_datetime(since, field="since")
        end = _parse_datetime(until, field="until")
        if bool(start) != bool(end):
            raise HTTPException(status_code=422, detail="since and until must be supplied together", headers=_PRIVATE_HEADERS)
        if start and end and (end <= start or end - start > timedelta(days=31)):
            raise HTTPException(status_code=422, detail="time range must be positive and no longer than 31 days", headers=_PRIVATE_HEADERS)
        if user_id and not _USER_ID_RE.fullmatch(user_id):
            raise HTTPException(status_code=422, detail="invalid user_id", headers=_PRIVATE_HEADERS)
        rows = await store.query(limit=limit, since=start, until=end, user_id=user_id)
        return JSONResponse({
            "data": rows,
            "count": len(rows),
            "retained": await store.retained_count(),
        }, headers=_PRIVATE_HEADERS)

    @app.get("/api/admin/diagnostics/web-chat-client-logs/export")
    async def export_web_chat_client_logs(
        request: Request,
        limit: int | None = Query(default=None, ge=1, le=20_000),
        since: str | None = None,
        until: str | None = None,
        user_id: str = "",
    ) -> Response:
        admin_actor(request)
        start = _parse_datetime(since, field="since")
        end = _parse_datetime(until, field="until")
        if bool(start) != bool(end):
            raise HTTPException(status_code=422, detail="since and until must be supplied together", headers=_PRIVATE_HEADERS)
        if start and end:
            if end <= start or end - start > timedelta(days=31):
                raise HTTPException(status_code=422, detail="time range must be positive and no longer than 31 days", headers=_PRIVATE_HEADERS)
            if limit is not None:
                raise HTTPException(status_code=422, detail="choose either a count or a time range", headers=_PRIVATE_HEADERS)
            export_limit = _MAX_ITEMS
            filter_description = {"since": _iso(start), "until": _iso(end)}
        else:
            export_limit = int(limit or 500)
            filter_description = {"limit": export_limit}
        if user_id and not _USER_ID_RE.fullmatch(user_id):
            raise HTTPException(status_code=422, detail="invalid user_id", headers=_PRIVATE_HEADERS)
        rows = await store.query(limit=export_limit, since=start, until=end, user_id=user_id)
        manifest = {
            "format": "chat2api.web-chat-client-diagnostics",
            "version": 1,
            "exported_at": _iso(),
            "count": len(rows),
            "user_id_filter": user_id or None,
            "filter": filter_description,
            "privacy": "No prompts, assistant response bodies, cookies, authorization headers, API keys, or file contents are collected.",
        }
        events = b"".join(
            (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            for row in rows
        )
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("client-events.jsonl", events)
            archive.writestr(
                "README.txt",
                "Chat2API Web Chat client diagnostics\n"
                "The event stream contains redacted client lifecycle and error metadata only.\n"
                "It intentionally excludes conversation prompts and assistant response content.\n",
            )
        filename = "chat2api-web-chat-diagnostics-" + _utc_now().strftime("%Y%m%d-%H%M%S") + ".zip"
        return Response(
            bundle.getvalue(),
            media_type="application/zip",
            headers={
                **_PRIVATE_HEADERS,
                "Content-Disposition": 'attachment; filename="' + filename + '"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    asset_path = Path(__file__).with_name("admin_web_chat_client_logs_v1.js")
    if not asset_path.is_file():
        raise RuntimeError("Web Chat client diagnostics admin asset is missing")

    @app.get("/assets/chat2api-admin-web-chat-client-logs-v1.js", include_in_schema=False)
    async def admin_web_chat_client_logs_asset() -> Response:
        return Response(
            asset_path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    app.state.web_chat_client_diagnostics_v1_installed = True
    return app


__all__ = ["ClientDiagnosticEvent", "ClientDiagnosticBatch", "WebChatClientDiagnosticStore", "install_web_chat_client_diagnostics_v1"]
