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
    assert 'const VERSION = "0.22.41-health-owner-v59"' in asset.text


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
        '["network", "网络"]',
        '["chatgpt", "ChatGPT"]',
        "healthState(row)",
        "运行状态中心",
        'worker_window: "admin_v21_5"',
        'health_columns: ["network", "chatgpt"]',
        'chained_capacity_poll: false',
    ):
        assert token in source

    assert '["platform", "平台"]' not in source
    assert 'ensureCell(tr, "platform")' not in source
    assert 'renderState(ensureCell(tr, "platform")' not in source
    assert 'ensureCell(tr, "worker_settings")' not in source
    assert 'renderState(ensureCell(tr, "worker_settings")' not in source
    assert 'globalThis.chat2apiRefreshWorkerWindowEditorsV58?.(rows)' not in source
    assert 'globalThis.chat2apiRefreshWorkerWindowEditorsV59?.(rows)' not in source
    assert '["health", "运行健康"]' not in source
    assert 'ensureCell(tr, "health")' not in source
    assert 'renderState(ensureCell(tr, "health")' not in source
    assert "removeLegacyHealthColumn" in source

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


def test_chatgpt_column_shows_login_status_without_composer_jargon():
    source = read(ROOT / "app" / "admin_v21_6.js")
    assert 'return {label: "已登录", level: "ok"' in source
    assert 'return {label: "已登录", level: "warn"' in source
    assert 'return {label: "未登录", level: "bad"' in source
    assert 'return {label: "检测中", level: "warn"' in source
    assert 'return {label: "未知", level: "warn"' in source
    assert "已登录 · Composer" not in source
    assert "Composer 未确认" not in source


def test_health_center_and_worker_settings_support_reordered_columns():
    health = read(ROOT / "app" / "admin_v21_6.js")
    live = read(ROOT / "app" / "admin_v21_5.js")
    columns = read(ROOT / "app" / "admin_extension_columns.js")

    assert 'td[data-chat2api-column-key="worker_settings"]' in live
    assert 'column: "worker_settings"' in live
    assert 'data-chat2api-structural-owner="worker-settings-v59"' in live
    assert 'columnCell(tr, "platform", 8)' not in live
    assert 'patchColumnSettingsLabels' not in live

    assert 'columnCell(tr, "client_id", 0)' in health
    assert 'columnCell(tr, "version", 2)' in health
    assert "row.appendChild(th)" in health
    assert "tr.appendChild(cell)" in health
    assert "insertCell" not in health
    assert "insertBefore(cell" not in health

    assert '{key: "worker_settings", label: "并发设置"}' in columns
    assert '{key: "bound_api_keys", label: "绑定 API Key 数"}' in columns
    assert 'data-chat2api-health-column="${key}"' in columns
    assert 'data-chat2api-health-cell="network"' in columns
    assert 'data-chat2api-health-cell="chatgpt"' in columns


def test_v21_6_is_installed_after_v21_5_and_before_runtime_contract():
    entry = read(ROOT / "app" / "entry.py")
    assert "install_v21_6_patch(app)" in entry
    assert entry.index("install_v21_5_patch(app)") < entry.index("install_v21_6_patch(app)")
    assert entry.index("install_v21_6_patch(app)") < entry.index("install_runtime_contract(app)")


def test_health_center_javascript_has_valid_syntax():
    subprocess.run(["node", "--check", str(ROOT / "app" / "admin_v21_6.js")], check=True)
