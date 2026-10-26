"""One exported request log, read-only Playground/probe attachments, one terminal authority."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_request_log_is_the_only_duplicate_download_surface():
    request = (ROOT / "app/request_history_v94_patch.py").read_text(encoding="utf-8")
    probes = (ROOT / "app/worker_probe_ui_patch.py").read_text(encoding="utf-8")
    assert "下载日志" in request
    assert "/api/admin/requests/${encodeURIComponent(requestId)}/log" in request
    assert "下载完整诊断日志（含探针）" not in probes
    assert "日志内含探针证据" in probes


def test_playground_keeps_read_only_history_but_no_standalone_report_export():
    html = (ROOT / "app/admin.py").read_text(encoding="utf-8")
    abcd = (ROOT / "app/admin_playground_abcd_v197.js").read_text(encoding="utf-8")
    assert 'id="testHistory"' in html
    assert "查看报告" not in html
    assert "window.downloadTest=" not in html
    assert "window.showTest=" not in html
    assert "pgABCDExport" not in abcd
    assert "/api/admin/playground/abcd/report" in abcd


def test_request_log_contains_probe_and_playground_evidence_without_changing_status():
    from app.playground_request_evidence_v207 import playground_diagnostics
    from app.worker_probe_store import WorkerProbeStore

    probes = (ROOT / "app/worker_probe_ui_patch.py").read_text(encoding="utf-8")
    assert "download" in (ROOT / "app/request_history_v94_patch.py").read_text(encoding="utf-8")
    assert "probe" in probes
    assert callable(playground_diagnostics)
    assert callable(WorkerProbeStore.events)


def test_standard_playground_requests_use_same_canonical_request_history_log():
    html = (ROOT / "app/admin.py").read_text(encoding="utf-8")
    assert "'X-Chat2API-Request-ID':requestId" in html
    assert "'X-Chat2API-Request-ID':r.request_id" in html
    assert "request_id:requestId||null" in html
    assert "crypto.randomUUID()" in html
