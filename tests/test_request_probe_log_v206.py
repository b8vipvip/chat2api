from pathlib import Path

from app.worker_probe_store import WorkerProbeStore

APP = Path(__file__).resolve().parents[1] / "app"


def test_per_request_log_includes_the_original_sanitized_probe_timeline(tmp_path):
    store = WorkerProbeStore(tmp_path)
    request_id = "req_321fe7b02f1b4401b6982ee77567b4c5"
    events = [
        {"request_id": request_id, "sequence": 1, "tool_id": "response_boundary_v1",
         "stage": "request_pre_submit", "classification": "baseline",
         "facts": {"scene_rich_block_count": 0}},
        {"request_id": request_id, "sequence": 2, "tool_id": "response_boundary_v1",
         "stage": "before_terminal", "classification": "completed_observed",
         "facts": {"scene_rich_block_count": 1023}},
    ]
    import json
    store._file(request_id).write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    assert store.events(request_id) == events
    assert store.summary(request_id)["event_count"] == 2

    source = (APP / "v8_patch.py").read_text(encoding="utf-8")
    assert '"worker_probe_evidence": request_probe_evidence(request_id)' in source
    assert '"events": store.events(request_id)' in source
    assert '"worker_probe_evidence": request_probe_evidence(str(row.get("request_id") or ""))' in source
    assert "Worker facts are already allowlisted" in source


def test_request_chat_downloads_same_log_without_separate_probe_package():
    source = (APP / "worker_probe_ui_patch.py").read_text(encoding="utf-8")
    assert "下载完整诊断日志（含探针）" in source
    assert "'/log'" in source
    assert "查看探针" not in source
