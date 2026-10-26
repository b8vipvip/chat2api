from app.playground_request_evidence_v207 import (
    playground_diagnostics, playground_link, request_test_index,
)


class Store:
    def __init__(self, runs):
        self.runs = runs

    def recent(self, limit):
        return list(self.runs)[-limit:][::-1]


def test_origin_marker_only_for_matching_real_test_request():
    store = Store([
        {"run_id": "chatrun_a", "request_id": "req_abcd123456",
         "request_ids": ["req_abcd123456"], "test_type": "chat",
         "status": "failed", "quality": {"source": "manual-playground-chat"}},
    ])
    index = request_test_index(store)
    assert playground_link(index.get("req_abcd123456")) == {
        "is_playground_test": True, "playground_run_id": "chatrun_a",
        "playground_test_type": "chat",
    }
    assert playground_link(index.get("req_realbot234567")) == {"is_playground_test": False}


def test_abcd_diagnostics_keeps_separate_content_grades_from_api_terminal():
    run = {
        "run_id": "chatrun_a", "request_id": "req_abcd123456",
        "status": "failed", "error": "1/3 images",
        "results": [{"request_id": "req_abcd123456", "status": "failed"}],
        "quality": {"source": "manual-playground-chat"},
        "abcd_report": {"scene": "B", "status": "failed", "checks": [
            {"label": "三张独立图片", "passed": False},
        ], "turns": [{"request_id": "req_abcd123456", "frames": 3}]},
    }
    detail = playground_diagnostics(run)
    assert detail["is_playground_test"] is True
    assert detail["abcd_report"]["checks"][0]["passed"] is False
    assert detail["test_results"][0]["status"] == "failed"
    assert "api_request_status" not in detail


def test_admin_and_download_reference_same_persisted_origin():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app"
    history = (root / "request_history_v94_patch.py").read_text()
    logs = (root / "v8_patch.py").read_text()
    assert "request_test_index(getattr(app.state, \"test_runs\", None))" in history
    assert "idCell.appendChild(marker)" in history
    assert "marker.textContent='测'" in history
    assert '"playground_diagnostics": playground_diagnostics(test_run)' in logs


def test_browser_abcd_report_persisted_after_chat_records():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app"
    browser = (root / "admin_playground_abcd_v197.js").read_text()
    server = (root / "playground_chat_patch.py").read_text()
    assert browser.index('"/api/admin/playground/chat-records"') < browser.index('"/api/admin/playground/abcd/report"')
    assert 'dependencies=[Depends(require_abcd_report_admin)]' in server
    assert 'app.state.test_runs.update(run_id, {"abcd_report": result, "abcd_scene": scene})' in server
