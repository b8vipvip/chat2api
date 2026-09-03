(() => {
  const KEY = "__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__";
  if (globalThis[KEY]) return;

  // Worker bundle 0.8.22 keeps the v71 request/response epoch while requiring
  // the v78 MAIN-world upload bridge plus the v84 attachment readiness gate.
  const REQUIRED_BUNDLE = "0.8.22";
  const REQUIRED_REVISION = 71;
  const MAIN_FILES = ["network_stream_main_v55.js", "multimodal_main_v78.js"];
  const OVERLAY_FILES = [
    "content_rate_limit_guard_v52.js",
    "content_tool_isolation_v48.js",
    "content_multimodal_v78.js",
    "content_multimodal_settle_v84.js",
    "content_request_lifecycle_v50.js",
    "content_request_hygiene_v42.js",
    "content_draft_managed_recovery_v55.js",
    "content_rich_response_v69.js",
    "content_request_v6.js",
    "content_response_stream_recovery_v69.js",
    "content_network_stream_recovery_v55.js",
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
    required_bundle: REQUIRED_BUNDLE,
    required_revision: REQUIRED_REVISION,
    multimodal_revision: 84,
    checks: 0,
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

  async function contract(tabId) {
    try {
      const result = await chrome.tabs.sendMessage(tabId, {type: "chat2api.runtime.contract.v71"});
      return result && typeof result === "object" ? result : null;
    } catch (_) { return null; }
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
      result?.modules?.multimodal_main_v78
    );
  }

  async function waitForContract(tabId, attempts = 20, delayMs = 100) {
    let last = null;
    for (let index = 0; index < attempts; index += 1) {
      last = await contract(tabId);
      if (current(last)) return last;
      if (index + 1 < attempts) await sleep(delayMs);
    }
    return last;
  }

  async function heal(tabId) {
    try { await baseEnsureContent(tabId); } catch (_) {}
    try { await injectOverlays(tabId); } catch (_) {}
    return waitForContract(tabId, 24, 100);
  }

  async function waitForReloadOrContract(tabId, timeoutMs = 20000) {
    const started = Date.now();
    let last = null;
    while (Date.now() - started < timeoutMs) {
      last = await contract(tabId);
      if (current(last)) return { ready: true, complete: false, result: last };
      let tab = null;
      try { tab = await chrome.tabs.get(tabId); } catch (_) { return { ready: false, complete: false, result: last }; }
      if (tab?.status === "complete" && /^https:\/\/(?:www\.)?(?:chatgpt\.com|chat\.openai\.com)\//i.test(String(tab.url || ""))) {
        return { ready: false, complete: true, result: last };
      }
      await sleep(100);
    }
    return { ready: false, complete: false, result: last };
  }

  async function preflight(tabId) {
    state.checks += 1;
    const started = Date.now();
    let result = await heal(tabId);
    let reloaded = false;
    const hotHealed = current(result);

    if (hotHealed) state.hot_heals += 1;

    if (!hotHealed) {
      state.reloads += 1;
      reloaded = true;
      try { await chrome.tabs.reload(tabId, {bypassCache: true}); } catch (_) {}
      const reloadState = await waitForReloadOrContract(tabId, 20000);
      result = reloadState.result;
      if (!reloadState.ready) {
        if (!reloadState.complete) state.reload_timeouts += 1;
        // A ChatGPT document can already be usable while chrome.tabs still
        // reports the navigation as loading. Re-establish the content epoch
        // and trust the runtime contract instead of tab.status alone.
        result = await heal(tabId);
      }
    }

    if (!current(result)) {
      state.failures += 1;
      state.last = {tab_id: tabId, ok: false, reloaded, hot_healed: hotHealed, result, elapsed_ms: Date.now() - started, at_ms: Date.now()};
      await chrome.storage.local.set({chat2apiRuntimePreflightV71: state.last}).catch(() => {});
      throw new Error(`ChatGPT tab Worker runtime is stale or incomplete; required bundle ${REQUIRED_BUNDLE} content revision ${REQUIRED_REVISION} multimodal revision 84`);
    }

    let toolPreflight = null;
    try { toolPreflight = await chrome.tabs.sendMessage(tabId, {type: "chat2api.tool-isolation.preflight"}); } catch (_) {}
    state.last = {
      tab_id: tabId,
      ok: true,
      reloaded,
      hot_healed: hotHealed,
      marker: result.marker,
      modules: result.modules,
      contract_revision: result.contract_revision,
      multimodal_revision: result.multimodal_revision,
      tool_preflight: toolPreflight,
      elapsed_ms: Date.now() - started,
      at_ms: Date.now(),
    };
    await chrome.storage.local.set({chat2apiRuntimePreflightV71: state.last}).catch(() => {});
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