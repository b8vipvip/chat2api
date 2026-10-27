"""Regression: an API request waiting for Worker admission must be in Request History."""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.telemetry import TelemetryStore

ROOT = Path(__file__).resolve().parents[1]


def test_queue_row_visible_before_dispatch_and_immutable_after_terminal(tmp_path):
    async def run():
        history = TelemetryStore(tmp_path)
        charged = []
        async def account(row):
            if row.get("status") == "completed":
                charged.append(row["request_id"])
        history.usage_accounting = account
        row = {
            "request_id": "req_queue_visible_220",
            "response_id": "chatcmpl-queued",
            "api_key_id": "mv3c",
            "api_key_name": "mv3c",
            "client_id": None,
            "requested_model": "gpt-5.6-sol",
            "request_type": "text",
            "status": "queued",
            "diagnostics": {"request_admission_stage": "selecting-worker"},
            "usage": {"total_tokens": 360},
        }
        await history.upsert(row)
        first = history.query(limit=20)["data"]
        assert len(first) == 1
        assert first[0]["status"] == "queued"
        assert first[0]["request_id"] == row["request_id"]
        assert charged == []
        recorded_at = first[0]["recorded_at"]
        await history.update(row["request_id"], {
            "client_id": "ext_9Gf5LdHw2TL",
            "diagnostics": {"request_admission_stage": "waiting-capacity"},
        })
        pending = history.get(row["request_id"])
        assert pending["status"] == "queued"
        assert pending["client_id"] == "ext_9Gf5LdHw2TL"
        assert pending["recorded_at"] == recorded_at
        await history.upsert({"request_id": row["request_id"], "status": "running"})
        await history.upsert({"request_id": row["request_id"], "status": "completed"})
        assert charged == [row["request_id"]]
        await history.upsert({"request_id": row["request_id"], "status": "queued"})
        assert history.get(row["request_id"])["status"] == "completed"
        assert len(history.query(limit=20)["data"]) == 1
    asyncio.run(run())


def test_api_queues_before_capacity_and_only_then_marks_running():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    handler = source[source.index('    @app.post("/v1/chat/completions")'):
                     source.index('    @app.post("/v1/images/generations")')]
    assert handler.index("await record_request_queued(") < handler.index(
        "client_id = await resolve_client_with_reconnect_grace("
    ) < handler.index("state = await broker.create(request_id, client_id)") < handler.index(
        "await record_request_running("
    )
    assert '"status": "queued"' in source
    assert '"request_admission_stage": "selecting-worker"' in source
    assert '"request_admission_stage": "waiting-capacity"' in handler
    assert '"request_admission_stage": "aborted-before-worker-dispatch"' in handler
    assert 'state.diagnostics["request_admission_stage"] = "dispatched"' in handler


def test_request_history_ui_can_show_and_filter_pre_admission_queue():
    source = (ROOT / "app/request_history_v94_patch.py").read_text(encoding="utf-8")
    assert "text==='queued'?'排队中':text" in source
    assert "queuedOption.value='queued'" in source
    assert "request_admission_stage||''" in source
