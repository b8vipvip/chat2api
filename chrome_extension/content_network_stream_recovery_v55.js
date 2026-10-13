(() => {
  const KEY = "__CHAT2API_NETWORK_STREAM_RECOVERY_V55__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-network-stream-v55";
  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const REQUEST_V6_KEY = "__CHAT2API_REQUEST_CONTENT_V6__";
  const streams = new Map();
  const state = {
    version: 57,
    owner: "network-stream-evidence-v57",
    streams,
    snapshots: 0,
    diagnostics: 0,
    terminal_hints: 0,
  };
  globalThis[KEY] = state;

  function activeRequest() {
    // v6 alone owns the current response. The legacy v5 active may carry
    // the same requestId yet point to a different object after an overlap.
    const v6 = globalThis[REQUEST_V6_KEY];
    return v6 ? (v6.active || null) : (globalThis[REQUEST_KEY]?.active || null);
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
      row = { requestId, streamId: key, lastText: "", lastTextSequence: 0, responseSeen: false, eventStream: false, status: null, chunks: 0, bytes: 0 };
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

  function recordEvidence(row, text, { authoritative = false, sequence = 0 } = {}) {
    const value = String(text || "");
    if (!value) return false;
    const nextSequence = Number(sequence || 0);
    if (nextSequence && nextSequence <= Number(row.lastTextSequence || 0)
        && value !== row.lastText) return false;
    // The JSON Patch assembler can replace already rendered content, including
    // shortening it. The old prefix-only filter permanently lost such edits.
    // Accept a rewrite ONLY from a newer authoritative snapshot of this exact
    // stream, never from stale/metadata/unrelated assistant candidates.
    const replacement = authoritative && nextSequence > Number(row.lastTextSequence || 0);
    if (row.lastText && !compatibleAdvance(row.lastText, value) && !replacement) return false;
    if (value === row.lastText) return false;
    row.lastText = value;
    row.lastTextSequence = Math.max(Number(row.lastTextSequence || 0), nextSequence);
    const active = activeRequest();
    if (String(active?.requestId || "") === row.requestId) {
      active.networkObservedText = value;
      active.networkObservedAt = Date.now();
    }
    return true;
  }

  function recordTerminalEvidence(row, detail) {
    const active = activeRequest();
    if (String(active?.requestId || "") !== row.requestId || active?.cancelled) return;
    const text = String(row.lastText || detail?.text || "");
    if (!text) return;
    active.networkTerminalText = text;
    active.networkTerminalAt = Date.now();
    active.networkTerminalEvidenceSource = String(detail?.terminal_evidence_source || "stream-eof").slice(0, 80);
    active.networkTerminalStreamId = row.streamId;
    active.networkTerminalSequence = Number(detail?.sequence || 0);
    active.networkTerminalChunks = Number(detail?.chunks ?? row.chunks ?? 0);
    active.networkTerminalBytes = Number(detail?.bytes ?? row.bytes ?? 0);
  }

  async function diagnostics(row, detail) {
    state.diagnostics += 1;
    await emit({
      type: "chat.diagnostics",
      request_id: row.requestId,
      diagnostics: {
        network_stream_observer: "conversation-fetch-v55",
        network_response_recovery: "evidence-only-v57",
        network_stream_phase: String(detail.phase || ""),
        network_stream_id: row.streamId,
        network_stream_sequence: Number(detail.sequence || 0),
        network_stream_chunks: Number(detail.chunks ?? row.chunks ?? 0),
        network_stream_bytes: Number(detail.bytes ?? row.bytes ?? 0),
        network_stream_http_status: row.status,
        network_stream_event_stream: row.eventStream,
        network_recovered_assistant_chars: String(row.lastText || "").length,
        network_terminal_authority: "request-v6",
        network_evidence_active_owner: globalThis[REQUEST_V6_KEY] ? "request-v6" : "legacy-v5",
        network_legacy_active_conflict: Boolean(globalThis[REQUEST_V6_KEY]?.active && globalThis[REQUEST_KEY]?.active && globalThis[REQUEST_V6_KEY].active !== globalThis[REQUEST_KEY].active),
        network_stream_error: String(detail.phase || "") === "error" ? String(detail.error || "stream-reader-failed").slice(0, 240) : null,
        network_stream_terminal_proof: detail.completion_hint === true,
        generation_progress: `${row.streamId}:${Number(detail.sequence || 0)}:${Number(detail.bytes ?? row.bytes ?? 0)}`,
        generating_observed: true,
      },
    });
  }

  async function snapshot(row, detail) {
    const text = String(detail.text || "");
    if (!text) return;
    const accepted = recordEvidence(row, text, {
      authoritative: String(detail.parser_source || "") === "json-patch",
      sequence: Number(detail.sequence || 0),
    });
    if (!accepted) return;
    if (row.terminalSeen) {
      // A verified end-turn can precede a final rich-text replacement.
      // Keep the stream bound through EOF so later revisions update this
      // request's terminal payload instead of becoming orphan snapshots.
      recordTerminalEvidence(row, {
        sequence: Number(detail.sequence || 0),
        terminal_evidence_source: row.terminalEvidenceSource || "assistant-end-turn-patch",
      });
    }
    state.snapshots += 1;
    await emit({
      type: "chat.snapshot",
      request_id: row.requestId,
      text: row.lastText,
      diagnostics: {
        response_stream_recovery: "network-sse-evidence-v57",
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
    if (text) {
      // The terminal frame is verified by the upstream parser; it is newer
      // than snapshots previously observed for this same stream.
      recordEvidence(row, text, {
        authoritative: detail.completion_hint === true,
        sequence: Number(detail.sequence || 0),
      });
    }
    row.terminalSeen = true;
    row.terminalEvidenceSource = String(detail.terminal_evidence_source || "assistant-end-turn-patch");
    recordTerminalEvidence(row, detail);
    state.terminal_hints += 1;
    await diagnostics(row, { ...detail, phase: "terminal-hint" });
    // Do not delete the stream yet: authoritative body patches may follow an
    // early end-turn proof. Only a final done/error frame detaches the stream.
    // The network observer never emits chat.completed; request-v6 owns it.
  }

  async function onNetwork(detail) {
    const phase = String(detail?.phase || "");
    const row = binding(detail?.stream_id, phase === "response");
    if (!row) return;
    if (phase === "response") {
      const active = activeRequest();
      if (active?.requestId === row.requestId && detail.event_stream === true) active.networkStreamSeen = true;
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
      if (detail.completion_hint === true) await terminalHint(row, detail);
      else await diagnostics(row, { ...detail, phase: "unverified-terminal-ignored" });
      return;
    }
    if (phase === "done") {
      row.chunks = Number(detail.chunks || row.chunks || 0);
      row.bytes = Number(detail.bytes || row.bytes || 0);
      if (row.terminalSeen) {
        await diagnostics(row, { ...detail, phase: "done-after-terminal" });
      } else if (row.lastText && detail.completion_hint === true) {
        await terminalHint(row, { ...detail, text: row.lastText });
      } else {
        const active = activeRequest();
        if (active?.requestId === row.requestId) active.networkStreamEndedWithoutTerminalAt = Date.now();
        await diagnostics(row, { ...detail, phase: "done-without-assistant-terminal" });
      }
      streams.delete(row.streamId);
      return;
    }
    if (phase === "error") {
      const active = activeRequest();
      if (active?.requestId === row.requestId) active.networkStreamEndedWithoutTerminalAt = Date.now();
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
