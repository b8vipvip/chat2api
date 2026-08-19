from pathlib import Path

import pytest

from app.linux_worker_proxy_catalog_patch import LinuxWorkerProxyCatalog, validate_proxy_share_link


ROOT = Path(__file__).resolve().parents[1]


def test_proxy_catalog_crud_persists_links_with_restricted_permissions(tmp_path):
    catalog = LinuxWorkerProxyCatalog(tmp_path)
    created = catalog.create("US 03", "vless://example-token@us03.example.com:443?security=tls&type=ws")
    assert created["proxy_id"].startswith("pxy_")
    assert created["name"] == "US 03"
    assert created["scheme"] == "vless"
    assert created["share_link"].startswith("vless://")
    assert catalog.path.exists()
    assert catalog.path.stat().st_mode & 0o777 == 0o600

    updated = catalog.update(created["proxy_id"], name="US 03 updated", share_link="trojan://secret@example.net:443")
    assert updated["name"] == "US 03 updated"
    assert updated["scheme"] == "trojan"
    assert LinuxWorkerProxyCatalog(tmp_path).list()[0]["share_link"] == "trojan://secret@example.net:443"

    catalog.delete(created["proxy_id"])
    assert catalog.list() == []


def test_proxy_catalog_rejects_unsupported_or_multiline_links():
    with pytest.raises(ValueError):
        validate_proxy_share_link("https://example.com")
    with pytest.raises(ValueError):
        validate_proxy_share_link("vless://one\nvless://two")


def test_proxy_catalog_patch_is_installed_and_admin_ui_supports_crud_and_worker_selection():
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    api = (ROOT / "app" / "linux_worker_proxy_catalog_patch.py").read_text(encoding="utf-8")
    ui = (ROOT / "app" / "admin_linux_worker_proxy_catalog.js").read_text(encoding="utf-8")
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")

    assert "install_linux_worker_proxy_catalog_patch(app)" in entry
    for route in (
        'app.get("/api/admin/linux-worker-proxies")',
        'app.post("/api/admin/linux-worker-proxies")',
        'app.patch("/api/admin/linux-worker-proxies/{proxy_id}")',
        'app.delete("/api/admin/linux-worker-proxies/{proxy_id}")',
    ):
        assert route in api
    assert 'SESSION_COOKIE' in api
    assert 'temp.chmod(0o600)' in api

    for token in (
        "添加代理",
        "代理管理",
        "新增并保存",
        "应用所选代理",
        "保存并应用",
        "/api/admin/linux-worker-proxies",
        "data-proxy",
        "stopImmediatePropagation",
    ):
        assert token in ui
    assert 'ADMIN_LINUX_PROXY_CATALOG_ASSET' in runtime
    assert 'chat2api-linux-worker-proxy-catalog.js' in runtime


def test_remote_login_opens_official_chatgpt_login_route_before_frame_streaming():
    helper = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    assert 'LOGIN_URL = os.environ.get("CHAT2API_LOGIN_URL", "https://chatgpt.com/auth/login")' in helper
    assert "navigation = _navigate_login_page()" in helper
    assert '_focus_window(window_id, error_name="login_navigation_focus_failed")' in helper
    assert '_type_url_into_focused_chrome(LOGIN_URL, error_name="login_navigation_failed")' in helper
