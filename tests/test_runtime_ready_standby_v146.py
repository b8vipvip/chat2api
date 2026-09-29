from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v146_standby_requires_current_worker_runtime() -> None:
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    assert "runtimeReadyTabs: new Map()" in pool
    assert "async function ensureRuntimeCurrent(tabId)" in pool
    assert "async function validateIdleRuntime" in pool
    assert "row.runtime_ready === true" in pool
    assert "function standbyRows(rows, value)" in pool
    assert "!assigned.has(row.window_id)" in pool
    assert "!busy.has(row.window_id)" in pool
    assert "runtime_ready_total: runtimeReadyRows.length" in pool
    assert "runtime_stale_total:" in pool
    assert "const STANDBY_SEMANTICS_REVISION = 152" in pool
    assert "standby_semantics_revision: STANDBY_SEMANTICS_REVISION" in pool
    assert "runtime_ready_standby: true" in pool


def test_v146_reused_route_is_preflighted_before_dispatch() -> None:
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    existing = pool.split("let existing = await routeLiveTab(route);", 1)[1].split("route.window_id = null;", 1)[0]
    assert "await ensureRuntimeCurrent(existing.id)" in existing
    assert "persistent-pool-existing-route-runtime-stale-v152" in existing
    assert "reuse-leased-route-v152" in existing
    assert "await closeWindow(staleWindowId)" in existing


def test_v146_request_admission_filters_stale_standby() -> None:
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    claim = pool.split("async function claimSlotForRequest(message)", 1)[1]
    assert 'validateIdleRuntime(value, physical, "request-admission-v152")' in claim
    assert "let rows = runtimeValidation.rows" in claim
    assert "let slot = standbyRows(rows, value)[0] || null" in claim
    assert 'scheduleReconcile("post-admission-standby-refill-v152", 250)' in claim


def test_v146_runtime_contract_and_capacity_telemetry_are_published() -> None:
    runtime = text("app/runtime_contract.py")
    capacity = text("chrome_extension/background_capacity_control_v35.js")
    assert '"persistent_window_runtime_preflight_v146": True' in runtime
    assert '"runtime_ready_standby_semantics_v146": True' in runtime
    assert '"persistent_window_pool_runtime_ready_revision": 146' in runtime
    assert "runtime-ready-standby-v146" in runtime
    assert "reserve_window_runtime_ready_total" in capacity
    assert "reserve_window_runtime_stale_total" in capacity
    assert "standby_semantics_revision || 146" in capacity
