(() => {
  const KEY = "__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__";
  if (globalThis[KEY]) return;

  // Worker bundle 0.8.27 keeps the v71 request/response epoch while requiring
  // the v78 MAIN-world upload bridge, v85 safe-submit gate and v88 terminal/prompt guard.
  const REQUIRED_BUNDLE = "0.8.27";
  const REQUIRED_REVISION = 71;
  const CONTRACT_TIMEOUT_MS = 700;
  const HOT_HEAL_BUDGET_MS = 2400;
  const RELOAD_BUDGET_MS = 3500;
  const FINAL_HEAL_BUDGET_MS = 1800;
  const MAIN_FILES = ["network_stream_main_v55.js", "multimodal_main_v78.js"];
  const OVERLAY_FILES = [
    "content_rate_limit_guard_v52.js",
    "content_tool_isolation_v48.js",
    "content_multimodal_v78.js",
    "content_multimodal_settle_v84.js",
    "content_multimodal_settle_v85.js",
    "content_request_lifecycle_v50.js",
    "content_request_hygiene_v42.js",
    "content_draft_managed_recovery_v55.js",
    "content_rich_response_v69.js",
    "content_request_v6.js",
    "content_response_stream_recovery_v69.js",
    "content_network_stream_recovery_v55.js",
    "content_request_terminal_prompt_v88.js",
    "content_response_semantic_recovery_v51.js",
    "content_transient_retry_v50.js",
    "content_generation_liveness_v49.js",
    "content_bundle_marker_v71.js",
    "content_runtime_contract_v48.js",
    "content_runtime_contract_v71.js",
  ];
  const inflight = new Map();
  const state = {
    version: 71,
    revision: 88,
    required_bundle: REQUIRED_BUNDLE,
    required_revision: REQUIRED_REVISION,
    multimodal_revision: 85,
    terminal_prompt_revision: 88,
    checks: 0,
    fast_path_hits: 0,
    contract_timeouts: 0,
    preflight_budget_exhausted: 0,
    hot_heals: 0,
    reloads: 0,
    reload_timeouts: 0,
    failures: 0,
    last: null,
  };
  globalThis[KEY] = state;

  const baseEnsureContent = ensureContent;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function injectOverlays(tabId) {
    await chrome.scripting.executeScript({ target: {tabId}, files: MAIN_FILES, world: "MAIN" });
    await chrome.scripting.executeScript({ target: {tabId}, files: OVERLAY_FILES });
  }

  async function settleWithin(promise, timeoutMs, fallback = null) {
    let timer = null;
    try {
      return await Promise.race([
        Promise.resolve(promise).catch(() => fallback),
        new Promise(resolve => { timer = setTimeout(() => resolve(fallback), Math.max(50, Number(timeoutMs || 0))); }),
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async function sendMessageBounded(tabId, message, timeoutMs = CONTRACT_TIMEOUT_MS) {
    let timedOut = false;
    const timeout = sleep(timeoutMs).then(() => {
      timedOut = true;
      return null;
    });
    const request = chrome.tabs.sendMessage(tabId, message).catch(() => null);
    const result = await Promise.race([request, timeout]);
    if (timedOut) state.contract_timeouts += 1;
    return result && typeof result === "object" ? result : null;
  }

  async function contract(tabId, timeoutMs = CONTRACT_TIMEOUT_MS) {
    return sendMessageBounded(tabId, {type: "chat2api.runtime.contract.v71"}, timeoutMs);
  }

  function current(result) {
    return Boolean(
      result?.ok &&
      String(result?.marker?.bundle || "") === REQUIRED_BUNDLE &&
      Number(result?.marker?.revision || 0) >= REQUIRED_REVISION &&
      result?.modules?.request_v6 &&
      result?.modules?.rich_response_v69 &&
      result?.modules?.response_stream_v69 &&
      result?.modules?.multimodal_v78 &&
      result?.modules?.multimodal_v84 &&
      result?.modules?.multimodal_v85 &&
      result?.modules?.multimodal_main_v78 &&
      result?.modules?.terminal_prompt_v88
    );
  }

  async function waitForContract(tabId, timeoutMs = HOT_HEAL_BUDGET_MS, delayMs = 100) {
    const deadline = Date.now() + Math.max(200, Number(timeoutMs || 0));
    let last = null;
    while (Date.now() < deadline) {
      const remaining = Math.max(100, deadline - Date.now());
      last = await contract(tabId, Math.min(CONTRACT_TIMEOUT_MS, remaining));
      if (current(last)) return last;
      if (Date.now() < deadline) await sleep(Math.min(delayMs, Math.max(20, deadline - Date.now())));
    }
    return last;
  }

  async function heal(tabId, budgetMs = HOT_HEAL_BUDGET_MS) {
    const started = Date.now();
    const firstBudget = Math.min(700, Math.max(150, budgetMs));
    await settleWithin(baseEnsureContent(tabId), firstBudget, null);
    const elapsed = Date.now() - started;
    const injectBudget = Math.min(900, Math.max(150, budgetMs - elapsed));
    await settleWithin(injectOverlays(tabId), injectBudget, null);
    const remaining = Math.max(200, budgetMs - (Date.now() - started));
    return waitForContract(tabId, remaining, 80);
  }

  async function waitForReloadOrContract(tabId, timeoutMs = RELOAD_BUDGET_MS) {
    const started = Date.now();
    let last = null;
    while (Date.now() - started < timeoutMs) {
      const remaining = Math.max(100, timeoutMs - (Date.now() - started));
      last = await contract(tabId, Math.min(CONTRACT_TIMEOUT_MS, remaining));
      if (current(last)) return { ready: true, complete: false, result: last };
      let tab = null;
      try { tab = await chrome.tabs.get(tabId); } catch (_) { return { ready: false, complete: false, result: last }; }
      if (tab?.status === "complete" && /^https:\/\/(?:www\.)?(?:chatgpt\.com|chat\.openai\.com)\//i.test(String(tab.url || ""))) {
        return { ready: false, complete: true, result: last };
      }
      await sleep(80);
    }
    return { ready: false, complete: false, result: last };
  }

  async function recordLast(last) {
    state.last = last;
    await chrome.storage.local.set({chat2apiRuntimePreflightV71: state.last}).catch(() => {});
  }

  async function toolIsolationPreflight(tabId) {
    return sendMessageBounded(tabId, {type: "chat2api.tool-isolation.preflight"});
  }

  async function preflight(tabId) {
    state.checks += 1;
    const started = Date.now();

    let result = await contract(tabId);
    if (current(result)) {
      state.fast_path_hits += 1;
      const toolPreflight = await toolIsolationPreflight(tabId);
      await recordLast({
        tab_id: tabId,
        ok: true,
        mode: "current-fast-path-v87",
        reloaded: false,
        hot_healed: false,
        marker: result.marker,
        modules: result.modules,
        contract_revision: result.contract_revision,
        multimodal_revision: result.multimodal_revision,
        terminal_prompt_revision: 88,
        tool_preflight: toolPreflight,
        elapsed_ms: Date.now() - started,
        at_ms: Date.now(),
      });
      return true;
    }

    // The whole recovery path remains wall-clock bounded by the v87 algorithm.
    // Bundle 0.8.27 additionally requires the v88 terminal/prompt guard, so
    // repaired tabs cannot silently lose successful-terminal protection or the
    // long-prompt fast insert path.
    result = await heal(tabId);
    let reloaded = false;
    const hotHealed = current(result);
    if (hotHealed) state.hot_heals += 1;

    if (!hotHealed) {
      state.reloads += 1;
      reloaded = true;
      try { await chrome.tabs.reload(tabId, {bypassCache: true}); } catch (_) {}
      const reloadState = await waitForReloadOrContract(tabId, RELOAD_BUDGET_MS);
      result = reloadState.result;
      if (!reloadState.ready) {
        if (!reloadState.complete) state.reload_timeouts += 1;
        result = await heal(tabId, FINAL_HEAL_BUDGET_MS);
      }
    }

    if (!current(result)) {
      state.failures += 1;
      state.preflight_budget_exhausted += 1;
      await recordLast({
        tab_id: tabId,
        ok: false,
        mode: "repair-budget-exhausted-v87",
        reloaded,
        hot_healed: hotHealed,
        result,
        elapsed_ms: Date.now() - started,
        budget_ms: CONTRACT_TIMEOUT_MS + HOT_HEAL_BUDGET_MS + RELOAD_BUDGET_MS + FINAL_HEAL_BUDGET_MS,
        at_ms: Date.now(),
      });
      const error = new Error(`ChatGPT tab Worker runtime is stale or incomplete after the v87-bounded preflight budget; required bundle ${REQUIRED_BUNDLE} content revision ${REQUIRED_REVISION} multimodal revision 85 terminal/prompt revision 88`);
      error.code = "chatgpt_runtime_preflight_budget";
      throw error;
    }

    const toolPreflight = await toolIsolationPreflight(tabId);
    await recordLast({
      tab_id: tabId,
      ok: true,
      mode: reloaded ? "reload-repair-v87" : "hot-repair-v87",
      reloaded,
      hot_healed: hotHealed,
      marker: result.marker,
      modules: result.modules,
      contract_revision: result.contract_revision,
      multimodal_revision: result.multimodal_revision,
      terminal_prompt_revision: 88,
      tool_preflight: toolPreflight,
      elapsed_ms: Date.now() - started,
      at_ms: Date.now(),
    });
    return true;
  }

  ensureContent = async function ensureCurrentWorkerRuntimeV71(tabId) {
    const id = Number(tabId);
    if (!Number.isInteger(id)) throw new Error("A valid ChatGPT tab id is required for Worker runtime preflight");
    if (inflight.has(id)) return inflight.get(id);
    const task = preflight(id).finally(() => inflight.delete(id));
    inflight.set(id, task);
    return task;
  };
})();