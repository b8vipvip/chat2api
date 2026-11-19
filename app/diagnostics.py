from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import traceback
import uuid
from collections import deque
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from .jsonl_tail import iter_recent_jsonl


current_trace_id: ContextVar[str | None] = ContextVar("chat2api_trace_id", default=None)

# Compare normalized names so snake_case, kebab-case and camelCase
# credentials are treated identically without redacting safe identifiers such
# as tokenID, tokenCount or apiKeyID.
_SENSITIVE = {
    "authorization", "proxyauthorization", "xapikey", "apikey",
    "token", "accesstoken", "refreshtoken", "idtoken", "sessiontoken",
    "csrftoken", "xsrftoken", "password", "passwd", "passphrase",
    "secret", "clientsecret", "clientassertion", "clientcredential",
    "xpairingcode", "xaccesstoken", "xrefreshtoken", "xcsrftoken",
    "xxsrftoken", "sessionid", "secretkey", "apisecret",
    "credential", "credentials", "cookie", "setcookie", "pairingcode",
    "invitecode", "invitationcode", "database64", "b64json",
    "signature", "signedurl", "privatekey", "jwt", "bearertoken",
    "authtoken", "oauthcode", "authorizationcode", "codeverifier",
    "verificationcode", "resetcode", "resettoken",
}
_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?i)["']?\b(authorization|proxy[-_]?authorization|x[-_]?api[-_]?key|api[-_]?key|access[-_]?token|refresh[-_]?token|id[-_]?token|session[-_]?token|csrf[-_]?token|client[-_]?secret|password|passwd|passphrase|secret|token|invite[-_]?code|invitation[-_]?code|pairing[-_]?code|x[-_]?pairing[-_]?code|signature|signed[-_]?url|jwt|cookie|set[-_]?cookie|session[-_]?cookie)\b["']?(\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;&]+)"""
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/-]{8,}={0,2}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")


def _is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key or "").casefold())
    return normalized in _SENSITIVE


def _redact_text(value: str) -> str:
    text = str(value or "")
    text = _BEARER_RE.sub(r"\1 [REDACTED]", text)
    text = _JWT_RE.sub("[REDACTED]", text)
    return _SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", text)


def _redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(key) else _redact_value(item, depth + 1)
            for key, item in list(value.items())[:200]
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, depth + 1) for item in list(value)[:200]]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _safe_path(value: Any) -> str:
    path = str(value or "")[:2000]
    # These endpoints carry bearer-capability tokens in path segments. Keep
    # resource IDs and route shape, but never persist the capability itself.
    path = re.sub(r"(?i)(/api/media/[^/]+/)[^/]+", r"\1[REDACTED]", path)
    path = re.sub(
        r"(?i)(/api/generated-files/pending/[^/]+/)[^/]+",
        r"\1[REDACTED]", path,
    )
    path = re.sub(
        r"(?i)(/api/generated-files/(?!pending/)[^/]+/)[^/]+",
        r"\1[REDACTED]", path,
    )
    return _redact_text(path)


def utc_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _safe_referer(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if not parsed.scheme or not host:
            return "[REDACTED]"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return f"{parsed.scheme.lower()}://{host}"[:1000]
    except ValueError:
        return "[REDACTED]"


def _decode_headers(raw: list[tuple[bytes, bytes]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in raw:
        name = key.decode("latin-1", errors="replace").lower()
        if name not in {"content-type", "user-agent", "origin", "referer", "accept", "x-chat2api-client"}:
            continue
        decoded = value.decode("latin-1", errors="replace")
        result[name] = _safe_referer(decoded) if name in {"referer", "origin"} else _redact_text(decoded[:1000])
    return result


def _safe_query(raw: bytes) -> str:
    if not raw:
        return ""
    pairs = []
    for key, value in parse_qsl(raw.decode("utf-8", errors="replace"), keep_blank_values=True):
        safe_value = "[REDACTED]" if _is_sensitive_key(key) else _redact_text(value[:300])
        pairs.append((key, safe_value))
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
        if key in payload and not _is_sensitive_key(key):
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
                    if _is_sensitive_key(key):
                        clean[key] = "[REDACTED]"
                    elif key in {"detail", "error", "message", "type"}:
                        clean[key] = _redact_value(value)
                return clean or {"json_keys": sorted(payload)[:30]}
            return _redact_value(payload)
        except Exception:
            pass
    return _redact_text(raw.decode("utf-8", errors="replace")[:2000])


class DiagnosticStore:
    def __init__(self, data_dir: Path, max_items: int = 2000) -> None:
        self.path = data_dir / "server_events.jsonl"
        self.items: deque[dict[str, Any]] = deque(maxlen=max_items)
        self.lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                for item in iter_recent_jsonl(self.path):
                    self.items.appendleft(item)
                    if len(self.items) >= max_items:
                        break
            except Exception:
                self.items.clear()

    async def append(self, item: dict[str, Any]) -> None:
        record = _redact_value(dict(item))
        record.setdefault("recorded_at", utc_stamp())
        async with self.lock:
            self.items.append(record)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        size = max(1, min(int(limit), self.items.maxlen or 2000))
        return [_redact_value(item) for item in list(self.items)[-size:][::-1]]

    def by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        return [
            _redact_value(item) for item in self.items
            if str(item.get("trace_id") or "") == trace_id
        ]


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
            exception_text = _redact_text(f"{type(error).__name__}: {error}")
            frames = " <- ".join(
                f"{frame.filename}:{frame.lineno}:{frame.name}"
                for frame in traceback.extract_tb(error.__traceback__)[-12:]
            )
            # Do not log exception messages: upstream errors may contain bearer
            # credentials. Keep type and stack locations for operator triage.
            self.logger.error(
                "Unhandled HTTP error trace_id=%s path=%s error_type=%s frames=%s",
                trace_id, _safe_path(scope.get("path")), type(error).__name__, frames,
            )
            raise
        finally:
            try:
                content_type = headers.get("content-type", "")
                body_summary = summarize_request_body(bytes(request_body), content_type)
                body_summary["body_bytes"] = request_bytes
                event: dict[str, Any] = {
                    "trace_id": trace_id,
                    "method": str(scope.get("method") or ""),
                    "path": _safe_path(scope.get("path")),
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
