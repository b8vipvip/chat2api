(() => {
  const KEY = "__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V48__";
  if (globalThis[KEY]) return;

  const REQUIRED_BUNDLE = "0.8.8";
  const OVERLAY_FILES = [
    "content_tool_isolation_v48.js",
    "content_request_lifecycle_v50.js",
    "content_response_stream_recovery_v49.js",
    "content_transient_retry_v50.js",
    "content_generation_liveness_v49.js",
    "content_runtime_contract_v48.js",
  ];
  const inflight = new Map();
  const state = {
    version: 48,
    required_bundle: REQUIRED_BUNDLE,
    checks: 0,
    reloads: 0,
    failures: 0,
    last: null,
  };
  globalThis[KEY] = state;

  const baseEnsureContent = ensureContent;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function injectOverlays(tabId) {
    await chrome.scripting.executeScript({ target: {tabId}, files: OVERLAY_FILES });
  }

  async function contract(tabId) {
    try {
      const result = await chrome.tabs.sendMessage(tabId, {type: "chat2api.runtime.contract.v48"});
      return result && typeof result === "object" ? result : null;
    } catch (_) { return null; }
  }

  async function waitForContract(tabId, attempts = 12, delayMs = 100) {
    let last = null;
    for (let index = 0; index < attempts; index += 1) {
      last = await contract(tabId);
      if (last?.ok && String(last?.marker?.bundle || "") === REQUIRED_BUNDLE) return last;
      if (index + 1 < attempts) await sleep(delayMs);
    }
    return last;
  }

  async function waitForComplete(tabId, timeoutMs = 20000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      let tab = null;
      try { tab = await chrome.tabs.get(tabId); } catch (_) { return false; }
      if (tab?.status === "complete" && /^https:\/\/(?:www\.)?(?:chatgpt\.com|chat\.openai\.com)\//i.test(String(tab.url || ""))) return true;
      await sleep(100);
    }
    return false;
  }

  async function preflight(tabId) {
    state.checks += 1;
    const started = Date.now();
    await baseEnsureContent(tabId);
    try { await injectOverlays(tabId); } catch (_) {}
    let result = await waitForContract(tabId, 8, 80);
    let reloaded = false;

    if (!result?.ok || String(result?.marker?.bundle || "") !== REQUIRED_BUNDLE) {
      state.reloads += 1;
      reloaded = true;
      await chrome.tabs.reload(tabId, {bypassCache: true});
      if (!await waitForComplete(tabId)) throw new Error("ChatGPT tab did not finish reloading for Worker runtime refresh");
      await sleep(250);
      await baseEnsureContent(tabId);
      try { await injectOverlays(tabId); } catch (_) {}
      result = await waitForContract(tabId, 16, 100);
    }

    if (!result?.ok || String(result?.marker?.bundle || "") !== REQUIRED_BUNDLE) {
      state.failures += 1;
      state.last = {tab_id: tabId, ok: false, reloaded, result, elapsed_ms: Date.now() - started, at_ms: Date.now()};
      await chrome.storage.local.set({chat2apiRuntimePreflightV48: state.last}).catch(() => {});
      throw new Error(`ChatGPT tab Worker runtime is stale or incomplete; required bundle ${REQUIRED_BUNDLE}`);
    }

    let toolPreflight = null;
    try { toolPreflight = await chrome.tabs.sendMessage(tabId, {type: "chat2api.tool-isolation.preflight"}); } catch (_) {}
    state.last = {
      tab_id: tabId,
      ok: true,
      reloaded,
      marker: result.marker,
      modules: result.modules,
      tool_preflight: toolPreflight,
      elapsed_ms: Date.now() - started,
      at_ms: Date.now(),
    };
    await chrome.storage.local.set({chat2apiRuntimePreflightV48: state.last}).catch(() => {});
    return true;
  }

  ensureContent = async function ensureCurrentWorkerRuntimeV48(tabId) {
    const id = Number(tabId);
    if (!Number.isInteger(id)) throw new Error("A valid ChatGPT tab id is required for Worker runtime preflight");
    if (inflight.has(id)) return inflight.get(id);
    const task = preflight(id).finally(() => inflight.delete(id));
    inflight.set(id, task);
    return task;
  };
})();
