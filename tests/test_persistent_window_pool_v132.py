from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_persistent_pool_is_active_routing_layer() -> None:
    entry = text("chrome_extension/background_entry.js")
    router = entry.index('"conversation_routing.js"')
    limiter = entry.index('"background_window_limit_v121.js"')
    pool = entry.index('"conversation_persistent_pool_v132.js"')
    restore = entry.index('"conversation_persistent_route_restore_v132.js"')
    dispatch = entry.index('"conversation_dispatch.js"')
    assert router < limiter < pool < restore < dispatch

    source = text("chrome_extension/conversation_persistent_pool_v132.js")
    assert 'policy: "persistent-prewarmed-total-window-pool-v132"' in source
    assert 'const target = normalizeTarget(state.target)' in source
    assert 'while (rows.length < effectiveTarget)' in source
    assert 'target + rows.filter(row => busyBefore.has(row.window_id)).length' in source
    assert 'standbyRows.length === target' in source
    assert 'await createStandby(reason)' in source
    assert 'window_owned = false' in source
    assert 'route.close_after = null' in source
    assert 'reassign-idle-persistent-route' in source
    assert 'worker_persistent_window_pool_exhausted' in source
    assert 'if (!await loginReady().catch(() => false)) return null;' in source


def test_persistent_pool_keeps_configured_standby_cardinality() -> None:
    source = text("chrome_extension/conversation_persistent_pool_v132.js")
    assert 'let rows = await physicalWindows();' in source
    assert 'while (rows.length < effectiveTarget)' in source
    assert 'target + rows.filter(row => busyBefore.has(row.window_id)).length' in source
    assert 'standbyRows.length === target' in source
    assert 'Math.max(0, current.length - target)' in source
    assert 'all_chatgpt_windows: rows.length' in source
    assert 'configured_target: target' in source
    assert 'effective_target: effective' in source


def test_saved_conversation_is_validated_after_slot_reassignment() -> None:
    source = text("chrome_extension/conversation_persistent_route_restore_v132.js")
    assert 'const expectedId = String(before?.conversation_id || "").trim()' in source
    assert 'await waitForConversationDecision(tab.id, expectedId)' in source
    assert 'persistent-pool-saved-conversation-unavailable' in source
    assert 'route.conversation_id = null' in source
    assert 'route.window_owned = false' in source
    assert 'speculative_windows: false' in source
    assert 'prewarmed_windows: true' in source


def test_capacity_control_uses_persistent_pool_as_window_authority() -> None:
    source = text("chrome_extension/background_capacity_control_v35.js")
    assert '__CHAT2API_PERSISTENT_WINDOW_POOL_V132__' in source
    assert 'pool.setTarget(target, cleanSource)' in source
    assert 'persistent-prewarmed-total-window-pool-v132' in source
    assert 'window_decision_authority: "persistent-window-pool-v132"' in source
    assert 'prewarmed_windows: true' in source


def test_admin_copy_describes_persistent_windows_without_legacy_helper_copy() -> None:
    # The Worker settings presenter owns this copy. Identity decoration must not
    # regain write authority over the canonical Worker table.
    source = text("app/admin_worker_limits_clipboard_v121.js")
    identity = text("app/admin_worker_identity_v131.js")
    assert "持续维持的可接待空闲窗口数量" in source
    assert "并发=同时执行的请求上限；备用=" in source
    assert "extensionDeviceBody" not in identity
    assert "空闲窗口常驻并预热" not in source
    assert "常驻窗口池跟随并发" not in source
    assert "常驻窗口池独立设置" not in source


def test_worker_extension_bundle_advertises_pool_contract() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert manifest["version"] == "0.22.95"
    assert "persistent prewarmed" in manifest["description"]


def test_new_pool_and_capacity_scripts_parse_in_node() -> None:
    for path in (
        "chrome_extension/conversation_persistent_pool_v132.js",
        "chrome_extension/conversation_persistent_route_restore_v132.js",
        "chrome_extension/background_capacity_control_v35.js",
        "app/admin_worker_identity_v131.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
