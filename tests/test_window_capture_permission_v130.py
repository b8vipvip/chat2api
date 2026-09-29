import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_window_manager_capture_has_manifest_permission_for_remote_command() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert "<all_urls>" in manifest.get("host_permissions", [])
    observer = text("chrome_extension/background_window_observer_v90.js")
    assert "chrome.tabs.captureVisibleTab" in observer
    assert 'message?.type !== "window.manager.capture"' in observer


def test_worker_window_setting_is_a_persistent_prewarmed_pool_target() -> None:
    server = text("app/worker_limits_clipboard_v121_patch.py")
    canonical = text("app/admin_extension_columns.js")
    behavior = text("app/admin_worker_limits_clipboard_v121.js")
    identity = text("app/admin_worker_identity_v131.js")
    routed_limit = text("chrome_extension/background_window_limit_v121.js")
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")

    assert "persistent-prewarmed-total-window-pool-v132" in server
    assert "persistent-window-pool-v132" in server
    assert '{key: "worker_settings", label: "并发 / 备用设置"}' in canonical
    assert "持续维持的未分配可接待窗口数量" in canonical
    assert "data-v121-limit-summary" in canonical
    assert 'renderer: "canonical-worker-list-v152"' in behavior
    assert "extensionDeviceBody" not in identity
    assert "空闲窗口常驻并预热" not in canonical
    assert "常驻窗口池跟随并发" not in canonical
    assert "effective >= limit" in routed_limit
    assert "reserveForRequest" in routed_limit
    assert "function standbyRows(rows, value)" in pool
    assert "!assigned.has(row.window_id)" in pool
    assert "!busy.has(row.window_id)" in pool
    assert "while (standbyRows(rows, value).length < target)" in pool
    assert "const STANDBY_SEMANTICS_REVISION = 152" in pool
    assert "standby_semantics_revision: STANDBY_SEMANTICS_REVISION" in pool
    assert "runtime_ready_standby: true" in pool
    assert "await createStandby(reason)" in pool
