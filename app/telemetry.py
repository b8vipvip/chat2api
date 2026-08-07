from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelemetryStore:
    def __init__(self, data_dir: Path, max_items: int = 500) -> None:
        self.path = data_dir / "request_history.jsonl"
        self.items: deque[dict[str, Any]] = deque(maxlen=max_items)
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-self.items.maxlen :]
            for line in lines:
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    self.items.append(item)
        except (OSError, ValueError, TypeError):
            self.items.clear()

    async def append(self, item: dict[str, Any]) -> None:
        record = dict(item)
        record.setdefault("recorded_at", utc_now())
        async with self.lock:
            self.items.append(record)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        size = max(1, min(int(limit), self.items.maxlen or 500))
        return list(self.items)[-size:][::-1]

    def summary(self) -> dict[str, Any]:
        rows = list(self.items)
        completed = [row for row in rows if row.get("status") == "completed"]
        errors = [row for row in rows if row.get("status") == "error"]
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
            "success_rate": round((len(completed) / len(rows) * 100), 2) if rows else 100.0,
            "estimated_prompt_tokens": prompt_tokens,
            "estimated_completion_tokens": completion_tokens,
            "estimated_total_tokens": prompt_tokens + completion_tokens,
            "avg_total_ms": round(sum(total_ms_values) / len(total_ms_values), 1) if total_ms_values else None,
            "avg_first_token_ms": round(sum(first_token_values) / len(first_token_values), 1) if first_token_values else None,
            "token_estimator": "chat2api-heuristic-v1",
            "token_usage_is_estimated": True,
        }
