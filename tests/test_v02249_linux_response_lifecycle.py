from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def test_linux_launcher_does_not_prune_extension_windows_by_default():
    source = text("scripts/linux_worker_chrome_launcher.sh")
    assert "CHAT2API_TAB_INIT_PRUNE:-0" in source
    assert "--wait 45" not in source

def test_reserve_target_counts_spares_not_routed_windows():
    source = text("chrome_extension/background_reserve_pool_v29.js")
    assert "spareTotal = Math.max(0, snapshot.total - snapshot.routed)" in source
    assert "const ROUTE_IDLE_CLOSE_MS = 5 * 60 * 1000;" in source

def test_conversation_affinity_is_five_minutes():
    assert "const IDLE_CLOSE_MS = 5 * 60 * 1000;" in text("chrome_extension/conversation_routing.js")
    assert "ROUTE_IDLE_CLOSE_SECONDS = 2 * 60" in text("app/v21_13_patch.py")

def test_response_requires_terminal_rich_dom_settlement():
    source = text("chrome_extension/content_request_v6.js")
    assert "response_terminal_settle_revision: 81" in source
    assert "Date.now() - settleStableSince < 3000" in source

def test_multimodal_waits_for_slow_upload_processing():
    source = text("chrome_extension/content_multimodal_v78.js")
    assert "timeoutMs = 60000, stableMs = 3000" in source
    assert "upload_settle_revision: Number(settled.upload_settle_revision || 0)" in source

def test_playground_messages_have_time_and_copy_actions():
    source = text("app/admin_playground_chat_v69.js")
    assert "created_at:new Date().toISOString()" in source
    assert "data-copy-index=" in source
    assert "formatMessageTime" in source

def test_worker_occupancy_can_show_live_physical_window_count():
    source = text("app/admin_worker_presentation_v66.js")
    assert "reserve_window_total" in source
    assert "当前实际 ChatGPT 浏览器窗口" in source

def test_historical_v212_does_not_stamp_runtime_identity():
    assert 'payload["server_version"] = PATCH_VERSION' not in text("app/v21_2_patch.py")

def test_release_versions():
    runtime = text("app/runtime_contract.py")
    manifest = text("chrome_extension/manifest.json")
    assert 'SERVER_RUNTIME_VERSION = "0.22.53"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.23"' in runtime
    assert '"version": "0.8.23"' in manifest