import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.v21_6_patch import ADMIN_HEALTH_ASSET, PATCH_VERSION, install_v21_6_patch


ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_health_center_asset_is_injected_after_historical_admin_assets():
    runtime = FastAPI(version="0.21.5")

    @runtime.get("/admin")
    async def admin():
        return HTMLResponse('<html><body><script src="/assets/chat2api-v21-5.js"></script></body></html>')

    install_v21_6_patch(runtime)
    assert runtime.version == PATCH_VERSION

    client = TestClient(runtime)
    html = client.get("/admin").text
    assert f'<script src="{ADMIN_HEALTH_ASSET}"></script>' in html
    assert html.index("/assets/chat2api-v21-5.js") < html.index(ADMIN_HEALTH_ASSET)

    asset = client.get(ADMIN_HEALTH_ASSET)
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert 'const VERSION = "0.21.6"' in asset.text


def test_health_center_uses_existing_extension_metadata_without_new_host_credentials():
    source = read(ROOT / "app" / "admin_v21_6.js")
    for token in (
        'metadata(row).extension_version',
        'meta.platform_os',
        'meta.platform_arch',
        'meta.network_probe_status',
        'meta.network_country_code',
        'meta.chatgpt_login_state',
        'meta.chatgpt_login_composer_ready',
        'api("/api/admin/extensions")',
        '["platform", "平台"]',
        '["network", "网络"]',
        '["chatgpt", "ChatGPT"]',
        '["health", "运行健康"]',
    ):
        assert token in source

    for forbidden in (
        "CHAT2API_ADMIN_PASSWORD",
        "PAIRING_CODE",
        "clientToken",
        "systemctl restart",
        "journalctl",
    ):
        assert forbidden not in source


def test_health_center_preserves_network_and_login_safety_boundaries():
    source = read(ROOT / "app" / "admin_v21_6.js")
    assert 'status === "china-mainland"' in source
    assert "只阻止主动 warm/prewarm；真实 API 请求的按需兜底仍保留" in source
    assert 'state === "login_required"' in source
    assert "需要在可见窗口中人工完成登录/CAPTCHA/2FA" in source
    assert 'label: "可调用 · 不主动预热"' in source
    assert 'label: "需要人工登录"' in source


def test_health_center_and_live_concurrency_support_reordered_columns():
    health = read(ROOT / "app" / "admin_v21_6.js")
    live = read(ROOT / "app" / "admin_v21_5.js")

    # v0.20 inserted account_type at base index 3. Historical positions remain
    # safe fallbacks before the v0.21.8 layout controller marks cells, while
    # keyed lookup becomes authoritative after a user reorders the table.
    assert 'columnCell(tr, "client_id", 0)' in live
    assert 'columnCell(tr, "concurrency", 5)' in live
    assert 'columnCell(tr, "concurrency", 4)' not in live
    assert 'data-chat2api-column-key' in live

    assert 'columnCell(tr, "client_id", 0)' in health
    assert 'columnCell(tr, "version", 2)' in health
    assert "row.appendChild(th)" in health
    assert "tr.appendChild(cell)" in health
    assert "insertCell" not in health
    assert "insertBefore(cell" not in health


def test_v21_6_is_installed_after_v21_5_and_before_runtime_contract():
    entry = read(ROOT / "app" / "entry.py")
    assert "install_v21_6_patch(app)" in entry
    assert entry.index("install_v21_5_patch(app)") < entry.index("install_v21_6_patch(app)")
    assert entry.index("install_v21_6_patch(app)") < entry.index("install_runtime_contract(app)")


def test_health_center_javascript_has_valid_syntax():
    subprocess.run(["node", "--check", str(ROOT / "app" / "admin_v21_6.js")], check=True)
