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


def test_worker_window_setting_is_an_on_demand_cap_not_a_warm_pool_target() -> None:
    server = text("app/worker_limits_clipboard_v121_patch.py")
    browser = text("app/admin_worker_limits_clipboard_v121.js")
    routed_limit = text("chrome_extension/background_window_limit_v121.js")
    assert '"policy": "on-demand-hard-cap-no-warm-pool"' in server
    assert "窗口不会预开" in browser
    assert "effective >= limit" in routed_limit
    assert "reserveForRequest" in routed_limit
