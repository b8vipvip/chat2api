from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.diagnostics import DiagnosticStore
from app.jsonl_tail import iter_recent_jsonl
from app.telemetry import TelemetryStore
from app.test_runs import TestRunStore


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_newest_first_reader_handles_chunk_boundaries_and_invalid_rows(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    rows = [{"id": i, "body": "汉字" * 1000} for i in range(12)]
    _write(path, rows)
    with path.open("ab") as handle:
        handle.write(b"not-json\n")
    result = list(iter_recent_jsonl(path, scan_limit_bytes=200_000))
    assert [row["id"] for row in result] == list(reversed(range(12)))
    assert path.stat().st_size > 12 * 1000


def test_scanned_tail_discards_partial_boundary_and_oversize_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(
        b'{"id":"old"}\n'
        + b'{"id":"giant","data":"'
        + b"x" * 200
        + b'"}\n'
        + b'{"id":"new"}\n'
    )
    results = list(iter_recent_jsonl(path, scan_limit_bytes=160, line_limit_bytes=100))
    assert results == [{"id": "new"}]
    results = list(iter_recent_jsonl(path, scan_limit_bytes=23))
    assert results == [{"id": "new"}]
    assert list(iter_recent_jsonl(path, retained_limit_bytes=10)) == []


def test_telemetry_load_is_bounded_and_retains_latest_distinct_requests(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "request_history.jsonl"
    _write(
        path,
        [
            {"request_id": "req_a", "status": "running", "response_text": "old"},
            {"request_id": "req_b", "status": "completed"},
            {"request_id": "req_a", "status": "completed", "response_text": "new"},
        ],
    )
    original_read_text = Path.read_text

    def no_whole_jsonl_reads(self, *args, **kwargs):
        if self.suffix == ".jsonl":
            raise AssertionError("full-file JSONL read would risk OOM")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_whole_jsonl_reads)
    store = TelemetryStore(tmp_path)
    asyncio.run(store.load())
    assert len(store.items) == 2
    assert store.get("req_a")["response_text"] == "new"
    assert [row["request_id"] for row in store.recent(2)] == ["req_a", "req_b"]


def test_diagnostics_and_test_runs_bound_their_startup_windows(
    tmp_path: Path, monkeypatch
) -> None:
    _write(tmp_path / "server_events.jsonl", [{"trace_id": str(i)} for i in range(10)])
    _write(
        tmp_path / "test_runs.jsonl",
        [
            {"run_id": "one", "status": "running"},
            {"run_id": "two", "status": "completed"},
            {"run_id": "one", "status": "stalled"},
        ],
    )
    original_read_text = Path.read_text

    def no_whole_jsonl_reads(self, *args, **kwargs):
        if self.suffix == ".jsonl":
            raise AssertionError("full-file JSONL read would risk OOM")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_whole_jsonl_reads)
    diag = DiagnosticStore(tmp_path, max_items=3)
    assert [row["trace_id"] for row in diag.recent(3)] == ["9", "8", "7"]
    runs = TestRunStore(tmp_path)
    asyncio.run(runs.load())
    assert runs.get("one")["status"] == "stalled"
    assert [row["run_id"] for row in runs.recent(2)] == ["one", "two"]
