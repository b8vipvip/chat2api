(() => {
  const KEY = "__CHAT2API_CONTENT_RUNTIME_CONTRACT_V48__";
  if (globalThis[KEY]) return;

  const REQUIRED_BUNDLE = "0.8.8";
  const snapshot = () => {
    const marker = globalThis.__CHAT2API_CONTENT_BUNDLE_MARKER_V48__ || null;
    const modules = {
      request_v5: Boolean(globalThis.__CHAT2API_REQUEST_CONTENT_V5__),
      request_lifecycle_v50: Number(globalThis.__CHAT2API_REQUEST_LIFECYCLE_V50__?.version || 0) === 50,
      response_capture_v41: Number(globalThis.__CHAT2API_RESPONSE_CAPTURE_V41__?.version || 0) === 41,
      completion_recovery_v6: Boolean(globalThis.__CHAT2API_COMPLETION_RECOVERY_V6__),
      tool_isolation_v48: Number(globalThis.__CHAT2API_TOOL_ISOLATION_V48__?.version || 0) === 48,
      response_stream_recovery_v49: Number(globalThis.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__?.version || 0) === 49,
      transient_retry_v50: Number(globalThis.__CHAT2API_TRANSIENT_RETRY_V50__?.version || 0) === 50,
      generation_liveness_v49: Number(globalThis.__CHAT2API_GENERATION_LIVENESS_V49__?.version || 0) === 49,
    };
    const markerOk = Number(marker?.version || 0) === 48 && String(marker?.bundle || "") === REQUIRED_BUNDLE;
    const modulesOk = Object.values(modules).every(Boolean);
    return {
      ok: markerOk && modulesOk,
      version: 48,
      required_bundle: REQUIRED_BUNDLE,
      marker: marker ? {...marker} : null,
      marker_ok: markerOk,
      modules,
      modules_ok: modulesOk,
      document_url: String(location.href || ""),
      document_ready_state: String(document.readyState || ""),
      checked_at_ms: Date.now(),
    };
  };

  const listener = (message, sender, sendResponse) => {
    if (message?.type !== "chat2api.runtime.contract.v48") return false;
    sendResponse(snapshot());
    return true;
  };
  chrome.runtime.onMessage.addListener(listener);

  globalThis[KEY] = Object.freeze({ version: 48, required_bundle: REQUIRED_BUNDLE, snapshot });
})();
