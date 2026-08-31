(() => {
  const KEY = "__CHAT2API_NETWORK_STREAM_RECOVERY_V55__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-network-stream-v55";
  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const RESPONSE_KEY = "__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__";
  const streams = new Map();
  const completed = new Set();
  const state = {
    version: 55,
    streams,
    snapshots: 0,
    completions: 0,
    diagnostics: 0,
  };
  globalThis[KEY] = state;

  function activeRequestId() {
    return String(globalThis[REQUEST_KEY]?.active?.requestId || "");
  }

  function responseContext(requestId) {
    const owner = globalThis[RESPONSE_KEY];
    const ctx = owner?.request;
    return String(ctx?.requestId || "") === String(requestId || "") ? ctx : null;
  }

  async function emit(event) {
    try {
      await chrome.runtime.sendMessage({ type: "chat2api.event", event });
      return true;
    } catch (_) {
      return false;
    }
  }

  function binding(streamId, create = false) {
    const key = String(streamId || "");
    if (!key) return null;
    let row = streams.get(key) || null;
    if (!row && create) {
      const requestId = activeRequestId();
      if (!requestId) return null;
      row = {
        requestId,
        streamId: key,
        lastText: "",
        responseSeen: false,
        eventStream: false,
        status: null,
        chunks: 0,
        bytes: 0,
      };
      streams.set(key, row);
    }
    return row;
  }

  function touchOwner(row, text = "") {
    const ctx = responseContext(row?.requestId);
    if (!ctx) return;
    const now = Date.now();
    if (!ctx.generationSeenAt) ctx.generationSeenAt = now;
    ctx.lastMeaningfulProgressAt = now;
    if (text) {
      ctx.emittedText = text;
      ctx.lastText = text;
      ctx.changedAt = now;
    }
  }

  async function diagnostics(row, detail) {
    state.diagnostics += 1;
    await emit({
      type: "chat.diagnostics",
      request_id: row.requestId,
      diagnostics: {
        network_stream_observer: "conversation-fetch-v55",
        network_response_recovery: "sse-assistant-v55",
        network_stream_phase: String(detail.phase || ""),
        network_stream_id: row.streamId,
        network_stream_sequence: Number(detail.sequence || 0),
        network_stream_chunks: Number(detail.chunks ?? row.chunks ?? 0),
        network_stream_bytes: Number(detail.bytes ?? row.bytes ?? 0),
        network_stream_http_status: row.status,
        network_stream_event_stream: row.eventStream,
        network_stream_controller_detached: !activeRequestId(),
        network_recovered_assistant_chars: String(row.lastText || "").length,
        generation_progress: `${row.streamId}:${Number(detail.sequence || 0)}:${Number(detail.bytes ?? row.bytes ?? 0)}`,
        generating_observed: true,
      },
    });
  }

  async function snapshot(row, detail) {
    const text = String(detail.text || "");
    if (!text || text === row.lastText || completed.has(row.requestId)) return;
    row.lastText = text;
    state.snapshots += 1;
    touchOwner(row, text);
    await emit({
      type: "chat.snapshot",
      request_id: row.requestId,
      text,
      diagnostics: {
        response_stream_recovery: "network-sse-v55",
        response_semantic_recovery: "assistant-role-only-sse-v55",
        network_stream_id: row.streamId,
        network_recovered_assistant_chars: text.length,
        network_recovery_source: String(detail.parser_source || "assistant-message"),
      },
    });
  }

  async function complete(row, detail) {
    const text = String(detail.text || row.lastText || "");
    if (!text || completed.has(row.requestId)) return;
    const ctx = responseContext(row.requestId);
    if (ctx?.completed) {
      completed.add(row.requestId);
      streams.delete(row.streamId);
      return;
    }
    completed.add(row.requestId);
    state.completions += 1;
    row.lastText = text;
    touchOwner(row, text);
    if (ctx) ctx.completed = true;
    await emit({
      type: "chat.completed",
      request_id: row.requestId,
      text,
      diagnostics: {
        response_stream_recovery: "network-sse-v55",
        response_semantic_recovery: "assistant-role-only-sse-v55",
        response_stream_completion_reason: "conversation-sse-ended",
        network_stream_id: row.streamId,
        network_recovered_assistant_chars: text.length,
        network_completion_hint: Boolean(detail.completion_hint),
      },
    });
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
      touchOwner(row);
      if (row.chunks === 1 || row.chunks % 4 === 0) await diagnostics(row, detail);
      return;
    }

    if (phase === "assistant-snapshot") {
      await snapshot(row, detail);
      return;
    }

    if (phase === "assistant-complete") {
      await complete(row, detail);
      return;
    }

    if (phase === "done") {
      row.chunks = Number(detail.chunks || row.chunks || 0);
      row.bytes = Number(detail.bytes || row.bytes || 0);
      await diagnostics(row, detail);
      if (row.lastText) await complete(row, { ...detail, text: row.lastText });
      else streams.delete(row.streamId);
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

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.runtime.contract.v48") return false;
    return false;
  });
})();