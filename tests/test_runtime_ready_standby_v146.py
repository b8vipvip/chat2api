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
    assert "const standbyRows = runtimeReadyRows.filter(row => !busy.has(row.window_id));" in pool
    assert "runtime_ready_total: runtimeReadyRows.length" in pool
    assert "runtime_stale_total:" in pool
    assert "standby_semantics_revision: 146" in pool
    assert "runtime_ready_standby: true" in pool


def test_v146_reused_route_is_preflighted_before_dispatch() -> None:
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    existing = pool.split("let existing = await routeLiveTab(route);", 1)[1].split("if (Number.isInteger(route.window_id))", 1)[0]
    assert "await ensureRuntimeCurrent(existing.id)" in existing
    assert "persistent-pool-existing-route-runtime-stale-v146" in existing
    assert "reuse-runtime-ready-persistent-route-v146" in existing
    assert "await closeWindow(existing.windowId)" in existing


def test_v146_request_admission_filters_stale_standby() -> None:
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    claim = pool.split("async function claimSlotForRequest(message)", 1)[1]
    assert 'validateIdleRuntime(value, physical, "request-admission-v146")' in claim
    assert "runtimeValidation.rows.filter(row => row.routable && row.runtime_ready === true)" in claim
    assert "rows.filter(row => !busy.has(row.window_id)).length < target" in claim


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
