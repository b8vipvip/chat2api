(() => {
  const KEY = "__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__";
  if (globalThis[KEY]) return;

  // Worker bundle 0.8.40 keeps the v71 request epoch. request-v6 is the sole
  // response terminal owner; network v55 supplies evidence only.
  const REQUIRED_BUNDLE = "0.8.40";
  const REQUIRED_REVISION = 71;
  const CONTRACT_TIMEOUT_MS = 700;
  const HOT_HEAL_BUDGET_MS = 2400;
  const RELOAD_BUDGET_MS = 3500;
  const FINAL_HEAL_BUDGET_MS = 1800;
  const MAIN_FILES = ["network_stream_main_v55.js", "multimodal_main_v78.js"];
  const NATIVE_MAIN_FILES = ["native_tool_stream_main_v63.js"];
  const CURRENT_MAIN_FILES = [MAIN_FILES[0], ...NATIVE_MAIN_FILES, MAIN_FILES[1]];
  const OVERLAY_FILES = [
    "content_ui_hygiene_v31.js",
    "content_rate_limit_guard_v52.js",
    "content_tool_isolation_v48.js",
    "content_multimodal_v78.js",
    "content_multimodal_settle_v84.js",
    "content_multimodal_settle_v85.js",
    "content_request_lifecycle_v50.js",
    "content_conversation_quota_failover_v95.js",
    "content_request_hygiene_v42.js",
    "content_draft_managed_recovery_v55.js",
    "content_rich_response_v69.js",
    "content_request_v6.js",
    "content_network_stream_recovery_v55.js",
    "content_native_tool_stream_v63.js",
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
    revision: 110,
    required_bundle: REQUIRED_BUNDLE,
    required_revision: REQUIRED_REVISION,
    response_terminal_owner: "request-v6",
    native_tool_stream_revision: 63,
    multimodal_revision: 85,
    terminal_prompt_revision: 88,
    conversation_quota_failover_revision: 95,
    ui_hygiene_revision: 101,
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
    await chrome.scripting.executeScript({ target: {tabId}, files: CURRENT_MAIN_FILES, world: "MAIN" });
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
      result?.modules?.network_stream_recovery_v55 &&
      result?.modules?.native_tool_stream_v63 &&
      result?.modules?.native_tool_stream_main_v63 &&
      result?.modules?.multimodal_v78 &&
      result?.modules?.multimodal_v84 &&
      result?.modules?.multimodal_v85 &&
      result?.modules?.multimodal_main_v78 &&
      result?.modules?.terminal_prompt_v88 &&
      result?.modules?.conversation_quota_failover_v95 &&
      result?.modules?.ui_hygiene_v101
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
      if (current(last)) return last;
      await sleep(Math.min(150, remaining));
    }
    return last;
  }

  ensureContent = async function ensureChat2apiRuntimeV71(tabId) {
    const id = Number(tabId || 0);
    if (!id) return baseEnsureContent(tabId);
    if (inflight.has(id)) return inflight.get(id);
    const task = (async () => {
      state.checks += 1;
      let result = await contract(id);
      if (current(result)) {
        state.fast_path_hits += 1;
        state.last = {ok: true, mode: "fast", contract: result};
        return true;
      }
      state.hot_heals += 1;
      result = await heal(id);
      if (current(result)) {
        state.last = {ok: true, mode: "heal", contract: result};
        return true;
      }
      state.reloads += 1;
      try { await chrome.tabs.reload(id); } catch (_) {}
      result = await waitForReloadOrContract(id);
      if (current(result)) {
        state.last = {ok: true, mode: "reload", contract: result};
        return true;
      }
      state.reload_timeouts += 1;
      result = await heal(id, FINAL_HEAL_BUDGET_MS);
      if (current(result)) {
        state.last = {ok: true, mode: "final-heal", contract: result};
        return true;
      }
      state.failures += 1;
      state.last = {ok: false, mode: "failed", contract: result};
      throw new Error("Worker runtime preflight failed: canonical request-v6 response owner is unavailable");
    })().finally(() => inflight.delete(id));
    inflight.set(id, task);
    return task;
  };
})();
