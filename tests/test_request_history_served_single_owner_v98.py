from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.request_history_v94_patch import ASSET_COMPILER_REVISION, compile_legacy_admin_asset
from app.v21_3_patch import REQUEST_HISTORY_LEGACY_ASSETS, _bundle_source


ROOT = Path(__file__).resolve().parents[1]
OLD_OWNER_SIGNATURES = (
    "loadRequestsV7",
    "loadRequestsV8",
    '$("rqGo").onclick = loadRequests',
    "$('rqGo').onclick = loadRequests",
)


def test_compiler_retires_historical_request_renderers_at_source_boundary() -> None:
    assert REQUEST_HISTORY_LEGACY_ASSETS == {"admin_v7.js", "admin_v8.js", "admin_v10.js"}
    for filename in sorted(REQUEST_HISTORY_LEGACY_ASSETS):
        source = (ROOT / "app" / filename).read_text(encoding="utf-8")
        compiled = compile_legacy_admin_asset(filename, source)
        for signature in OLD_OWNER_SIGNATURES:
            assert signature not in compiled, f"{filename} still owns Request History via {signature}"
    assert "simplifyRequestPage" not in compile_legacy_admin_asset(
        "admin_v7.js", (ROOT / "app" / "admin_v7.js").read_text(encoding="utf-8")
    )
    assert "ensureDiagnosticControls" not in compile_legacy_admin_asset(
        "admin_v8.js", (ROOT / "app" / "admin_v8.js").read_text(encoding="utf-8")
    )
    assert "rqBody" not in compile_legacy_admin_asset(
        "admin_v10.js", (ROOT / "app" / "admin_v10.js").read_text(encoding="utf-8")
    )


def test_actual_production_bundle_uses_passive_historical_request_assets() -> None:
    bundle = _bundle_source()
    for signature in OLD_OWNER_SIGNATURES:
        assert signature not in bundle, f"admin-latest bundle still contains old request owner: {signature}"
    assert "BEGIN admin_v7.js" in bundle
    assert "BEGIN admin_v8.js" in bundle
    assert "BEGIN admin_v10.js" in bundle
    # Unrelated historical features remain in the bundle; we are removing only
    # Request History decision rights rather than deleting whole legacy assets.
    assert "window.__chat2apiAdminPatchErrors" in bundle
    assert "admin bundle ready" in bundle


def _run_fresh_entry_probe() -> dict:
    code = r'''
import json
from fastapi.testclient import TestClient
from app.entry import app
from app.request_history_v94_patch import ASSET_COMPILER_REVISION

client = TestClient(app)
page = client.get('/admin')
bundle = client.get('/assets/chat2api-admin-latest.js')
legacy = {}
for route in ('/assets/chat2api-v7.js','/assets/chat2api-v8.js','/assets/chat2api-v10.js'):
    response = client.get(route)
    legacy[route] = {'status': response.status_code, 'text': response.text}
print(json.dumps({
    'page_status': page.status_code,
    'page': page.text,
    'bundle_status': bundle.status_code,
    'bundle': bundle.text,
    'legacy': legacy,
    'compiler_revision': getattr(app.state, 'request_history_asset_compiler_revision', ''),
    'expected_revision': ASSET_COMPILER_REVISION,
}, ensure_ascii=False))
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_actual_served_admin_and_assets_have_one_request_history_owner() -> None:
    probe = _run_fresh_entry_probe()
    assert probe["page_status"] == 200
    assert probe["bundle_status"] == 200
    assert probe["compiler_revision"] == probe["expected_revision"] == ASSET_COMPILER_REVISION

    page = probe["page"]
    assert page.count("async function loadRequests()") == 1
    assert page.count('id="rqBody"') == 1
    assert page.count('id="rqGo"') == 1
    assert "时间（北京时间）" in page
    for label in ("请求ID", "设备标识", "对话", "日志"):
        assert f"<th>{label}</th>" in page
    assert "requestHistoryButton(tr,'查看对话'" in page
    assert "requestHistoryButton(tr,'下载日志'" in page
    assert "cell.colSpan=13" in page

    bundle = probe["bundle"]
    for signature in OLD_OWNER_SIGNATURES:
        assert signature not in bundle

    for route, payload in probe["legacy"].items():
        assert payload["status"] == 200, route
        for signature in OLD_OWNER_SIGNATURES:
            assert signature not in payload["text"], (route, signature)


def test_canonical_renderer_has_exact_thirteen_cell_contract() -> None:
    probe = _run_fresh_entry_probe()
    page = probe["page"]
    start = page.index("async function loadRequests()")
    end = page.index("requestHistoryEnsureControls();", start)
    loader = page[start:end]
    # time, request ID, type, key, device, model, attachment, first-token,
    # total-time and token are normal cells; status is one cell; conversation
    # and log are two action cells. No historical renderer may append extras.
    assert loader.count("requestHistoryCell(tr,") == 10
    assert loader.count("requestHistoryStatusCell(tr,") == 1
    assert loader.count("requestHistoryButton(tr,") == 2
    assert loader.count("requestHistoryCell(tr,") + loader.count("requestHistoryStatusCell(tr,") + loader.count("requestHistoryButton(tr,") == 13
