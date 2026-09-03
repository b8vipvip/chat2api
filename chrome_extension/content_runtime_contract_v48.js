(() => {
  const KEY = "__CHAT2API_CONTENT_RUNTIME_CONTRACT_V48__";
  if (globalThis[KEY]) return;

  const REQUIRED_BUNDLE = "0.8.23";
  const snapshot = () => {
    const marker = globalThis.__CHAT2API_CONTENT_BUNDLE_MARKER_V48__ || null;
    const responseOwner = globalThis.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__ || null;
    const networkRecovery = globalThis.__CHAT2API_NETWORK_STREAM_RECOVERY_V55__ || null;
    const semanticHelper = globalThis.__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__ || null;
    const modules = {
      request_v5: Boolean(globalThis.__CHAT2API_REQUEST_CONTENT_V5__),
      request_lifecycle_v50: Number(globalThis.__CHAT2API_REQUEST_LIFECYCLE_V50__?.version || 0) === 50,
      response_capture_v41: Number(globalThis.__CHAT2API_RESPONSE_CAPTURE_V41__?.version || 0) === 41,
      completion_recovery_v6: Boolean(globalThis.__CHAT2API_COMPLETION_RECOVERY_V6__),
      rate_limit_guard_v52: Boolean(globalThis.__CHAT2API_RATE_LIMIT_CONTENT_V52__),
      tool_isolation_v48: Number(globalThis.__CHAT2API_TOOL_ISOLATION_V48__?.version || 0) === 48,
      draft_managed_recovery_v55: Number(globalThis.__CHAT2API_DRAFT_MANAGED_RECOVERY_V55__?.version || 0) === 55,
      response_stream_recovery_v49: Number(responseOwner?.version || 0) === 49,
      response_single_owner_v53: Number(responseOwner?.owner_revision || 0) === 53 && Boolean(responseOwner?.timer),
      network_stream_recovery_v55: Number(networkRecovery?.version || 0) === 55,
      network_stream_main_v55: document.documentElement?.getAttribute?.("data-chat2api-network-stream-main-v55") === "55",
      network_stream_parser_v62: document.documentElement?.getAttribute?.("data-chat2api-network-stream-parser") === "62",
      response_semantic_recovery_v51: Number(semanticHelper?.version || 0) === 51 && semanticHelper?.timer == null,
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
      response_observer_owner: responseOwner?.owner || null,
      response_observer_revision: Number(responseOwner?.owner_revision || 0),
      network_response_recovery: Number(networkRecovery?.version || 0) === 55 ? "conversation-sse-v55-parser-v62" : null,
      network_response_parser_revision: Number(document.documentElement?.getAttribute?.("data-chat2api-network-stream-parser") || 0),
      semantic_helper_mode: semanticHelper?.mode || null,
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