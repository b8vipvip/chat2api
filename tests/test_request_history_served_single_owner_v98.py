from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import admin as admin_module
from app.entry import app
from app.request_history_v94_patch import ASSET_COMPILER_REVISION, compile_legacy_admin_asset


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_EXTERNAL_REQUEST_OWNERS = ("rqBody", "loadRequests", "rqGo")


def test_compiler_retires_historical_request_renderers_at_source_boundary() -> None:
    for filename in ("admin_v7.js", "admin_v8.js", "admin_v10.js"):
        source = (ROOT / "app" / filename).read_text(encoding="utf-8")
        compiled = compile_legacy_admin_asset(filename, source)
        for token in FORBIDDEN_EXTERNAL_REQUEST_OWNERS:
            assert token not in compiled, f"{filename} still owns Request History via {token}"


def test_actual_served_legacy_assets_are_request_history_passive() -> None:
    client = TestClient(app)
    mapping = {
        "/assets/chat2api-v7.js": "admin_v7.js",
        "/assets/chat2api-v8.js": "admin_v8.js",
        "/assets/chat2api-v10.js": "admin_v10.js",
    }
    for route, filename in mapping.items():
        response = client.get(route)
        assert response.status_code == 200, (route, response.status_code, response.text[:200])
        expected = compile_legacy_admin_asset(
            filename,
            (ROOT / "app" / filename).read_text(encoding="utf-8"),
        )
        assert response.text == expected
        for token in FORBIDDEN_EXTERNAL_REQUEST_OWNERS:
            assert token not in response.text, f"served {route} still owns Request History via {token}"


def test_admin_page_has_one_canonical_request_history_renderer() -> None:
    html = admin_module.ADMIN_HTML
    assert html.count("async function loadRequests()") == 1
    assert html.count('id="rqBody"') == 1
    assert html.count('id="rqGo"') == 1
    assert "时间（北京时间）" in html
    for label in ("请求ID", "设备标识", "对话", "日志"):
        assert f"<th>{label}</th>" in html
    assert "requestHistoryButton(tr,'查看对话'" in html
    assert "requestHistoryButton(tr,'下载日志'" in html
    assert "cell.colSpan=13" in html
    assert getattr(app.state, "request_history_asset_compiler_revision", "") == ASSET_COMPILER_REVISION


def test_no_external_script_loaded_by_admin_retains_request_table_decision_rights() -> None:
    client = TestClient(app)
    response = client.get("/admin")
    assert response.status_code == 200
    script_srcs = re.findall(r'<script[^>]+src="([^"]+)"', response.text)
    offenders: dict[str, list[str]] = {}
    for src in script_srcs:
        if not src.startswith("/assets/"):
            continue
        asset = client.get(src)
        assert asset.status_code == 200, (src, asset.status_code)
        tokens = [token for token in FORBIDDEN_EXTERNAL_REQUEST_OWNERS if token in asset.text]
        if tokens:
            offenders[src] = tokens
    assert offenders == {}, f"external request-history owners remain: {offenders}"
