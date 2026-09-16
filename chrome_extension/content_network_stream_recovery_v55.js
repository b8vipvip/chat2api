(() => {
  const KEY = "__CHAT2API_NETWORK_STREAM_RECOVERY_V55__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-network-stream-v55";
  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const streams = new Map();
  const state = {
    version: 56,
    owner: "network-stream-evidence-v56",
    streams,
    snapshots: 0,
    diagnostics: 0,
    terminal_hints: 0,
  };
  globalThis[KEY] = state;

  function activeRequest() {
    return globalThis[REQUEST_KEY]?.active || null;
  }

  function requestIdentity() {
    const active = activeRequest();
    if (active?.requestId && !active.cancelled) return String(active.requestId);
    return "";
  }

  async function emit(event) {
    try {
      const result = await chrome.runtime.sendMessage({ type: "chat2api.event", event });
      return result?.ok !== false;
    } catch (_) {
      return false;
    }
  }

  function binding(streamId, create = false) {
    const key = String(streamId || "");
    if (!key) return null;
    let row = streams.get(key) || null;
    if (!row && create) {
      const requestId = requestIdentity();
      if (!requestId) return null;
      row = { requestId, streamId: key, lastText: "", responseSeen: false, eventStream: false, status: null, chunks: 0, bytes: 0 };
      streams.set(key, row);
    }
    return row;
  }

  function compatibleAdvance(current, candidate) {
    const before = String(current || "");
    const next = String(candidate || "");
    if (!next || next === before) return false;
    if (!before) return true;
    return next.startsWith(before) || before.startsWith(next) ? next.length > before.length : false;
  }

  function recordEvidence(row, text) {
    const value = String(text || "");
    if (!value) return;
    if (!row.lastText || compatibleAdvance(row.lastText, value)) row.lastText = value;
    const active = activeRequest();
    if (String(active?.requestId || "") !== row.requestId) return;
    const current = String(active.networkObservedText || "");
    if (!current || compatibleAdvance(current, value)) {
      active.networkObservedText = value;
      active.networkObservedAt = Date.now();
    }
  }

  async function diagnostics(row, detail) {
    state.diagnostics += 1;
    await emit({
      type: "chat.diagnostics",
      request_id: row.requestId,
      diagnostics: {
        network_stream_observer: "conversation-fetch-v55",
        network_response_recovery: "evidence-only-v56",
        network_stream_phase: String(detail.phase || ""),
        network_stream_id: row.streamId,
        network_stream_sequence: Number(detail.sequence || 0),
        network_stream_chunks: Number(detail.chunks ?? row.chunks ?? 0),
        network_stream_bytes: Number(detail.bytes ?? row.bytes ?? 0),
        network_stream_http_status: row.status,
        network_stream_event_stream: row.eventStream,
        network_recovered_assistant_chars: String(row.lastText || "").length,
        network_terminal_authority: "request-v6",
        generation_progress: `${row.streamId}:${Number(detail.sequence || 0)}:${Number(detail.bytes ?? row.bytes ?? 0)}`,
        generating_observed: true,
      },
    });
  }

  async function snapshot(row, detail) {
    const text = String(detail.text || "");
    if (!text) return;
    const previous = row.lastText;
    recordEvidence(row, text);
    if (row.lastText === previous) return;
    state.snapshots += 1;
    await emit({
      type: "chat.snapshot",
      request_id: row.requestId,
      text: row.lastText,
      diagnostics: {
        response_stream_recovery: "network-sse-evidence-v56",
        response_semantic_recovery: "assistant-role-only-sse-v55",
        network_stream_id: row.streamId,
        network_recovered_assistant_chars: row.lastText.length,
        network_recovery_source: String(detail.parser_source || "assistant-message"),
        network_terminal_authority: "request-v6",
      },
    });
  }

  async function terminalHint(row, detail) {
    const text = String(detail.text || row.lastText || "");
    if (text) recordEvidence(row, text);
    state.terminal_hints += 1;
    await diagnostics(row, { ...detail, phase: "terminal-hint" });
    // Network completion is evidence only. request-v6 owns the one and only
    // chat.completed decision after reconciling DOM and network observations.
    streams.delete(row.streamId);
  }

  async function onNetwork(detail) {
    const phase = String(detail?.phase || "");
    const row = binding(detail?.stream_id, phase === "response");
    if (!row) return;
    if (phase === "response") {
      row.responseSeen = true;
      row.status = Number(detail.status || 0) || null;
      row.eventStream = detail.event_stream === true;
      await diagnostics(row, detail);
      return;
    }
    if (phase === "chunk") {
      row.chunks = Number(detail.chunks || row.chunks || 0);
      row.bytes = Number(detail.bytes || row.bytes || 0);
      if (row.chunks === 1 || row.chunks % 4 === 0) await diagnostics(row, detail);
      return;
    }
    if (phase === "assistant-snapshot") {
      await snapshot(row, detail);
      return;
    }
    if (phase === "assistant-complete") {
      await terminalHint(row, detail);
      return;
    }
    if (phase === "done") {
      row.chunks = Number(detail.chunks || row.chunks || 0);
      row.bytes = Number(detail.bytes || row.bytes || 0);
      if (row.lastText) await terminalHint(row, { ...detail, text: row.lastText });
      else {
        await diagnostics(row, detail);
        streams.delete(row.streamId);
      }
      return;
    }
    if (phase === "error") {
      await diagnostics(row, detail);
      streams.delete(row.streamId);
    }
  }

  globalThis.addEventListener("message", event => {
    if (event.source !== globalThis) return;
    const detail = event.data;
    if (!detail || detail.source !== SOURCE) return;
    onNetwork(detail).catch(() => {});
  });
})();
