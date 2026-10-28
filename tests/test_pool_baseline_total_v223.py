"""Physical standby is one canonical routable state; N is normal TOTAL count."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_window_policy_busy_zero_one_two_three_then_release():
    result = subprocess.run(
        ["node", str(ROOT / "tests/pool_baseline_total_v223.mjs")],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_worker_admission_is_not_a_second_fixed_n_window_cap():
    server = source("app/capacity_scheduler_v58.py")
    assert 'def worker_baseline_total(client_id: str) -> int:' in server
    assert 'def worker_limit(client_id: str) -> int:' in server
    assert 'worker_pool_ready_window_ids' in server
    assert 'pool_window_claims' in server
    assert 'held.pop(str(request_id), None)' in server
    assert 'return min(MAX_LIMIT, used + evidence["unclaimed_ready_windows"])' in server
    assert '"baseline_total_window_target"' in server
    assert 'broker.create = create' in server
    assert 'broker.capacity_snapshot = capacity_snapshot' in server
    assert 'asyncio.wait_for(condition.wait(), timeout=1.0)' in server


def test_standby_ready_cannot_be_displayed_as_unavailable():
    ui = source("app/admin_window_manager_v88.js")
    server = source("app/window_manager_v88_patch.py")
    assert 'if (value === "ready") return "可接待";' in ui
    assert "待机（调度容量已满）" not in ui
    assert "server_admission_available" not in server
    assert '"scheduler_baseline_total"' in server
    assert '常态窗口总数' in ui
    assert '5秒关闭' in ui


def test_window_router_alone_closes_completed_tabs():
    routing = source("chrome_extension/conversation_routing.js")
    manager = source("chrome_extension/background_window_manager_v88.js")
    pool = source("chrome_extension/conversation_persistent_pool_v132.js")
    assert "const IDLE_CLOSE_MS = 5 * 1000;" in routing
    assert "const SUCCESS_LEASE_MS = 5 * 1000;" in manager
    assert "route_idle_lease_ms: 5 * 1000" in pool
    assert 'if (busyWindowIds(router).has(Number(windowId))' in pool
    assert 'routeByWindow(router).has(Number(windowId))' in pool
    assert "max(0, requiredTotal - routedCount)" in pool
    assert "await shrinkStandby(value, rows, standbyTarget, reason)" in pool
