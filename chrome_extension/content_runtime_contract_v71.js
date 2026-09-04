(() => {
  const KEY = "__CHAT2API_CONTENT_RUNTIME_CONTRACT_V71__";
  const prior = globalThis[KEY];
  if (prior?.listener) {
    try { chrome.runtime.onMessage.removeListener(prior.listener); } catch (_) {}
  }

  const REQUIRED_BUNDLE = "0.8.28";
  const REQUIRED_REVISION = 71;

  function snapshot() {
    const marker = globalThis.__CHAT2API_CONTENT_BUNDLE_MARKER_V71__ || null;
    const request = globalThis.__CHAT2API_REQUEST_CONTENT_V6__ || null;
    const rich = globalThis.__CHAT2API_RICH_RESPONSE_V69__ || null;
    const recovery = globalThis.__CHAT2API_RESPONSE_STREAM_RECOVERY_V69__ || null;
    const networkRecovery = globalThis.__CHAT2API_NETWORK_STREAM_RECOVERY_V55__ || null;
    const semanticHelper = globalThis.__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__ || null;
    const legacyResponseOwner = globalThis.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__ || null;
    const multimodal = globalThis.__CHAT2API_MULTIMODAL_V4__ || null;
    const terminalPrompt = globalThis.__CHAT2API_REQUEST_TERMINAL_PROMPT_V88__ || null;
    const modules = {
      request_v6: Number(request?.revision || 0) >= 69,
      rich_response_v69: Boolean(rich),
      response_stream_v69: Number(recovery?.version || 0) >= 69,
      multimodal_v78: Number(multimodal?.revision || 0) >= 78,
      multimodal_v84: Number(multimodal?.revision || 0) >= 84 && typeof multimodal?.waitForReady === "function",
      multimodal_v85: Number(multimodal?.revision || 0) >= 85 && typeof multimodal?.waitForSafeSubmit === "function",
      multimodal_main_v78: document.documentElement?.getAttribute?.("data-chat2api-multimodal-main-v78") === "78",
      terminal_prompt_v88: Number(terminalPrompt?.revision || 0) >= 88,
      request_lifecycle_v50: Number(globalThis.__CHAT2API_REQUEST_LIFECYCLE_V50__?.version || 0) === 50,
      response_capture_v41: Number(globalThis.__CHAT2API_RESPONSE_CAPTURE_V41__?.version || 0) === 41,
      completion_recovery_v6: Boolean(globalThis.__CHAT2API_COMPLETION_RECOVERY_V6__),
      rate_limit_guard_v52: Boolean(globalThis.__CHAT2API_RATE_LIMIT_CONTENT_V52__),
      tool_isolation_v48: Number(globalThis.__CHAT2API_TOOL_ISOLATION_V48__?.version || 0) === 48,
      draft_managed_recovery_v55: Number(globalThis.__CHAT2API_DRAFT_MANAGED_RECOVERY_V55__?.version || 0) === 55,
      response_single_owner_v53: Number(legacyResponseOwner?.owner_revision || 0) === 53,
      network_stream_recovery_v55: Number(networkRecovery?.version || 0) === 55,
      network_stream_main_v55: document.documentElement?.getAttribute?.("data-chat2api-network-stream-main-v55") === "55",
      network_stream_parser_v62: document.documentElement?.getAttribute?.("data-chat2api-network-stream-parser") === "62",
      response_semantic_recovery_v51: Number(semanticHelper?.version || 0) === 51 && semanticHelper?.timer == null,
      transient_retry_v50: Number(globalThis.__CHAT2API_TRANSIENT_RETRY_V50__?.version || 0) === 50,
      generation_liveness_v49: Number(globalThis.__CHAT2API_GENERATION_LIVENESS_V49__?.version || 0) === 49,
    };
    const markerCurrent = String(marker?.bundle || "") === REQUIRED_BUNDLE && Number(marker?.revision || 0) >= REQUIRED_REVISION;
    const modulesCurrent = Object.values(modules).every(Boolean);
    return {
      ok: markerCurrent && modulesCurrent,
      version: 71,
      contract_revision: REQUIRED_REVISION,
      required_bundle: REQUIRED_BUNDLE,
      marker: marker ? {...marker} : null,
      marker_ok: markerCurrent,
      modules,
      modules_ok: modulesCurrent,
      request_revision: Number(request?.revision || 0),
      response_revision: Number(recovery?.version || 0),
      multimodal_revision: Number(multimodal?.revision || 0),
      terminal_prompt_revision: Number(terminalPrompt?.revision || 0),
      network_response_recovery: Number(networkRecovery?.version || 0) === 55 ? "conversation-sse-v55-parser-v62" : null,
      network_response_parser_revision: Number(document.documentElement?.getAttribute?.("data-chat2api-network-stream-parser") || 0),
      semantic_helper_mode: semanticHelper?.mode || null,
      document_url: String(location.href || ""),
      document_ready_state: String(document.readyState || ""),
      checked_at_ms: Date.now(),
    };
  }

  const listener = (message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.runtime.contract.v71") return false;
    sendResponse(snapshot());
    return false;
  };

  const state = { version: 71, required_bundle: REQUIRED_BUNDLE, listener, snapshot };
  globalThis[KEY] = state;
  chrome.runtime.onMessage.addListener(listener);
})();