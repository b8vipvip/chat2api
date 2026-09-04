from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ready_reserve_is_used_when_warm_slot_is_not_immediately_claimable() -> None:
    source = text("chrome_extension/background_window_affinity_v87.js")
    entry = text("chrome_extension/background_entry.js")
    assert '"background_window_affinity_v87.js"' in entry
    assert "async function immediateWarmClaimable" in source
    assert "async function claimReadyReserve" in source
    assert "if (await immediateWarmClaimable(message)) return null" in source
    assert 'conversation_reserve_fallback_reason: "warm-not-immediately-claimable"' in source
    assert 'route.last_rotation_reason = "reserve-fallback-v87"' in source
    assert "reserve.reserveSlots.delete(selectedKey)" in source


def test_successful_route_receives_hard_five_minute_idle_lease() -> None:
    source = text("chrome_extension/background_window_affinity_v87.js")
    assert "const IDLE_CLOSE_MS = 5 * 60 * 1000" in source
    assert "selected.route.close_after = now + IDLE_CLOSE_MS" in source
    assert 'action: "successful-route-protected-5m"' in source
    assert 'action: "blocked-early-success-route-close"' in source
    assert "chat2apiWorkerMasterDisabledV61" in source


def test_healthy_idle_spares_are_lease_refreshed_instead_of_periodically_rotated() -> None:
    source = text("chrome_extension/background_window_affinity_v87.js")
    boot = text("chrome_extension/background_standby_storage_lease_v87.js")
    entry = text("chrome_extension/background_entry.js")
    assert "LEASE_TOUCH_AGE_MS = 10 * 60 * 1000" in source
    assert "LEASE_REFRESH_INTERVAL_MS = 5 * 60 * 1000" in source
    assert "async function refreshHealthySpareLeases" in source
    assert source.count("slot.ready_at_ms = now") >= 2
    assert 'action: "healthy-spare-lease-refreshed"' in source
    assert "refreshHealthySpareLeases?.().catch?.(() => {})" in entry
    assert entry.index('"background_standby_storage_lease_v87.js"') < entry.index('"conversation_warm_pool_v2.js"')
    assert entry.index('"background_standby_storage_lease_v87.js"') < entry.index('"background_reserve_pool_v29.js"')
    assert 'const WARM_KEY = "chat2apiConversationWarmPoolV2"' in boot
    assert 'const RESERVE_KEY = "chat2apiReservePoolV29"' in boot
    assert "standby_lease_recovered_v87" in boot


def test_orphaned_stale_route_window_is_closed_before_replacement() -> None:
    source = text("chrome_extension/background_orphan_route_cleanup_v87.js")
    entry = text("chrome_extension/background_entry.js")
    assert entry.index('"background_window_affinity_v87.js"') < entry.index('"background_orphan_route_cleanup_v87.js"')
    assert "if (route.inflight_request_id) return false" in source
    assert 'route.last_rotation_reason = "orphan-route-cleanup-v87"' in source
    assert "await chrome.windows.remove(oldWindowId)" in source
    assert 'action: "orphan-route-cleaned"' in source
    assert "return baseResolver(message)" in source


def test_submit_path_remains_single_owner_and_keeps_v6_confirmed_fallback() -> None:
    source = text("chrome_extension/content_request_v6.js")
    manifest = json.loads(text("chrome_extension/manifest.json"))
    preflight = text("chrome_extension/background_runtime_preflight_v48.js")
    isolated = next(item for item in manifest["content_scripts"] if item.get("world") != "MAIN")
    scripts = isolated["js"]
    assert "content_submit_rescue_v87.js" not in scripts
    assert "content_submit_rescue_v87.js" not in preflight
    assert "ready.button.click()" in source
    assert "if (!confirmed && promptStillPresent(active))" in source
    assert "dispatchEnter(findComposer())" in source
    assert 'diagnostic(active, "enter-fallback"' in source
    assert 'waitAfterSend(active, "enter", 6000)' in source


def test_v87_runtime_preflight_uses_whole_path_wall_clock_budgets() -> None:
    source = text("chrome_extension/background_runtime_preflight_v48.js")
    assert "CONTRACT_TIMEOUT_MS = 700" in source
    assert "HOT_HEAL_BUDGET_MS = 2400" in source
    assert "RELOAD_BUDGET_MS = 3500" in source
    assert "FINAL_HEAL_BUDGET_MS = 1800" in source
    assert 'mode: "repair-budget-exhausted-v87"' in source
    assert 'error.code = "chatgpt_runtime_preflight_budget"' in source


def test_v87_javascript_assets_parse() -> None:
    for path in [
        "chrome_extension/background_standby_storage_lease_v87.js",
        "chrome_extension/background_window_affinity_v87.js",
        "chrome_extension/background_orphan_route_cleanup_v87.js",
        "chrome_extension/background_runtime_preflight_v48.js",
        "chrome_extension/content_request_v6.js",
        "app/admin_prompt_config_v75.js",
    ]:
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
