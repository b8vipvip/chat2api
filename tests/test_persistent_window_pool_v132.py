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
    assert 'row.runtime_ready === true' in source
    assert 'validateIdleRuntime' in source
    assert 'function standbyRows(rows, value)' in source
    assert '!assigned.has(row.window_id)' in source
    assert '!busy.has(row.window_id)' in source
    assert 'standby.length === target' in source
    assert 'standby_semantics_revision: STANDBY_SEMANTICS_REVISION' in source
    assert 'const STANDBY_SEMANTICS_REVISION = 152' in source
    assert 'await createStandby(reason)' in source
    assert 'route.window_owned = true' in source
    assert 'post-admission-standby-refill-v152' in source
    assert 'worker_persistent_window_pool_exhausted' in source
    assert 'if (!await loginReady().catch(() => false)) return null;' in source

    # The pool is no longer allowed to cancel the route's 5-minute lease.
    assert 'markPooledRoute' not in source
    assert 'reassign-idle-persistent-route' not in source


def test_persistent_pool_keeps_configured_unassigned_standby_cardinality() -> None:
    source = text("chrome_extension/conversation_persistent_pool_v132.js")
    assert 'let rows = await physicalWindows();' in source
    assert 'while (standbyRows(rows, value).length < target)' in source
    assert 'const compactStandby = isDisabled || auth.explicit_logout === true;' in source
    assert 'const standbyTarget = compactStandby ? Math.min(1, target) : target;' in source
    assert 'Math.max(0, standby.length - effective)' in source
    assert 'routable_total: routableRows.length' in source
    assert 'unroutable_total: unroutableRows.length' in source
    assert 'runtime_ready_total: runtimeReadyRows.length' in source
    assert 'runtime_stale_total:' in source
    assert 'all_chatgpt_windows: rows.length' in source
    assert 'configured_target: target' in source
    assert 'effective_target: effective' in source
    assert 'routed: routedRows.length' in source
    assert 'leased,' in source
    assert 'standby_excludes_routed_windows: true' in source


def test_transient_login_readiness_never_compacts_configured_standby() -> None:
    source = text("chrome_extension/conversation_persistent_pool_v132.js")

    # `unknown`, `checking`, or a temporary composer probe miss are not logout
    # evidence. They may pause warming/admission but must retain the configured
    # standby target instead of destructively shrinking it to one window.
    assert 'async function loginStatus()' in source
    assert 'ready: stateValue === "ready" && composerReady' in source
    assert 'explicit_logout: stateValue === "login_required"' in source
    assert 'const compactStandby = isDisabled || auth.explicit_logout === true;' in source
    assert 'const standbyTarget = compactStandby ? Math.min(1, target) : target;' in source
    assert 'ready ? target : Math.min(1, target)' not in source
    assert 'transient_login_state_preserves_standby_target: true' in source

    # Diagnostics must make a future cardinality problem self-explaining.
    assert 'login_state: auth.state' in source
    assert 'login_composer_ready: auth.composer_ready' in source
    assert 'login_explicit_logout: auth.explicit_logout' in source
    assert '? "login_required"' in source
    assert '? "login_not_ready"' in source


def test_saved_conversation_is_validated_after_slot_reassignment_without_losing_lease_ownership() -> None:
    source = text("chrome_extension/conversation_persistent_route_restore_v132.js")
    assert 'const expectedId = String(before?.conversation_id || "").trim()' in source
    assert 'await waitForConversationDecision(tab.id, expectedId)' in source
    assert 'persistent-pool-saved-conversation-unavailable' in source
    assert 'route.conversation_id = null' in source
    assert 'route.window_owned = true' in source
    assert 'standby_excludes_routed_windows: true' in source
    assert 'speculative_windows: false' in source
    assert 'prewarmed_windows: true' in source


def test_capacity_control_uses_persistent_pool_as_window_authority() -> None:
    source = text("chrome_extension/background_capacity_control_v35.js")
    assert '__CHAT2API_PERSISTENT_WINDOW_POOL_V132__' in source
    assert 'pool.setTarget(target, cleanSource)' in source
    assert 'persistent-prewarmed-total-window-pool-v132' in source
    assert 'const standby = Number(snapshot.idle || snapshot.standby || 0);' in source
    assert 'standby === target' in source
    assert 'window_decision_authority: "persistent-window-pool-v132"' in source
    assert 'prewarmed_windows: true' in source


def test_admin_copy_describes_persistent_windows_without_legacy_helper_copy() -> None:
    behavior = text("app/admin_worker_limits_clipboard_v121.js")
    canonical = text("app/admin_extension_columns.js")
    identity = text("app/admin_worker_identity_v131.js")
    assert "持续维持的可接待空闲窗口数量" in behavior
    assert "并发=同时执行请求上限；备用=持续维持的未分配可接待窗口数量" in canonical
    assert "extensionDeviceBody" not in identity


def test_worker_extension_bundle_advertises_pool_contract() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert manifest["version"] == "0.22.103"
    assert "persistent prewarmed" in manifest["description"]


def test_new_pool_and_capacity_scripts_parse_in_node() -> None:
    for path in (
        "chrome_extension/conversation_persistent_pool_v132.js",
        "chrome_extension/conversation_persistent_route_restore_v132.js",
        "chrome_extension/background_capacity_control_v35.js",
        "chrome_extension/background_window_observer_v90.js",
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
