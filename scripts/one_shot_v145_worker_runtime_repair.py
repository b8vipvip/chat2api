from __future__ import annotations

from pathlib import Path
import re


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    source = p.read_text(encoding="utf-8")
    if source.count(old) < count:
        raise SystemExit(f"{path}: missing replacement anchor: {old[:120]!r}")
    p.write_text(source.replace(old, new, count), encoding="utf-8")


def sub(path: str, pattern: str, repl: str, count: int = 1) -> None:
    p = Path(path)
    source = p.read_text(encoding="utf-8")
    output, changed = re.subn(pattern, repl, source, count=count, flags=re.S)
    if changed != count:
        raise SystemExit(f"{path}: regex replacement expected {count}, got {changed}: {pattern[:120]!r}")
    p.write_text(output, encoding="utf-8")


def patch_persistent_pool() -> None:
    path = "chrome_extension/conversation_persistent_pool_v132.js"
    replace(
        path,
        "    const effective = target ? (isDisabled ? Math.min(1, target) : (ready ? target + inUse : Math.min(1, target))) : 0;",
        "    const effective = target ? (isDisabled ? Math.min(1, target) : (ready ? target + rows.filter(row => row.routable && busyWindowIds(value).has(row.window_id)).length : Math.min(1, target))) : 0;",
    )
    replace(
        path,
        "    const routedRows = rows.filter(row => assigned.has(row.window_id));",
        "    const routableRows = rows.filter(row => row.routable);\n"
        "    const unroutableRows = rows.filter(row => !row.routable);\n"
        "    const routedRows = routableRows.filter(row => assigned.has(row.window_id));",
    )
    replace(
        path,
        "    const standbyRows = rows.filter(row => !assigned.has(row.window_id));",
        "    // Backup capacity is every routable idle window, including an idle slot\n"
        "    // that still retains logical route affinity and can be reused/reassigned.\n"
        "    const standbyRows = routableRows.filter(row => !busy.has(row.window_id));",
    )
    replace(
        path,
        "    const inUse = rows.filter(row => busy.has(row.window_id)).length;",
        "    const inUse = routableRows.filter(row => busy.has(row.window_id)).length;",
    )
    replace(
        path,
        "      active: inUse,\n      idle: Math.max(0, rows.length - inUse),",
        "      routable_total: routableRows.length,\n"
        "      unroutable_total: unroutableRows.length,\n"
        "      active: inUse,\n"
        "      idle: standbyRows.length,",
    )
    replace(
        path,
        "      all_chatgpt_windows: rows.length,\n      login_ready: ready,",
        "      all_chatgpt_windows: rows.length,\n"
        "      standby_semantics_revision: 145,\n"
        "      login_ready: ready,",
    )
    replace(
        path,
        "      excess: Math.max(0, rows.length - effective),",
        "      excess: Math.max(0, routableRows.length - effective),",
    )
    replace(
        path,
        "    let excess = Math.max(0, current.length - target);",
        "    let excess = Math.max(0, current.filter(row => row.routable).length - target);",
    )
    replace(
        path,
        ".filter(row => !assigned.has(row.window_id) && !busy.has(row.window_id))",
        ".filter(row => row.routable && !assigned.has(row.window_id) && !busy.has(row.window_id))",
    )
    replace(
        path,
        "    const effectiveTarget = isDisabled ? Math.min(1, target) : (ready ? target + rows.filter(row => busyBefore.has(row.window_id)).length : Math.min(1, target));",
        "    const busyRoutableBefore = rows.filter(row => row.routable && busyBefore.has(row.window_id)).length;\n"
        "    const effectiveTarget = isDisabled ? Math.min(1, target) : (ready ? target + busyRoutableBefore : Math.min(1, target));",
    )
    replace(
        path,
        "      while (rows.length < effectiveTarget) {",
        "      while (rows.filter(row => row.routable).length < effectiveTarget) {",
    )
    replace(
        path,
        "      before_or_after_total: rows.length,\n      opened,",
        "      before_or_after_total: rows.length,\n"
        "      routable_before_or_after_total: rows.filter(row => row.routable).length,\n"
        "      standby_before_or_after_total: rows.filter(row => row.routable && !busyWindowIds(value).has(row.window_id)).length,\n"
        "      standby_semantics_revision: 145,\n"
        "      opened,",
    )


def patch_capacity_control() -> None:
    path = "chrome_extension/background_capacity_control_v35.js"
    old = (
        '    const reached = snapshot.login_ready === true && snapshot.worker_disabled !== true && Number(snapshot.total || 0) === target;\n'
        '    let pendingReason = "";\n'
        '    if (snapshot.worker_disabled === true) pendingReason = "worker_disabled";\n'
        '    else if (snapshot.login_ready !== true) pendingReason = "login_not_ready";\n'
        '    else if (Number(snapshot.total || 0) < target) pendingReason = "warming";\n'
        '    else if (Number(snapshot.total || 0) > target) pendingReason = "busy_windows_protected";'
    )
    new = (
        '    const standby = Number(snapshot.idle || snapshot.standby || 0);\n'
        '    const reached = snapshot.login_ready === true && snapshot.worker_disabled !== true && standby === target;\n'
        '    let pendingReason = "";\n'
        '    if (snapshot.worker_disabled === true) pendingReason = "worker_disabled";\n'
        '    else if (snapshot.login_ready !== true) pendingReason = "login_not_ready";\n'
        '    else if (standby < target) pendingReason = "warming";\n'
        '    else if (standby > target) pendingReason = "excess_standby";'
    )
    replace(path, old, new)
    replace(
        path,
        "      reserve_window_target: Number(snapshot?.target || 0),",
        "      reserve_window_target: Number(snapshot?.target || 0),\n"
        "      reserve_window_routable_total: Number(snapshot?.routable_total || 0),\n"
        "      reserve_window_unroutable_total: Number(snapshot?.unroutable_total || 0),\n"
        "      reserve_window_standby_semantics_revision: Number(snapshot?.standby_semantics_revision || 145),",
    )


def patch_runtime_repair() -> None:
    bootstrap = "chrome_extension/content_bootstrap.js"
    replace(
        bootstrap,
        'files: ["network_stream_main_v55.js", "native_tool_stream_main_v63.js", "multimodal_main_v78.js"]',
        'files: ["network_stream_main_v55.js", "model_evidence_main_v144.js", "native_tool_stream_main_v63.js", "multimodal_main_v78.js"]',
    )
    replace(
        bootstrap,
        '          "content_page_adapter_v22.js",',
        '          "content_page_adapter_v22.js",\n          "content_model_evidence_v144.js",',
    )

    evidence_main = "chrome_extension/model_evidence_main_v144.js"
    replace(
        evidence_main,
        "  globalThis[KEY] = state;\n\n  const nativeFetch",
        '  globalThis[KEY] = state;\n'
        '  try { document.documentElement?.setAttribute?.("data-chat2api-model-evidence-main-v144", "144"); } catch (_) {}\n\n'
        "  const nativeFetch",
    )

    contract = "chrome_extension/content_runtime_contract_v71.js"
    replace(
        contract,
        "    const rich = globalThis.__CHAT2API_RICH_RESPONSE_V69__ || null;\n    const choicePolicy",
        "    const rich = globalThis.__CHAT2API_RICH_RESPONSE_V69__ || null;\n"
        "    const modelEvidence = globalThis.__CHAT2API_MODEL_EVIDENCE_V144__ || null;\n"
        "    const choicePolicy",
    )
    replace(
        contract,
        "      rich_response_v69: Boolean(rich),\n      chatgpt_choice_policy_v143:",
        "      rich_response_v69: Boolean(rich),\n"
        "      model_evidence_v144: Number(modelEvidence?.version || 0) >= 144,\n"
        '      model_evidence_main_v144: document.documentElement?.getAttribute?.("data-chat2api-model-evidence-main-v144") === "144",\n'
        "      chatgpt_choice_policy_v143:",
    )

    preflight = "chrome_extension/background_runtime_preflight_v48.js"
    replace(
        preflight,
        '  const MAIN_FILES = ["network_stream_main_v55.js", "multimodal_main_v78.js"];\n'
        '  const NATIVE_MAIN_FILES = ["native_tool_stream_main_v63.js"];\n'
        '  const CURRENT_MAIN_FILES = [MAIN_FILES[0], ...NATIVE_MAIN_FILES, MAIN_FILES[1]];',
        '  const MAIN_FILES = ["network_stream_main_v55.js", "model_evidence_main_v144.js", "multimodal_main_v78.js"];\n'
        '  const NATIVE_MAIN_FILES = ["native_tool_stream_main_v63.js"];\n'
        '  const CURRENT_MAIN_FILES = [MAIN_FILES[0], MAIN_FILES[1], ...NATIVE_MAIN_FILES, MAIN_FILES[2]];',
    )
    replace(
        preflight,
        '    "content_ui_hygiene_v31.js", "content_rate_limit_guard_v52.js", "content_tool_isolation_v48.js",',
        '    "content_model_evidence_v144.js", "content_ui_hygiene_v31.js", "content_rate_limit_guard_v52.js", "content_tool_isolation_v48.js",',
    )
    replace(
        preflight,
        "      result?.modules?.rich_response_v69 && result?.modules?.network_stream_recovery_v55 &&",
        "      result?.modules?.rich_response_v69 && result?.modules?.model_evidence_v144 && result?.modules?.model_evidence_main_v144 &&\n"
        "      result?.modules?.network_stream_recovery_v55 &&",
    )
    sub(
        preflight,
        r"  async function waitForReloadOrContract\(tabId, timeoutMs = RELOAD_BUDGET_MS\) \{.*?\n  \}\n(?=  async function recordLast)",
        """  async function waitForReloadOrContract(tabId, timeoutMs = RELOAD_BUDGET_MS) {
    const started = Date.now(); let last = null; let complete = false;
    // A just-issued reload may still expose the outgoing document as \"complete\".
    // Never return early on that state; keep polling the v71 contract until the
    // replacement document actually reports the required runtime epoch.
    while (Date.now() - started < timeoutMs) {
      const remaining = Math.max(100, timeoutMs - (Date.now() - started));
      last = await contract(tabId, Math.min(CONTRACT_TIMEOUT_MS, remaining));
      if (current(last)) return {ready: true, complete: true, result: last};
      let tab = null;
      try { tab = await chrome.tabs.get(tabId); } catch (_) { return {ready: false, complete: false, result: last}; }
      complete = tab?.status === \"complete\" && /^https:\\/\\/(?:www\\.)?(?:chatgpt\\.com|chat\\.openai\\.com)\\//i.test(String(tab.url || \"\"));
      await sleep(80);
    }
    return {ready: false, complete, result: last};
  }
""",
    )
    replace(
        preflight,
        "  async function recordLast(last) {",
        "  function missingModules(result) {\n"
        '    const modules = result?.modules && typeof result.modules === "object" ? result.modules : {};\n'
        "    return Object.entries(modules).filter(([, ok]) => !ok).map(([name]) => name);\n"
        "  }\n"
        "  async function recordLast(last) {",
    )
    sub(
        preflight,
        r'      await recordLast\(\{tab_id: tabId, ok: false, mode: "repair-budget-exhausted-v87".*?error\.code = "chatgpt_runtime_preflight_budget";',
        """      const missing = missingModules(result);
      await recordLast({tab_id: tabId, ok: false, mode: \"repair-budget-exhausted-v145\", response_terminal_owner: \"request-v6\",
        reloaded, hot_healed: hotHealed, result, missing_modules: missing, elapsed_ms: Date.now()-started,
        budget_ms: CONTRACT_TIMEOUT_MS + HOT_HEAL_BUDGET_MS + RELOAD_BUDGET_MS + FINAL_HEAL_BUDGET_MS, at_ms: Date.now()});
      const marker = result?.marker ? `${String(result.marker.bundle || \"?\")}/${Number(result.marker.revision || 0)}` : \"missing\";
      const error = new Error(`ChatGPT tab Worker runtime is stale or incomplete after the bounded preflight budget; required bundle ${REQUIRED_BUNDLE} content revision ${REQUIRED_REVISION} native-tool-stream revision 63 multimodal revision 85 terminal/prompt revision 88 conversation-quota-failover revision 95 UI-hygiene revision 101 model-evidence revision 144; response terminal owner request-v6; observed marker ${marker}; missing modules ${missing.join(\",\") || \"unknown\"}`);
      error.code = \"chatgpt_runtime_preflight_budget\";""",
    )


def patch_tests() -> None:
    pool_test = Path("tests/test_persistent_window_pool_v132.py")
    source = pool_test.read_text(encoding="utf-8")
    source = source.replace(
        "assert 'while (rows.length < effectiveTarget)' in source",
        "assert 'while (rows.filter(row => row.routable).length < effectiveTarget)' in source",
    )
    source = source.replace(
        "assert 'target + rows.filter(row => busyBefore.has(row.window_id)).length' in source",
        "assert 'target + busyRoutableBefore' in source",
    )
    source = source.replace(
        "assert 'Math.max(0, current.length - target)' in source",
        "assert 'Math.max(0, current.filter(row => row.routable).length - target)' in source",
    )
    anchor = "    assert 'standbyRows.length === target' in source\n"
    if anchor not in source:
        raise SystemExit("pool test standby anchor missing")
    source = source.replace(
        anchor,
        anchor
        + "    assert 'const standbyRows = routableRows.filter(row => !busy.has(row.window_id));' in source\n"
        + "    assert 'standby_semantics_revision: 145' in source\n",
        1,
    )
    source = source.replace(
        "    assert 'all_chatgpt_windows: rows.length' in source\n",
        "    assert 'routable_total: routableRows.length' in source\n"
        "    assert 'unroutable_total: unroutableRows.length' in source\n"
        "    assert 'all_chatgpt_windows: rows.length' in source\n",
        1,
    )
    capacity_anchor = '    assert \'persistent-prewarmed-total-window-pool-v132\' in source\n'
    if capacity_anchor not in source:
        raise SystemExit("capacity test anchor missing")
    source = source.replace(
        capacity_anchor,
        capacity_anchor
        + "    assert 'const standby = Number(snapshot.idle || snapshot.standby || 0);' in source\n"
        + "    assert 'standby === target' in source\n",
        1,
    )
    pool_test.write_text(source, encoding="utf-8")

    preflight_test = "tests/runtime_preflight_refresh_v71.mjs"
    replace(
        preflight_test,
        "      rich_response_v69: true,\n      network_stream_recovery_v55: true,",
        "      rich_response_v69: true,\n"
        "      model_evidence_v144: true,\n"
        "      model_evidence_main_v144: true,\n"
        "      network_stream_recovery_v55: true,",
    )
    replace(preflight_test, "  const worlds = [];", "  const worlds = [];\n  const injectedFiles = [];")
    replace(
        preflight_test,
        '        worlds.push(options?.world || "ISOLATED");\n        return [];',
        '        worlds.push(options?.world || "ISOLATED");\n        injectedFiles.push(...(options?.files || []));\n        return [];',
    )
    replace(
        preflight_test,
        "  return { injected, reloads, baseEnsures, saved, worlds, state: context.__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__ };",
        "  return { injected, reloads, baseEnsures, saved, worlds, injectedFiles, state: context.__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__ };",
    )
    replace(
        preflight_test,
        'assert.equal(hot.worlds[0], "MAIN");\nassert.equal(hot.state.last?.ok, true);',
        'assert.equal(hot.worlds[0], "MAIN");\n'
        'assert.ok(hot.injectedFiles.includes("model_evidence_main_v144.js"));\n'
        'assert.ok(hot.injectedFiles.includes("content_model_evidence_v144.js"));\n'
        "assert.equal(hot.state.last?.ok, true);",
    )

    model_test = "tests/test_model_evidence_v144.py"
    replace(
        model_test,
        '    assert "content_model_evidence_v144.js" in isolated_scripts\n',
        '    assert "content_model_evidence_v144.js" in isolated_scripts\n\n'
        '    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")\n'
        '    preflight = (ROOT / "chrome_extension" / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")\n'
        '    contract = (ROOT / "chrome_extension" / "content_runtime_contract_v71.js").read_text(encoding="utf-8")\n'
        '    assert "model_evidence_main_v144.js" in bootstrap\n'
        '    assert "content_model_evidence_v144.js" in bootstrap\n'
        '    assert "model_evidence_main_v144.js" in preflight\n'
        '    assert "content_model_evidence_v144.js" in preflight\n'
        '    assert "model_evidence_v144" in contract\n'
        '    assert "model_evidence_main_v144" in contract\n',
    )


def main() -> None:
    patch_persistent_pool()
    patch_capacity_control()
    patch_runtime_repair()
    patch_tests()
    print("v145 Worker runtime + standby repair applied")


if __name__ == "__main__":
    main()
