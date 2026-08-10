from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode


current_trace_id: ContextVar[str | None] = ContextVar("chat2api_trace_id", default=None)

_SENSITIVE = {
    "authorization",
    "x-api-key",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "pairing_code",
    "x-pairing-code",
    "data_base64",
    "b64_json",
}


def utc_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _decode_headers(raw: list[tuple[bytes, bytes]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in raw:
        name = key.decode("latin-1", errors="replace").lower()
        if name in {"content-type", "user-agent", "origin", "referer", "accept", "x-chat2api-client"}:
            result[name] = value.decode("latin-1", errors="replace")[:1000]
    return result


def _safe_query(raw: bytes) -> str:
    if not raw:
        return ""
    pairs = []
    for key, value in parse_qsl(raw.decode("utf-8", errors="replace"), keep_blank_values=True):
        pairs.append((key, "[REDACTED]" if key.lower() in _SENSITIVE else value[:300]))
    return urlencode(pairs)


def _content_kinds(content: Any) -> list[str]:
    if isinstance(content, str):
        return ["text"]
    if not isinstance(content, list):
        return [type(content).__name__]
    kinds: list[str] = []
    for part in content:
        if isinstance(part, dict):
            kinds.append(str(part.get("type") or "object"))
        else:
            kinds.append(type(part).__name__)
    return kinds[:20]


def summarize_request_body(raw: bytes, content_type: str) -> dict[str, Any]:
    result: dict[str, Any] = {"body_bytes": len(raw)}
    if not raw or "json" not in content_type.lower():
        return result
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        result["json_parse"] = "failed"
        return result
    if not isinstance(payload, dict):
        result["json_type"] = type(payload).__name__
        return result

    for key in ("model", "stream", "prompt_mode", "response_format", "n", "timeout", "language"):
        if key in payload and key not in _SENSITIVE:
            result[key] = payload.get(key)

    messages = payload.get("messages")
    if isinstance(messages, list):
        result["message_count"] = len(messages)
        result["messages"] = [
            {
                "role": str(item.get("role") or "") if isinstance(item, dict) else "",
                "content_kinds": _content_kinds(item.get("content")) if isinstance(item, dict) else [],
                "content_chars": len(item.get("content")) if isinstance(item, dict) and isinstance(item.get("content"), str) else None,
            }
            for item in messages[:30]
        ]

    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        result["attachment_count"] = len(attachments)
        result["attachment_types"] = [
            str(item.get("mime_type") or item.get("type") or "") if isinstance(item, dict) else type(item).__name__
            for item in attachments[:20]
        ]

    for field in ("prompt", "input", "instruction"):
        value = payload.get(field)
        if isinstance(value, str):
            result[f"{field}_chars"] = len(value)

    if isinstance(payload.get("audio_file_id"), str):
        result["audio_file_id"] = payload["audio_file_id"][:160]

    if isinstance(payload.get("filename"), str):
        result["filename"] = payload["filename"][:300]
    if isinstance(payload.get("mime_type"), str):
        result["mime_type"] = payload["mime_type"][:160]
    if isinstance(payload.get("purpose"), str):
        result["purpose"] = payload["purpose"][:160]
    if isinstance(payload.get("data_base64"), str):
        result["data_base64_chars"] = len(payload["data_base64"])

    return result


def safe_error_response(raw: bytes, content_type: str) -> Any:
    if not raw:
        return None
    if "json" in content_type.lower():
        try:
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict):
                clean: dict[str, Any] = {}
                for key, value in payload.items():
                    if key.lower() in _SENSITIVE:
                        clean[key] = "[REDACTED]"
                    elif key in {"detail", "error", "message", "type"}:
                        clean[key] = value
                return clean or {"json_keys": sorted(payload)[:30]}
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")[:2000]


class DiagnosticStore:
    def __init__(self, data_dir: Path, max_items: int = 2000) -> None:
        self.path = data_dir / "server_events.jsonl"
        self.items: deque[dict[str, Any]] = deque(maxlen=max_items)
        self.lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                for line in self.path.read_text(encoding="utf-8").splitlines()[-max_items:]:
                    if line.strip():
                        item = json.loads(line)
                        if isinstance(item, dict):
                            self.items.append(item)
            except Exception:
                self.items.clear()

    async def append(self, item: dict[str, Any]) -> None:
        record = dict(item)
        record.setdefault("recorded_at", utc_stamp())
        async with self.lock:
            self.items.append(record)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        size = max(1, min(int(limit), self.items.maxlen or 2000))
        return list(self.items)[-size:][::-1]

    def by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self.items if str(item.get("trace_id") or "") == trace_id]


def configure_file_logging(data_dir: Path) -> Path:
    path = data_dir / "chat2api.log"
    logger = logging.getLogger("chat2api")
    logger.setLevel(logging.INFO)
    resolved = str(path.resolve())
    already = any(getattr(handler, "baseFilename", None) == resolved for handler in logger.handlers)
    if not already:
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return path


class DiagnosticMiddleware:
    def __init__(self, app, store: DiagnosticStore) -> None:
        self.app = app
        self.store = store
        self.logger = logging.getLogger("chat2api")

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        trace_id = "trace_" + uuid.uuid4().hex
        trace_token = current_trace_id.set(trace_id)
        started = time.perf_counter()
        request_body = bytearray()
        request_bytes = 0
        response_body = bytearray()
        response_status = 500
        response_content_type = ""
        response_started = False
        exception_text: str | None = None
        headers = _decode_headers(scope.get("headers") or [])

        async def receive_wrapper():
            nonlocal request_bytes
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body") or b""
                request_bytes += len(chunk)
                if len(request_body) < 131072:
                    request_body.extend(chunk[: 131072 - len(request_body)])
            return message

        async def send_wrapper(message):
            nonlocal response_status, response_content_type, response_started
            if message.get("type") == "http.response.start":
                response_started = True
                response_status = int(message.get("status") or 500)
                raw_headers = list(message.get("headers") or [])
                for key, value in raw_headers:
                    if key.lower() == b"content-type":
                        response_content_type = value.decode("latin-1", errors="replace")
                        break
                raw_headers.append((b"x-chat2api-trace-id", trace_id.encode("ascii")))
                message = {**message, "headers": raw_headers}
            elif message.get("type") == "http.response.body" and response_status >= 400:
                chunk = message.get("body") or b""
                if len(response_body) < 65536:
                    response_body.extend(chunk[: 65536 - len(response_body)])
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as error:
            exception_text = f"{type(error).__name__}: {error}"
            self.logger.exception("Unhandled HTTP error trace_id=%s path=%s", trace_id, scope.get("path"))
            raise
        finally:
            try:
                content_type = headers.get("content-type", "")
                body_summary = summarize_request_body(bytes(request_body), content_type)
                body_summary["body_bytes"] = request_bytes
                event: dict[str, Any] = {
                    "trace_id": trace_id,
                    "method": str(scope.get("method") or ""),
                    "path": str(scope.get("path") or ""),
                    "query": _safe_query(scope.get("query_string") or b""),
                    "status_code": response_status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client": scope.get("client")[0] if scope.get("client") else None,
                    "headers": headers,
                    "request": body_summary,
                    "response_started": response_started,
                    "exception": exception_text,
                }
                if response_status >= 400:
                    event["response_error"] = safe_error_response(bytes(response_body), response_content_type)
                await self.store.append(event)
            except Exception:
                self.logger.exception("Failed to persist HTTP diagnostics trace_id=%s", trace_id)
            finally:
                current_trace_id.reset(trace_token)
