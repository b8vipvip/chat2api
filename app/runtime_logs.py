from __future__ import annotations

import json
import logging
import re
import threading
import traceback
from collections import deque
from pathlib import Path
from typing import Any

from .timezone_utils import beijing_now_iso


MAX_MEMORY_ENTRIES = 5000
MAX_MESSAGE_CHARS = 12000
MAX_EXCEPTION_CHARS = 32000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROTATED_FILES = 4


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[A-Za-z0-9+/_.=-]+"),
    re.compile(r"(?i)((?:worker_?token|client_?token|access_?token|refresh_?token|password|secret|pairing_?code|api[_-]?key)\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)([?&](?:token|code|key|api_key)=)[^&\s]+"),
    re.compile(r"wbind_[A-Za-z0-9._-]+"),
)


def redact_text(value: Any) -> str:
    text = str(value or "")
    text = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", text)
    text = _SECRET_PATTERNS[1].sub(r"\1[REDACTED]", text)
    text = _SECRET_PATTERNS[2].sub(r"\1[REDACTED]", text)
    text = _SECRET_PATTERNS[3].sub("wbind_[REDACTED]", text)
    return text


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


class RuntimeLogStore:
    """Thread-safe, redacted runtime log ring with a small persistent NDJSON tail."""

    def __init__(self, data_dir: Path, *, max_entries: int = MAX_MEMORY_ENTRIES) -> None:
        self.directory = Path(data_dir) / "runtime_logs"
        self.path = self.directory / "chat2api-runtime.ndjson"
        self.max_entries = max(200, int(max_entries))
        self.entries: deque[dict[str, Any]] = deque(maxlen=self.max_entries)
        self.lock = threading.RLock()
        self._load_tail()

    def _load_tail(self) -> None:
        candidates = [
            self.directory / f"chat2api-runtime.ndjson.{index}"
            for index in range(MAX_ROTATED_FILES, 0, -1)
        ] + [self.path]
        with self.lock:
            for path in candidates:
                if not path.is_file():
                    continue
                try:
                    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                        if not line.strip():
                            continue
                        try:
                            item = json.loads(line)
                        except Exception:
                            continue
                        if isinstance(item, dict):
                            self.entries.append(item)
                except OSError:
                    continue

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            size = 0
        if size + incoming_bytes <= MAX_FILE_BYTES:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        oldest = self.directory / f"chat2api-runtime.ndjson.{MAX_ROTATED_FILES}"
        oldest.unlink(missing_ok=True)
        for index in range(MAX_ROTATED_FILES - 1, 0, -1):
            source = self.directory / f"chat2api-runtime.ndjson.{index}"
            if source.exists():
                source.replace(self.directory / f"chat2api-runtime.ndjson.{index + 1}")
        if self.path.exists():
            self.path.replace(self.directory / "chat2api-runtime.ndjson.1")

    def append(
        self,
        *,
        level: str,
        logger_name: str,
        message: str,
        exception: str = "",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_context: dict[str, Any] = {}
        for key, value in dict(context or {}).items():
            if re.search(r"(?i)token|secret|password|authorization|api[_-]?key|pairing", str(key)):
                safe_context[str(key)] = "[REDACTED]"
            elif isinstance(value, (str, int, float, bool)) or value is None:
                safe_context[str(key)] = redact_text(value) if isinstance(value, str) else value
            else:
                safe_context[str(key)] = redact_text(json.dumps(value, ensure_ascii=False, default=str))[:4000]

        entry = {
            "at": beijing_now_iso(),
            "level": str(level or "INFO").upper()[:20],
            "logger": str(logger_name or "chat2api")[:160],
            "message": _clip(redact_text(message), MAX_MESSAGE_CHARS),
            "exception": _clip(redact_text(exception), MAX_EXCEPTION_CHARS) if exception else "",
            "context": safe_context,
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        with self.lock:
            self.entries.append(entry)
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed(len(encoded))
                with self.path.open("ab") as handle:
                    handle.write(encoded)
            except OSError:
                # Logging must never make the application request path fail.
                pass
        return entry

    def query(
        self,
        *,
        limit: int = 500,
        level: str | None = None,
        logger_name: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(5000, int(limit)))
        level_value = str(level or "").strip().upper()
        logger_value = str(logger_name or "").strip().lower()
        query_value = str(q or "").strip().lower()
        with self.lock:
            rows = list(self.entries)
        filtered: list[dict[str, Any]] = []
        for row in reversed(rows):
            if level_value and str(row.get("level") or "").upper() != level_value:
                continue
            if logger_value and logger_value not in str(row.get("logger") or "").lower():
                continue
            if query_value:
                haystack = "\n".join(
                    [
                        str(row.get("message") or ""),
                        str(row.get("exception") or ""),
                        json.dumps(row.get("context") or {}, ensure_ascii=False, default=str),
                    ]
                ).lower()
                if query_value not in haystack:
                    continue
            filtered.append(row)
            if len(filtered) >= limit:
                break
        return {
            "data": filtered,
            "returned": len(filtered),
            "buffered": len(rows),
            "limit": limit,
        }

    def export_text(
        self,
        *,
        limit: int = 5000,
        level: str | None = None,
        logger_name: str | None = None,
        q: str | None = None,
    ) -> str:
        rows = self.query(limit=limit, level=level, logger_name=logger_name, q=q)["data"]
        lines: list[str] = []
        for row in reversed(rows):
            head = f"{row.get('at','')} [{row.get('level','INFO')}] {row.get('logger','chat2api')}: {row.get('message','')}"
            lines.append(head.rstrip())
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            if context:
                lines.append("context=" + json.dumps(context, ensure_ascii=False, default=str))
            if row.get("exception"):
                lines.append(str(row["exception"]).rstrip())
            lines.append("")
        return "\n".join(lines)


class RuntimeLogHandler(logging.Handler):
    def __init__(self, store: RuntimeLogStore) -> None:
        super().__init__(level=logging.DEBUG)
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if getattr(record, "_chat2api_runtime_captured", False):
                return
            record._chat2api_runtime_captured = True
            message = record.getMessage()
            exception = ""
            if record.exc_info:
                exception = "".join(traceback.format_exception(*record.exc_info))
            elif record.stack_info:
                exception = str(record.stack_info)
            context: dict[str, Any] = {}
            for key in ("request_id", "client_id", "worker_id", "control_id", "action", "path", "method", "status_code"):
                value = getattr(record, key, None)
                if value is not None:
                    context[key] = value
            self.store.append(
                level=record.levelname,
                logger_name=record.name,
                message=message,
                exception=exception,
                context=context,
            )
        except Exception:
            self.handleError(record)


def install_runtime_log_handler(store: RuntimeLogStore) -> RuntimeLogHandler:
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RuntimeLogHandler):
            return handler
    handler = RuntimeLogHandler(store)
    root.addHandler(handler)
    root.setLevel(min(root.level or logging.WARNING, logging.INFO))
    for name in ("chat2api", "uvicorn.error", "uvicorn.access", "fastapi", "starlette", "asyncio"):
        logger = logging.getLogger(name)
        logger.setLevel(min(logger.level or logging.WARNING, logging.INFO))
    return handler
