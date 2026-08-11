from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from .timezone_utils import beijing_now_iso, to_beijing_iso


def utc_now() -> str:
    """Backward-compatible helper name; canonical timestamps are Asia/Shanghai."""
    return beijing_now_iso()


def normalize_times(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    for key in ("recorded_at", "started_at", "finished_at", "created_at", "updated_at"):
        if row.get(key):
            row[key] = to_beijing_iso(row[key]) or row[key]
    return row


class TestRunStore:
    def __init__(self, data_dir: Path, max_items: int = 300) -> None:
        self.path = data_dir / "test_runs.jsonl"
        self.items: deque[dict[str, Any]] = deque(maxlen=max_items)

    async def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines()[-self.items.maxlen :]:
                if line.strip():
                    row = json.loads(line)
                    if isinstance(row, dict):
                        self.items.append(normalize_times(row))
        except (OSError, ValueError, TypeError):
            self.items.clear()

    async def append(self, item: dict[str, Any]) -> dict[str, Any]:
        row = normalize_times(item)
        row.setdefault("recorded_at", utc_now())
        self.items.append(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return row

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self.items)[-max(1, min(limit, self.items.maxlen or 300)) :][::-1]

    def get(self, run_id: str) -> dict[str, Any] | None:
        return next((row for row in reversed(self.items) if row.get("run_id") == run_id), None)
