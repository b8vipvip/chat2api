(() => {
  const KEY = "__CHAT2API_NETWORK_STREAM_PROGRESS_V54__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-network-stream-v54";
  const EVENT = "chat2api.network-stream.v54";
  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const RECOVERY_KEY = "__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__";
  const EMIT_THROTTLE_MS = 900;

  const state = {
    version: 54,
    sequence: 0,
    lastEmitAt: 0,
    lastStreamId: "",
    totalBytes: 0,
    chunks: 0,
    contract: null,
  };
  globalThis[KEY] = state;

  function requestIdentity() {
    const active = globalThis[REQUEST_KEY]?.active || null;
    if (active?.requestId) {
      if (active.cancelled) return null;
      return { requestId: String(active.requestId), active, detached: false };
    }
    // request-v5 may release its local controller while response recovery still
    // owns the server request. Continue correlating stream liveness to that same
    // request so a healthy SSE cannot regress into the historical 45s DOM stall.
    const ctx = globalThis[RECOVERY_KEY]?.request || null;
    if (!ctx?.requestId || ctx.completed || ctx.failed) return null;
    return { requestId: String(ctx.requestId), active: null, detached: true };
  }

  function recoveryContext(requestId) {
    const recovery = globalThis[RECOVERY_KEY];
    const ctx = recovery?.request;
    return ctx && String(ctx.requestId || "") === String(requestId || "") ? ctx : null;
  }

  function touchRecovery(requestId, now = Date.now()) {
    const ctx = recoveryContext(requestId);
    if (!ctx) return false;
    ctx.generationSeenAt ||= now;
    ctx.lastMeaningfulProgressAt = now;
    return true;
  }

  async function emitDiagnostics(identity, detail, force = false, progress = true) {
    const now = Date.now();
    if (!force && now - state.lastEmitAt < EMIT_THROTTLE_MS) return false;
    state.lastEmitAt = now;
    state.sequence += 1;
    const diagnostics = {
      network_stream_observer: "conversation-fetch-v54",
      network_stream_phase: String(detail.phase || ""),
      network_stream_id: String(detail.stream_id || "").slice(0, 80),
      network_stream_sequence: state.sequence,
      network_stream_chunks: state.chunks,
      network_stream_bytes: state.totalBytes,
      network_stream_http_status: Number(detail.status || 0) || null,
      network_stream_event_stream: detail.event_stream === true,
      network_stream_controller_detached: identity.detached === true,
    };
    if (progress) {
      diagnostics.generation_progress = `${String(detail.stream_id || "stream").slice(0, 40)}:${state.sequence}:${state.totalBytes}`;
      diagnostics.generating_observed = true;
    }
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: identity.requestId,
          diagnostics,
        },
      });
      return true;
    } catch (_) {
      return false;
    }
  }

  async function handle(detail) {
    const identity = requestIdentity();
    if (!identity) return false;

    const phase = String(detail?.phase || "");
    if (!["response", "chunk", "done", "error", "unavailable"].includes(phase)) return false;

    const streamId = String(detail.stream_id || "");
    if (phase === "response" && streamId && streamId !== state.lastStreamId) {
      state.lastStreamId = streamId;
      state.totalBytes = 0;
      state.chunks = 0;
    } else if (streamId) {
      state.lastStreamId = streamId;
    }

    if (phase === "chunk") {
      state.chunks = Math.max(state.chunks + 1, Number(detail.sequence || 0));
      state.totalBytes = Math.max(state.totalBytes, Number(detail.total_bytes || 0));
    } else if (phase === "done" || phase === "error") {
      state.totalBytes = Math.max(state.totalBytes, Number(detail.total_bytes || 0));
    }

    const progress = phase === "response" || phase === "chunk" || phase === "done";
    const touched = progress ? touchRecovery(identity.requestId) : false;

    if (phase === "response") return emitDiagnostics(identity, detail, true, true);
    if (phase === "chunk") return emitDiagnostics(identity, detail, false, true);
    if (phase === "done") return emitDiagnostics(identity, detail, true, true);
    if (phase === "error" || phase === "unavailable") {
      // Do not synthesize chat.error or generation progress here. The DOM response
      // owner remains authoritative for terminal decisions; this is diagnostics.
      return emitDiagnostics(identity, detail, true, false);
    }
    return touched;
  }

  function listener(event) {
    if (event.source !== window) return;
    const detail = event.data;
    if (!detail || detail.source !== SOURCE || detail.type !== EVENT) return;
    handle(detail).catch(() => {});
  }

  state.contract = { requestIdentity, touchRecovery, handle };
  window.addEventListener("message", listener, false);
})();
