from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"missing patch anchor in {path}: {old[:120]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


pool = "chrome_extension/conversation_persistent_pool_v132.js"

replace_once(
    pool,
    "    reservations: new Map(),\n    reconcilePromise: null,",
    "    reservations: new Map(),\n    runtimeReadyTabs: new Map(),\n    runtimeChecks: 0,\n    runtimeFailures: 0,\n    reconcilePromise: null,",
)

replace_once(
    pool,
    "        routable: isChatGpt(url) && !isAuthSurface(url),\n        focused: win.focused === true,",
    "        routable: isChatGpt(url) && !isAuthSurface(url),\n        runtime_ready: state.runtimeReadyTabs.get(tab.id) === true,\n        focused: win.focused === true,",
)

replace_once(
    pool,
    "  async function waitForReady(tabId, timeoutMs = 30000) {",
    "  async function ensureRuntimeCurrent(tabId) {\n    const id = Number(tabId);\n    if (!Number.isInteger(id)) throw new Error(\"Persistent ChatGPT runtime preflight requires a valid tab id\");\n    state.runtimeChecks += 1;\n    try {\n      if (typeof ensureContent === \"function\") await ensureContent(id);\n      state.runtimeReadyTabs.set(id, true);\n      return true;\n    } catch (error) {\n      state.runtimeReadyTabs.set(id, false);\n      state.runtimeFailures += 1;\n      throw error;\n    }\n  }\n\n  async function waitForReady(tabId, timeoutMs = 30000) {",
)

replace_once(
    pool,
    "        if (typeof ensureContent === \"function\") await ensureContent(tabId);\n        return tab;",
    "        await ensureRuntimeCurrent(tabId);\n        return tab;",
)

replace_once(
    pool,
    "    return { window_id: created.id, tab_id: tab.id, url: tab.url || NEW_CHAT_URL, routable: true, status: tab.status || \"complete\" };",
    "    return { window_id: created.id, tab_id: tab.id, url: tab.url || NEW_CHAT_URL, routable: true, runtime_ready: true, status: tab.status || \"complete\" };",
)

replace_once(
    pool,
    "  function snapshotFrom(rows, value, ready, isDisabled) {",
    "  async function validateIdleRuntime(value, rows, reason = \"runtime-ready-standby-v146\") {\n    const preflight = globalThis.__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__;\n    if (!preflight || typeof ensureContent !== \"function\") {\n      return { rows, checked: 0, failed: 0, closed: 0, routesChanged: false, skipped: true };\n    }\n    const busy = busyWindowIds(value);\n    const candidates = rows.filter(row => row.routable && !busy.has(row.window_id) && row.runtime_ready !== true);\n    if (!candidates.length) return { rows, checked: 0, failed: 0, closed: 0, routesChanged: false, skipped: false };\n\n    const results = await Promise.all(candidates.map(async row => {\n      try {\n        await ensureRuntimeCurrent(row.tab_id);\n        return { row, ok: true, error: \"\" };\n      } catch (error) {\n        return { row, ok: false, error: String(error?.message || error) };\n      }\n    }));\n\n    let current = rows.slice();\n    let failed = 0;\n    let closed = 0;\n    let routesChanged = false;\n    const assigned = routeByWindow(value);\n    for (const result of results) {\n      const row = result.row;\n      if (result.ok) {\n        current = current.map(item => item.tab_id === row.tab_id ? { ...item, runtime_ready: true } : item);\n        continue;\n      }\n      failed += 1;\n      const entry = assigned.get(row.window_id);\n      if (entry?.route) {\n        await detachRoute(entry.route, `${reason}:runtime-preflight-failed`);\n        routesChanged = true;\n      }\n      if (await closeWindow(row.window_id)) {\n        current = current.filter(item => item.window_id !== row.window_id);\n        state.runtimeReadyTabs.delete(row.tab_id);\n        closed += 1;\n      } else {\n        current = current.map(item => item.window_id === row.window_id ? { ...item, runtime_ready: false } : item);\n      }\n    }\n    if (routesChanged) await persistRoutes(value);\n    return { rows: current, checked: candidates.length, failed, closed, routesChanged, skipped: false };\n  }\n\n  function snapshotFrom(rows, value, ready, isDisabled) {",
)

replace_once(
    pool,
    "    const target = normalizeTarget(state.target) || 0;\n    const effective = target ? (isDisabled ? Math.min(1, target) : (ready ? target + rows.filter(row => row.routable && busyWindowIds(value).has(row.window_id)).length : Math.min(1, target))) : 0;\n    const assigned = routeByWindow(value);\n    const busy = busyWindowIds(value);\n    const routableRows = rows.filter(row => row.routable);\n    const unroutableRows = rows.filter(row => !row.routable);\n    const routedRows = routableRows.filter(row => assigned.has(row.window_id));\n    // Backup capacity is every routable idle window, including an idle slot\n    // that still retains logical route affinity and can be reused/reassigned.\n    const standbyRows = routableRows.filter(row => !busy.has(row.window_id));\n    const inUse = routableRows.filter(row => busy.has(row.window_id)).length;",
    "    const target = normalizeTarget(state.target) || 0;\n    const assigned = routeByWindow(value);\n    const busy = busyWindowIds(value);\n    const routableRows = rows.filter(row => row.routable);\n    const unroutableRows = rows.filter(row => !row.routable);\n    const runtimeReadyRows = routableRows.filter(row => row.runtime_ready === true);\n    const routedRows = routableRows.filter(row => assigned.has(row.window_id));\n    const inUse = routableRows.filter(row => busy.has(row.window_id)).length;\n    const effective = target ? (isDisabled ? Math.min(1, target) : (ready ? target + inUse : Math.min(1, target))) : 0;\n    // v146 backup capacity requires both a routable ChatGPT surface and a\n    // verified current Worker runtime. URL-only tabs never satisfy standby.\n    const standbyRows = runtimeReadyRows.filter(row => !busy.has(row.window_id));",
)

replace_once(
    pool,
    "      routable_total: routableRows.length,\n      unroutable_total: unroutableRows.length,",
    "      routable_total: routableRows.length,\n      unroutable_total: unroutableRows.length,\n      runtime_ready_total: runtimeReadyRows.length,\n      runtime_stale_total: routableRows.filter(row => row.runtime_ready !== true).length,",
)

replace_once(
    pool,
    "      standby_semantics_revision: 145,",
    "      standby_semantics_revision: 146,\n      runtime_ready_standby: true,\n      runtime_preflight_checks: state.runtimeChecks,\n      runtime_preflight_failures: state.runtimeFailures,",
)

replace_once(
    pool,
    "      excess: Math.max(0, routableRows.length - effective),",
    "      excess: Math.max(0, runtimeReadyRows.length - effective),",
)

replace_once(
    pool,
    "    let excess = Math.max(0, current.filter(row => row.routable).length - target);",
    "    const capacityRows = current.filter(row => row.routable && (busy.has(row.window_id) || row.runtime_ready === true));\n    let excess = Math.max(0, capacityRows.length - target);",
)

replace_once(
    pool,
    "      .filter(row => row.routable && !assigned.has(row.window_id) && !busy.has(row.window_id))",
    "      .filter(row => row.routable && row.runtime_ready === true && !assigned.has(row.window_id) && !busy.has(row.window_id))",
)

replace_once(
    pool,
    "    if (!target) {\n      state.lastResult = { ok: true, reason, target: null, action: \"unconfigured\", at: Date.now() };\n      return snapshotFrom(rows, value, ready, isDisabled);\n    }\n\n    const busyBefore = busyWindowIds(value);",
    "    let runtimeValidation = { checked: 0, failed: 0, closed: 0, skipped: true };\n    if (ready && !isDisabled) {\n      runtimeValidation = await validateIdleRuntime(value, rows, reason);\n      rows = runtimeValidation.rows;\n    }\n\n    if (!target) {\n      state.lastResult = { ok: true, reason, target: null, action: \"unconfigured\", runtime_validation: runtimeValidation, at: Date.now() };\n      return snapshotFrom(rows, value, ready, isDisabled);\n    }\n\n    const busyBefore = busyWindowIds(value);",
)

replace_once(
    pool,
    "      while (rows.filter(row => row.routable).length < effectiveTarget) {",
    "      while (rows.filter(row => row.routable && (busyWindowIds(value).has(row.window_id) || row.runtime_ready === true)).length < effectiveTarget) {",
)

replace_once(
    pool,
    "      routable_before_or_after_total: rows.filter(row => row.routable).length,\n      standby_before_or_after_total: rows.filter(row => row.routable && !busyWindowIds(value).has(row.window_id)).length,\n      standby_semantics_revision: 145,",
    "      routable_before_or_after_total: rows.filter(row => row.routable).length,\n      runtime_ready_before_or_after_total: rows.filter(row => row.routable && row.runtime_ready === true).length,\n      standby_before_or_after_total: rows.filter(row => row.routable && row.runtime_ready === true && !busyWindowIds(value).has(row.window_id)).length,\n      standby_semantics_revision: 146,\n      runtime_validation: runtimeValidation,",
)

replace_once(
    pool,
    "      const existing = await routeLiveTab(route);\n      if (existing) {\n        await markPooledRoute(route);\n        route.last_active_at = Date.now();\n        state.reservations.set(key, existing.windowId);\n        await persistRoutes(value);\n        state.reused += 1;\n        return { key, window_id: existing.windowId, tab_id: existing.id, strategy: \"reuse-persistent-route\" };\n      }",
    "      let existing = await routeLiveTab(route);\n      if (existing) {\n        try {\n          if (existing.status && existing.status !== \"complete\") existing = await waitForReady(existing.id, 12000);\n          else { await ensureRuntimeCurrent(existing.id); existing = await chrome.tabs.get(existing.id); }\n        } catch (_) {\n          await detachRoute(route, \"persistent-pool-existing-route-runtime-stale-v146\");\n          await closeWindow(existing.windowId);\n          state.runtimeReadyTabs.delete(existing.id);\n          existing = null;\n        }\n      }\n      if (existing) {\n        await markPooledRoute(route);\n        route.last_active_at = Date.now();\n        state.reservations.set(key, existing.windowId);\n        await persistRoutes(value);\n        state.reused += 1;\n        return { key, window_id: existing.windowId, tab_id: existing.id, strategy: \"reuse-runtime-ready-persistent-route-v146\" };\n      }",
)

replace_once(
    pool,
    "      let rows = (await physicalWindows()).filter(row => row.routable);\n      let assigned = routeByWindow(value);",
    "      let physical = await physicalWindows();\n      const runtimeValidation = await validateIdleRuntime(value, physical, \"request-admission-v146\");\n      let rows = runtimeValidation.rows.filter(row => row.routable && row.runtime_ready === true);\n      let assigned = routeByWindow(value);",
)

replace_once(
    pool,
    "      if (!slot && rows.length < target) {",
    "      if (!slot && rows.filter(row => !busy.has(row.window_id)).length < target) {",
)

replace_once(
    pool,
    "  chrome.tabs.onUpdated.addListener((_tabId, changeInfo) => {\n    if (changeInfo.url || changeInfo.status === \"complete\") scheduleReconcile(\"tab-updated\", 700);\n  });",
    "  chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {\n    if (changeInfo.url || changeInfo.status) state.runtimeReadyTabs.delete(Number(tabId));\n    if (changeInfo.url || changeInfo.status === \"complete\") scheduleReconcile(\"tab-updated\", 700);\n  });\n  chrome.tabs.onRemoved.addListener(tabId => state.runtimeReadyTabs.delete(Number(tabId)));",
)

capacity = "chrome_extension/background_capacity_control_v35.js"
replace_once(
    capacity,
    "      reserve_window_routable_total: Number(snapshot?.routable_total || 0),\n      reserve_window_unroutable_total: Number(snapshot?.unroutable_total || 0),\n      reserve_window_standby_semantics_revision: Number(snapshot?.standby_semantics_revision || 145),",
    "      reserve_window_routable_total: Number(snapshot?.routable_total || 0),\n      reserve_window_unroutable_total: Number(snapshot?.unroutable_total || 0),\n      reserve_window_runtime_ready_total: Number(snapshot?.runtime_ready_total || 0),\n      reserve_window_runtime_stale_total: Number(snapshot?.runtime_stale_total || 0),\n      reserve_window_standby_semantics_revision: Number(snapshot?.standby_semantics_revision || 146),",
)

runtime = "app/runtime_contract.py"
replace_once(
    runtime,
    "-worker-runtime-preflight-repair-v145-routable-standby-v145-release-v02298\"",
    "-worker-runtime-preflight-repair-v145-routable-standby-v145-release-v02298-runtime-ready-standby-v146\"",
)
replace_once(
    runtime,
    "-worker-runtime-preflight-repair-v145-routable-standby-v145-release-v0844\"",
    "-worker-runtime-preflight-repair-v145-routable-standby-v145-release-v0844-runtime-ready-standby-v146\"",
)
replace_once(
    runtime,
    "            \"persistent_window_pool_contract_revision\": 135,\n            \"route_close_terminal_revision\": 91,",
    "            \"persistent_window_pool_contract_revision\": 135,\n            \"persistent_window_pool_runtime_ready_revision\": 146,\n            \"route_close_terminal_revision\": 91,",
)
replace_once(
    runtime,
    "            \"worker_runtime_preflight_repair_v145\": True,\n            \"routable_standby_semantics_v145\": True,",
    "            \"worker_runtime_preflight_repair_v145\": True,\n            \"routable_standby_semantics_v145\": True,\n            \"persistent_window_runtime_preflight_v146\": True,\n            \"runtime_ready_standby_semantics_v146\": True,",
)

persistent_test = "tests/test_persistent_window_pool_v132.py"
replace_once(persistent_test, "    assert 'const standbyRows = routableRows.filter(row => !busy.has(row.window_id));' in source", "    assert 'const standbyRows = runtimeReadyRows.filter(row => !busy.has(row.window_id));' in source")
replace_once(persistent_test, "    assert 'standby_semantics_revision: 145' in source", "    assert 'standby_semantics_revision: 146' in source")
replace_once(persistent_test, "    assert 'while (rows.filter(row => row.routable).length < effectiveTarget)' in source", "    assert 'row.runtime_ready === true' in source\n    assert 'validateIdleRuntime' in source\n    assert 'while (rows.filter(row => row.routable && (busyWindowIds(value).has(row.window_id) || row.runtime_ready === true)).length < effectiveTarget)' in source")
replace_once(persistent_test, "    assert 'Math.max(0, current.filter(row => row.routable).length - target)' in source", "    assert 'Math.max(0, capacityRows.length - target)' in source")
replace_once(persistent_test, "    assert 'unroutable_total: unroutableRows.length' in source", "    assert 'unroutable_total: unroutableRows.length' in source\n    assert 'runtime_ready_total: runtimeReadyRows.length' in source\n    assert 'runtime_stale_total:' in source")

release_test = "tests/test_worker_runtime_standby_release_v02298.py"
replace_once(release_test, "    assert \"const standbyRows = routableRows.filter(row => !busy.has(row.window_id));\" in pool\n    assert \"standby_semantics_revision: 145\" in pool\n    assert \"while (rows.filter(row => row.routable).length < effectiveTarget)\" in pool", "    assert \"const standbyRows = runtimeReadyRows.filter(row => !busy.has(row.window_id));\" in pool\n    assert \"standby_semantics_revision: 146\" in pool\n    assert \"runtime_ready_standby: true\" in pool\n    assert \"validateIdleRuntime\" in pool")

capacity_vm = "tests/capacity_control_v35.mjs"
replace_once(capacity_vm, "      unroutable_total: 0,\n      active,", "      unroutable_total: 0,\n      runtime_ready_total: activeRows.length,\n      runtime_stale_total: 0,\n      active,")
replace_once(capacity_vm, "      standby_semantics_revision: 145,", "      standby_semantics_revision: 146,")
replace_once(capacity_vm, "assert.equal(result.metadata.reserve_window_unroutable_total, 0);\nassert.equal(result.metadata.reserve_window_standby_semantics_revision, 145);", "assert.equal(result.metadata.reserve_window_unroutable_total, 0);\nassert.equal(result.metadata.reserve_window_runtime_ready_total, 3);\nassert.equal(result.metadata.reserve_window_runtime_stale_total, 0);\nassert.equal(result.metadata.reserve_window_standby_semantics_revision, 146);")

new_test = ROOT / "tests/test_runtime_ready_standby_v146.py"
new_test.write_text('''from __future__ import annotations\n\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef text(path: str) -> str:\n    return (ROOT / path).read_text(encoding="utf-8")\n\n\ndef test_v146_standby_requires_current_worker_runtime() -> None:\n    pool = text("chrome_extension/conversation_persistent_pool_v132.js")\n    assert "runtimeReadyTabs: new Map()" in pool\n    assert "async function ensureRuntimeCurrent(tabId)" in pool\n    assert "async function validateIdleRuntime" in pool\n    assert "row.runtime_ready === true" in pool\n    assert "const standbyRows = runtimeReadyRows.filter(row => !busy.has(row.window_id));" in pool\n    assert "runtime_ready_total: runtimeReadyRows.length" in pool\n    assert "runtime_stale_total:" in pool\n    assert "standby_semantics_revision: 146" in pool\n    assert "runtime_ready_standby: true" in pool\n\n\ndef test_v146_reused_route_is_preflighted_before_dispatch() -> None:\n    pool = text("chrome_extension/conversation_persistent_pool_v132.js")\n    existing = pool.split("let existing = await routeLiveTab(route);", 1)[1].split("if (Number.isInteger(route.window_id))", 1)[0]\n    assert "await ensureRuntimeCurrent(existing.id)" in existing\n    assert "persistent-pool-existing-route-runtime-stale-v146" in existing\n    assert "reuse-runtime-ready-persistent-route-v146" in existing\n    assert "await closeWindow(existing.windowId)" in existing\n\n\ndef test_v146_request_admission_filters_stale_standby() -> None:\n    pool = text("chrome_extension/conversation_persistent_pool_v132.js")\n    claim = pool.split("async function claimSlotForRequest(message)", 1)[1]\n    assert 'validateIdleRuntime(value, physical, "request-admission-v146")' in claim\n    assert "runtimeValidation.rows.filter(row => row.routable && row.runtime_ready === true)" in claim\n    assert "rows.filter(row => !busy.has(row.window_id)).length < target" in claim\n\n\ndef test_v146_runtime_contract_and_capacity_telemetry_are_published() -> None:\n    runtime = text("app/runtime_contract.py")\n    capacity = text("chrome_extension/background_capacity_control_v35.js")\n    assert '"persistent_window_runtime_preflight_v146": True' in runtime\n    assert '"runtime_ready_standby_semantics_v146": True' in runtime\n    assert '"persistent_window_pool_runtime_ready_revision": 146' in runtime\n    assert "runtime-ready-standby-v146" in runtime\n    assert "reserve_window_runtime_ready_total" in capacity\n    assert "reserve_window_runtime_stale_total" in capacity\n    assert "standby_semantics_revision || 146" in capacity\n''', encoding="utf-8")

print("v146 runtime-ready standby patch applied")
