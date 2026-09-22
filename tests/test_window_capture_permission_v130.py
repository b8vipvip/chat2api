import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_window_manager_capture_has_manifest_permission_for_remote_command() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    # Window-manager screenshots are initiated from the server/admin console,
    # not from a browser-action user gesture. captureVisibleTab therefore needs
    # the persistent <all_urls> host permission rather than relying on activeTab.
    assert "<all_urls>" in manifest.get("host_permissions", [])
    observer = text("chrome_extension/background_window_observer_v90.js")
    assert "chrome.tabs.captureVisibleTab" in observer
    assert 'message?.type !== "window.manager.capture"' in observer


def test_worker_window_setting_is_a_persistent_prewarmed_pool_target() -> None:
    server = text("app/worker_limits_clipboard_v121_patch.py")
    browser = text("app/admin_worker_limits_clipboard_v121.js")
    identity = text("app/admin_worker_identity_v131.js")
    routed_limit = text("chrome_extension/background_window_limit_v121.js")
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")

    assert "persistent-prewarmed-total-window-pool-v132" in server
    assert "persistent-window-pool-v132" in server
    assert 'headerCell.textContent = "并发 / 备用"' in browser
    assert "持续维持的可接待空闲窗口数量" in browser
    assert "data-v121-limit-summary" in browser
    assert "extensionDeviceBody" not in identity
    assert "空闲窗口常驻并预热" not in browser
    assert "常驻窗口池跟随并发" not in browser
    assert "effective >= limit" in routed_limit
    assert "reserveForRequest" in routed_limit
    assert "while (rows.length < effectiveTarget)" in pool
    assert "await createStandby(reason)" in pool
