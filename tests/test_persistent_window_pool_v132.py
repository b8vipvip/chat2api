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



def test_bound_online_worker_checks_standby_integrity_every_15_seconds_and_immediately() -> None:
    source = text("chrome_extension/conversation_persistent_pool_v132.js")
    assert 'const ONLINE_INTEGRITY_INTERVAL_MS = 15 * 1000;' in source
    assert '["serverUrl", "clientId", "clientToken", "socketState"]' in source
    assert 'String(stored?.socketState || "") === "connected"' in source
    assert 'return { bound, online, eligible: bound && online };' in source
    assert 'onlineIntegrityTick("online-integrity-15s")' in source
    assert 'changes.socketState?.newValue === "connected"' in source
    assert '"worker-online-immediate"' in source
    assert 'onlineIntegrityTick("startup-immediate")' in source
    assert 'online_integrity_interval_ms: ONLINE_INTEGRITY_INTERVAL_MS' in source


def test_online_integrity_is_trigger_only_and_persistent_pool_remains_single_terminal_authority() -> None:
    source = text("chrome_extension/conversation_persistent_pool_v132.js")
    start = source.index("async function onlineIntegrityTick(")
    end = source.index("async function setTarget(", start)
    watchdog = source[start:end]
    assert "await reconcile(reason)" in watchdog
    assert "createStandby(" not in watchdog
    assert "closeWindow(" not in watchdog
    assert "chrome.windows.create" not in watchdog
    assert "chrome.windows.remove" not in watchdog
    assert 'window_decision_authority: "persistent-window-pool-v132"' in source
    assert "online_integrity_single_authority: true" in source
    assert "if (state.reconcilePromise) return state.reconcilePromise;" in source


def test_linux_and_windows_browser_workers_share_the_same_extension_window_authority() -> None:
    launcher = text("scripts/linux_worker_chrome_launcher.sh")
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    agent = text("scripts/linux_worker_agent.py")
    assert 'EXTENSION_DIR="${CHAT2API_EXTENSION_DIR:-/opt/chat2api-worker/chrome_extension}"' in launcher
    assert '--disable-extensions-except="$EXTENSION_DIR"' in launcher
    assert '--load-extension="$EXTENSION_DIR"' in launcher
    assert 'window_decision_authority: "persistent-window-pool-v132"' in pool
    assert "chrome.windows.create" not in agent
    assert "chrome.windows.remove" not in agent

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
    assert manifest["version"] == "0.22.107"
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



def test_zero_window_worker_bootstraps_one_pool_owned_login_surface() -> None:
    source = text("chrome_extension/conversation_persistent_pool_v132.js")
    assert 'const LOGIN_PROBE_TAB_KEY = "chatgptLoginProbeTabId"' in source
    assert 'async function createBootstrapSurface(' in source
    assert 'async function ensureLoginSurface(' in source
    assert 'if (!isDisabled && rows.length === 0)' in source
    assert 'pending_reason: "login_bootstrap"' in source
    assert 'scheduleReconcile("login-bootstrap-follow-up", 1200)' in source
    assert 'login_bootstrap_single_surface: true' in source
    assert 'login_bootstrap_authority: "persistent-window-pool-v132"' in source
    assert 'state.ensureLoginSurface = ensureLoginSurface;' in source


def test_login_detector_delegates_bootstrap_window_lifecycle_to_persistent_pool() -> None:
    login = text("chrome_extension/background_login_v27.js")
    assert 'const PERSISTENT_POOL_KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__"' in login
    assert 'pool.ensureLoginSurface({' in login
    assert 'persistent-pool-bootstrap-adopted' in login
    assert 'chrome.windows.create' not in login
    assert 'chrome.windows.remove' not in login


def test_bootstrap_is_adopted_before_remaining_standby_windows_are_warmed() -> None:
    source = text("chrome_extension/conversation_persistent_pool_v132.js")
    bootstrap = source.index('if (!isDisabled && rows.length === 0)')
    early_return = source.index('return snapshotFrom(rows, value, auth, isDisabled);', bootstrap)
    warm = source.index('while (standbyRows(rows, value).length < target)', early_return)
    assert bootstrap < early_return < warm
    assert 'if (ready && !isDisabled && !lastError)' in source
