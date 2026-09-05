from __future__ import annotations

from pathlib import Path

from app import admin as admin_module
from app.request_history_v94_patch import PATCH_ID, _normalize_admin_html


ROOT = Path(__file__).resolve().parents[1]


def test_request_history_v94_feature_assets_do_not_own_rqbody() -> None:
    identity = (ROOT / "app" / "admin_request_device_identity_v47.js").read_text(encoding="utf-8")
    prompt72 = (ROOT / "app" / "admin_prompt_config_v72.js").read_text(encoding="utf-8")
    prompt75 = (ROOT / "app" / "admin_prompt_config_v75.js").read_text(encoding="utf-8")
    window = (ROOT / "app" / "admin_window_manager_v88.js").read_text(encoding="utf-8")

    for source in (identity, prompt72, prompt75, window):
        assert "rqBody" not in source
        assert "MutationObserver" not in source

    assert "window.loadRequests" not in prompt72
    assert "loadRequests" not in prompt75
    assert "structural_owner: false" in identity
    assert "structural_owner: false" in prompt75
    assert 'structural_owner: "window-manager-only"' in window


def test_request_history_v94_normalizer_replaces_base_owner_instead_of_wrapping_it() -> None:
    html = _normalize_admin_html(admin_module.ADMIN_HTML)

    assert html.count("async function loadRequests()") == 1
    assert html.count("$('rqGo').onclick=loadRequests;") == 1
    assert "时间（北京时间）" in html
    assert "<th>请求ID</th>" in html
    assert "<th>设备标识</th>" in html
    assert "<th>提示词</th>" in html
    assert "<th>日志</th>" in html
    assert "body.replaceChildren()" in html
    assert "requestHistoryCell" in html
    assert "requestHistoryButton" in html
    assert "textContent=requestHistoryText(value)" in html
    assert ".innerHTML=d.data.map" not in html


def test_request_history_v94_is_installed_after_prompt_ui_and_is_fail_fast() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "request_history_v94_patch.py").read_text(encoding="utf-8")

    assert entry.index("install_prompt_config_v72_patch(app)") < entry.index("install_request_history_v94_patch(app)")
    assert "expected exactly one base request-history header" in patch
    assert "expected exactly one base loadRequests owner" in patch
    assert f'PATCH_ID = "{PATCH_ID}"' in patch
