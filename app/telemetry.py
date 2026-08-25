from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any

from .diagnostics import current_trace_id
from .timezone_utils import beijing_now_iso, to_beijing_iso


def utc_now() -> str:
    """Backward-compatible helper name; canonical timestamps are Asia/Shanghai."""
    return beijing_now_iso()


def _normalize_record_time(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for key in ("recorded_at", "updated_at", "finished_at"):
        if normalized.get(key):
            normalized[key] = to_beijing_iso(normalized[key]) or normalized[key]
    return normalized


class TelemetryStore:
    def __init__(self, data_dir: Path, max_items: int = 500) -> None:
        self.path = data_dir / "request_history.jsonl"
        self.items: deque[dict[str, Any]] = deque(maxlen=max_items)
        self.lock = asyncio.Lock()

    def _remember(self, record: dict[str, Any]) -> None:
        request_id = str(record.get("request_id") or "")
        if request_id:
            retained = [item for item in self.items if str(item.get("request_id") or "") != request_id]
            self.items = deque(retained, maxlen=self.items.maxlen)
        self.items.append(record)

    async def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return
        try:
            latest: dict[tuple[str, str], dict[str, Any]] = {}
            order: list[tuple[str, str]] = []
            lines = self.path.read_text(encoding="utf-8").splitlines()
            for line_number in range(len(lines) - 1, -1, -1):
                line = lines[line_number]
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    continue
                request_id = str(item.get("request_id") or "")
                record_key = ("request", request_id) if request_id else ("legacy-line", str(line_number))
                if record_key in latest:
                    continue
                latest[record_key] = _normalize_record_time(item)
                order.append(record_key)
                if len(order) >= (self.items.maxlen or 500):
                    break
            for record_key in reversed(order):
                self._remember(latest[record_key])
        except (OSError, ValueError, TypeError):
            self.items.clear()

    async def append(self, item: dict[str, Any]) -> None:
        await self.upsert(item)

    async def upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        record = dict(item)
        trace_id = current_trace_id.get()
        if trace_id:
            record.setdefault("trace_id", trace_id)
        async with self.lock:
            request_id = str(record.get("request_id") or "")
            existing = self.get(request_id) if request_id else None
            record = {**(existing or {}), **record}
            record.setdefault("recorded_at", utc_now())
            record["updated_at"] = utc_now()
            if str(record.get("status") or "") in {"completed", "error", "cancelled", "stalled"}:
                record.setdefault("finished_at", utc_now())
            record = _normalize_record_time(record)
            self._remember(record)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return dict(record)

    async def update(self, request_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not self.get(request_id):
            raise KeyError(f"Unknown request: {request_id}")
        return await self.upsert({"request_id": request_id, **changes})

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        size = max(1, min(int(limit), self.items.maxlen or 500))
        return list(self.items)[-size:][::-1]

    def get(self, request_id: str) -> dict[str, Any] | None:
        for row in reversed(self.items):
            if str(row.get("request_id") or "") == request_id:
                return dict(row)
        return None

    def query(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        model: str | None = None,
        key_id: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        rows = list(self.items)[::-1]
        status_value = (status or "").strip().lower()
        model_value = (model or "").strip().lower()
        key_value = (key_id or "").strip()
        search = (q or "").strip().lower()

        def matches(row: dict[str, Any]) -> bool:
            if status_value and str(row.get("status") or "").lower() != status_value:
                return False
            requested_model = str(row.get("requested_model") or "")
            actual_model = str((row.get("diagnostics") or {}).get("actual_model") or "")
            if model_value and model_value not in requested_model.lower() and model_value not in actual_model.lower():
                return False
            if key_value and str(row.get("api_key_id") or "") != key_value:
                return False
            if search:
                haystack = " ".join(
                    str(value or "")
                    for value in (
                        row.get("request_id"),
                        row.get("trace_id"),
                        row.get("client_id"),
                        row.get("requested_model"),
                        actual_model,
                        row.get("api_key_id"),
                        row.get("api_key_name"),
                        row.get("error"),
                    )
                ).lower()
                if search not in haystack:
                    return False
            return True

        filtered = [row for row in rows if matches(row)]
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        return {
            "data": filtered[safe_offset : safe_offset + safe_limit],
            "total": len(filtered),
            "limit": safe_limit,
            "offset": safe_offset,
        }

    def key_stats(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in self.items:
            key_id = str(row.get("api_key_id") or "")
            if not key_id:
                continue
            stats = result.setdefault(
                key_id,
                {
                    "request_count": 0,
                    "completed_requests": 0,
                    "error_requests": 0,
                    "estimated_tokens": 0,
                    "last_request_at": None,
                },
            )
            stats["request_count"] += 1
            if row.get("status") == "completed":
                stats["completed_requests"] += 1
            elif row.get("status") in {"error", "cancelled", "stalled"}:
                stats["error_requests"] += 1
            stats["estimated_tokens"] += int((row.get("usage") or {}).get("total_tokens") or 0)
            stats["last_request_at"] = row.get("recorded_at") or stats["last_request_at"]
        return result

    def summary(self) -> dict[str, Any]:
        rows = list(self.items)
        completed = [row for row in rows if row.get("status") == "completed"]
        errors = [row for row in rows if row.get("status") in {"error", "cancelled", "stalled"}]
        running = [row for row in rows if row.get("status") == "running"]
        terminal_count = len(completed) + len(errors)
        prompt_tokens = sum(int((row.get("usage") or {}).get("prompt_tokens") or 0) for row in rows)
        completion_tokens = sum(int((row.get("usage") or {}).get("completion_tokens") or 0) for row in rows)
        total_ms_values = [
            float((row.get("timings") or {}).get("total_ms"))
            for row in completed
            if (row.get("timings") or {}).get("total_ms") is not None
        ]
        first_token_values = [
            float((row.get("timings") or {}).get("first_token_ms"))
            for row in completed
            if (row.get("timings") or {}).get("first_token_ms") is not None
        ]
        return {
            "retained_requests": len(rows),
            "completed_requests": len(completed),
            "error_requests": len(errors),
            "running_requests": len(running),
            "success_rate": round((len(completed) / terminal_count * 100), 2) if terminal_count else 100.0,
            "estimated_prompt_tokens": prompt_tokens,
            "estimated_completion_tokens": completion_tokens,
            "estimated_total_tokens": prompt_tokens + completion_tokens,
            "avg_total_ms": round(sum(total_ms_values) / len(total_ms_values), 1) if total_ms_values else None,
            "avg_first_token_ms": round(sum(first_token_values) / len(first_token_values), 1) if first_token_values else None,
            "token_estimator": "chat2api-heuristic-v1",
            "token_usage_is_estimated": True,
            "timestamp_timezone": "Asia/Shanghai",
        }
