from __future__ import annotations

import asyncio
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
        self.lock = asyncio.Lock()

    def _remember(self, row: dict[str, Any]) -> None:
        run_id = str(row.get("run_id") or "")
        if run_id:
            retained = [item for item in self.items if str(item.get("run_id") or "") != run_id]
            self.items = deque(retained, maxlen=self.items.maxlen)
        self.items.append(row)

    def _write_snapshot(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

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
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
                run_id = str(row.get("run_id") or "")
                record_key = ("run", run_id) if run_id else ("legacy-line", str(line_number))
                if record_key in latest:
                    continue
                latest[record_key] = normalize_times(row)
                order.append(record_key)
                if len(order) >= (self.items.maxlen or 300):
                    break
            for record_key in reversed(order):
                self._remember(latest[record_key])
        except (OSError, ValueError, TypeError):
            self.items.clear()

    async def append(self, item: dict[str, Any]) -> dict[str, Any]:
        return await self.upsert(item)

    async def upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        row = normalize_times(item)
        run_id = str(row.get("run_id") or "")
        if not run_id:
            raise ValueError("test run requires run_id")
        async with self.lock:
            existing = self.get(run_id) or {}
            merged = {**existing, **row}
            merged.setdefault("recorded_at", utc_now())
            merged["updated_at"] = utc_now()
            merged = normalize_times(merged)
            self._remember(merged)
            self._write_snapshot(merged)
        return dict(merged)

    async def update(self, run_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            existing = self.get(run_id)
            if not existing:
                raise KeyError(f"Unknown test run: {run_id}")
            merged = normalize_times({**existing, **changes, "run_id": run_id})
            merged.setdefault("recorded_at", utc_now())
            merged["updated_at"] = utc_now()
            self._remember(merged)
            self._write_snapshot(merged)
            return dict(merged)

    async def recover_interrupted(self) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        for row in list(self.items):
            if str(row.get("status") or "") not in {"pending", "running"}:
                continue
            message = "Server restarted before the playground run reached a terminal state"
            recovered.append(
                await self.update(
                    str(row.get("run_id") or ""),
                    {
                        "status": "stalled",
                        "finished_at": utc_now(),
                        "error": message,
                        "summary": message,
                    },
                )
            )
        return recovered

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self.items)[-max(1, min(limit, self.items.maxlen or 300)) :][::-1]

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = next((item for item in reversed(self.items) if item.get("run_id") == run_id), None)
        return dict(row) if row else None
