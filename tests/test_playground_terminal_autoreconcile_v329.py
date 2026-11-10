"""The 2026-11-10 A-request stayed 'running' in Test History after the
authoritative Broker had persisted stalled. Browser loss cannot own status.
"""
import asyncio
from pathlib import Path

from app.playground_chat_patch import _chat_run_id
from app.playground_truth_v170 import reconcile_playground_request_terminal
from app.telemetry import TelemetryStore
from app.test_runs import TestRunStore


RID = "req_b734ffd3fd1d4d20a7bc2466dab68200"


def _register(tests, rid=RID, *, abcd_report=None):
    row = {
        "run_id": _chat_run_id(rid),
        "request_id": rid,
        "request_ids": [rid],
        "status": "running",
        "test_type": "chat",
        "quality": {"source": "manual-playground-chat",
                    "playground_chat": "playground-chat-v3",
                    "transport_contract": "abcd-real-sse-v293",
                    "abcd_scene": "A"},
        "results": [{"kind": "chat", "request_id": rid,
                     "status": "running", "message": "聊天请求执行中"}],
    }
    if abcd_report is not None:
        row["abcd_report"] = abcd_report
    return tests.upsert(row)


def test_stalled_request_reconciles_on_backend_terminal_without_admin_get(tmp_path):
    tests, telemetry = TestRunStore(tmp_path), TelemetryStore(tmp_path)
    async def run():
        await _register(tests)
        await telemetry.upsert({"request_id": RID, "status": "running",
                                "completion_chars": 0})
        assert await reconcile_playground_request_terminal(tests, telemetry, RID) == 0
        assert tests.get(_chat_run_id(RID))["status"] == "running"
        await telemetry.upsert({"request_id": RID, "status": "stalled",
                                "error": "Request exceeded its browser lease"})
        assert await reconcile_playground_request_terminal(tests, telemetry, RID) == 1
        assert await reconcile_playground_request_terminal(tests, telemetry, RID) == 0
    asyncio.run(run())
    row = tests.get(_chat_run_id(RID))
    assert row["status"] == "stalled"
    assert row["finished_at"]
    assert row["results"][0]["status"] == "stalled"
    assert row["results"][0]["authoritative_request_status"] == "stalled"
    assert telemetry.get(RID)["status"] == "stalled"


def test_early_worker_unavailable_failure_is_reconciled_without_dispatch(tmp_path):
    tests, telemetry = TestRunStore(tmp_path), TelemetryStore(tmp_path)
    rid = "req_f4dc47431eb040bb972cda12bd87d9d5"
    async def run():
        await _register(tests, rid)
        await telemetry.upsert({"request_id": rid, "status": "error",
                                "error": "No Chrome extension is online"})
        assert await reconcile_playground_request_terminal(tests, telemetry, rid) == 1
    asyncio.run(run())
    assert tests.get(_chat_run_id(rid))["status"] == "failed"


def test_backend_completed_but_sse_disconnected_projects_warning_not_passed(tmp_path):
    tests, telemetry = TestRunStore(tmp_path), TelemetryStore(tmp_path)
    rid = "req_47c91cb03f3e49eda7cf7e494c3fa9aa"
    async def run():
        await _register(tests, rid)
        await telemetry.upsert({"request_id": rid, "status": "completed",
                                "completion_chars": 20492,
                                "diagnostics": {"api_stream_closed_before_delivery": True,
                                                "api_delivery_state": "disconnected"}})
        assert await reconcile_playground_request_terminal(tests, telemetry, rid) == 1
    asyncio.run(run())
    row = tests.get(_chat_run_id(rid))
    assert row["status"] == "warning"
    assert row["results"][0]["response_chars"] == 20492
    assert telemetry.get(rid)["status"] == "completed"


def test_automated_reconcile_does_not_override_abc_d_file_integrity_warning(tmp_path):
    tests, telemetry = TestRunStore(tmp_path), TelemetryStore(tmp_path)
    rid = "req_5ba6bebe8b0a4d808c69b2c1c49d9e42"
    report = {"scene": "C", "status": "warning", "passed": True,
              "checks": [{"passed": True, "label": "四文件字节 SHA256 对照"}]}
    async def run():
        await _register(tests, rid, abcd_report=report)
        await telemetry.upsert({"request_id": rid, "status": "completed",
                                "completion_chars": 1829})
        assert await reconcile_playground_request_terminal(tests, telemetry, rid) == 1
    asyncio.run(run())
    assert tests.get(_chat_run_id(rid))["status"] == "warning"
    assert tests.get(_chat_run_id(rid))["results"][0]["status"] == "warning"


def test_no_run_is_created_and_an_unrelated_run_is_untouched(tmp_path):
    tests, telemetry = TestRunStore(tmp_path), TelemetryStore(tmp_path)
    async def run():
        await _register(tests, RID)
        assert await reconcile_playground_request_terminal(
            tests, telemetry, "req_notregistered001"
        ) == 0
        assert await reconcile_playground_request_terminal(
            tests, telemetry, "imgreq_1234"
        ) == 0
    asyncio.run(run())
    assert len(tests.recent(10)) == 1
    assert tests.get(_chat_run_id(RID))["status"] == "running"


def test_recording_paths_use_request_scoped_reconciliation_not_bulk_scan():
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    assert source.count("await reconcile_playground_request_terminal(") >= 2
    assert "await reconcile_playground_request_terminal(\n                test_runs, telemetry, state.request_id," in source
    assert "await reconcile_playground_request_terminal(\n                test_runs, telemetry, request_id," in source
