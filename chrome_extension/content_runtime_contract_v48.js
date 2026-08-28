(() => {
  const KEY = "__CHAT2API_CONTENT_RUNTIME_CONTRACT_V48__";
  if (globalThis[KEY]) return;

  const REQUIRED_BUNDLE = "0.8.6";
  const snapshot = () => {
    const marker = globalThis.__CHAT2API_CONTENT_BUNDLE_MARKER_V48__ || null;
    const modules = {
      request_v5: Boolean(globalThis.__CHAT2API_REQUEST_CONTENT_V5__),
      response_capture_v41: Number(globalThis.__CHAT2API_RESPONSE_CAPTURE_V41__?.version || 0) === 41,
      completion_recovery_v6: Boolean(globalThis.__CHAT2API_COMPLETION_RECOVERY_V6__),
      tool_isolation_v48: Number(globalThis.__CHAT2API_TOOL_ISOLATION_V48__?.version || 0) === 48,
      response_stream_recovery_v48: Number(globalThis.__CHAT2API_RESPONSE_STREAM_RECOVERY_V48__?.version || 0) === 48,
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
