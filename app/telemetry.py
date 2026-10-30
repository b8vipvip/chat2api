from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from collections import deque
from pathlib import Path
from typing import Any

from .diagnostics import current_trace_id
from .jsonl_tail import iter_recent_jsonl
from .runtime_code_identity import runtime_code_sha
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



def parse_request_since(raw: str | None) -> datetime | None:
    """One strict timestamp contract for history list and ZIP export.

    A missing value means no time filter. Zoned timestamps honor their own
    offsets; naive values mean Beijing time, matching the admin UI.
    """
    source = str(raw or "").strip()
    if not source:
        return None
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}"
        r"(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?",
        source, flags=re.IGNORECASE,
    ):
        raise ValueError("Invalid since timestamp format")
    try:
        parsed = datetime.fromisoformat(source.replace("Z", "+00:00").replace("z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        offset = parsed.utcoffset()
        if offset is None or abs(offset.total_seconds()) > 14 * 3600:
            raise ValueError("Invalid timezone offset")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Invalid since timestamp or timezone") from exc


def current_deployment_start(data_dir: Path, expected_sha: str) -> datetime | None:
    """Limit current-version results to the successful deployment instance.

    The same SHA may be deployed again. The completion time of the currently
    active successful deployment is an additional inclusive lower bound.
    """
    try:
        state = json.loads((data_dir / "admin-update-status.json").read_text(encoding="utf-8"))
        if state.get("status") not in ("succeeded", "success") or state.get("deployed_commit") != expected_sha:
            return None
        completed = datetime.fromisoformat(str(state["completed_at"]).replace("Z", "+00:00"))
        return completed.astimezone(timezone.utc) if completed.tzinfo else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def request_record_created_before(row: dict[str, Any], ceiling: datetime) -> bool:
    try:
        recorded = datetime.fromisoformat(str(row.get("recorded_at") or "").replace("Z", "+00:00"))
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone(timedelta(hours=8)))
        return recorded.astimezone(timezone.utc) <= ceiling
    except (ValueError, OverflowError, TypeError):
        return False


def request_record_created_after(row: dict[str, Any], cutoff: datetime) -> bool:
    try:
        recorded = datetime.fromisoformat(str(row.get("recorded_at") or "").replace("Z", "+00:00"))
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone(timedelta(hours=8)))
        return recorded.astimezone(timezone.utc) >= cutoff
    except (ValueError, OverflowError, TypeError):
        return False


class TelemetryStore:
    def __init__(self, data_dir: Path, max_items: int = 500) -> None:
        self.path = data_dir / "request_history.jsonl"
        self.items: deque[dict[str, Any]] = deque(maxlen=max_items)
        self.lock = asyncio.Lock()
        self.usage_accounting = None  # Installed by create_app; one owner of quota deduction.

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
            for line_number, item in enumerate(iter_recent_jsonl(self.path)):
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
            # Request-v6's first terminal is authoritative. An asynchronous
            # observer or late write from an earlier running snapshot may not
            # reopen the completed/error request or replace its final body.
            if existing and str(existing.get("status") or "") in {
                "completed", "error", "cancelled", "stalled",
            } and record.get("status") is not None and (
                str(record["status"]) != str(existing["status"])
            ):
                return dict(existing)
            # Stamp at creation, not when a later status update is written.
            # Historical unversioned requests must remain unversioned.
            if existing:
                record.pop("request_server_version", None)
                record.pop("request_server_commit", None)
            else:
                from .runtime_contract import SERVER_RUNTIME_VERSION
                record["request_server_version"] = SERVER_RUNTIME_VERSION
                # Never trust an upstream-supplied revision. Stamp the actual
                # running code at request creation and keep it immutable.
                commit = runtime_code_sha()
                if commit:
                    record["request_server_commit"] = commit
                else:
                    record.pop("request_server_commit", None)
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
        if self.usage_accounting is not None:
            await self.usage_accounting(record)
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
        since: datetime | None = None,
        current_version_only: bool = False,
        server_version: str | None = None,
        server_commit: str | None = None,
    ) -> dict[str, Any]:
        # Query consumers are allowed to decorate/redact list rows. Return copies
        # so list-only redaction (final_prompt/response_text) can never mutate the
        # authoritative in-memory detail record used by /api/admin/requests/{id}.
        rows = [dict(row) for row in reversed(self.items)]
        query_ends_at = datetime.now(timezone.utc)
        deployment_start = current_deployment_start(self.path.parent, server_commit or '') if current_version_only else None
        status_value = (status or "").strip().lower()
        model_value = (model or "").strip().lower()
        key_value = (key_id or "").strip()
        search = (q or "").strip().lower()

        def matches(row: dict[str, Any]) -> bool:
            if current_version_only and (
                not server_commit
                or str(row.get("request_server_commit") or "") != server_commit
            ):
                return False
            if deployment_start is not None and not request_record_created_after(row, deployment_start):
                return False
            if since is not None and not request_record_created_after(row, since):
                return False
            if not request_record_created_before(row, query_ends_at):
                return False
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
        safe_limit = max(1, min(int(limit), self.items.maxlen or 500))
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
