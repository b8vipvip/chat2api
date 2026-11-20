(() => {
  const KEY = "__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chat2apiWorkerTransportOutboxV47";
  const RETENTION_MS = 15 * 60 * 1000;
  const MAX_EVENTS = 12;
  const TERMINAL_TYPES = new Set([
    "chat.completed", "chat.error", "chat.cancelled",
    "image.completed", "image.error", "image.cancelled",
  ]);
  const state = {
    outbox: new Map(),
    restoring: true,
    flushing: false,
    flushTimer: null,
  };
  globalThis[KEY] = state;

  const baseTrySendSocket = trySendSocket;
  const baseHandleServerMessage = handleServerMessage;

  handleServerMessage = async function handleServerMessageWithTerminalAck(message) {
    if (message?.type === "chat.terminal.ack") {
      const requestId = String(message.request_id || "");
      const terminalType = String(message.terminal_type || "");
      const key = `${requestId}:${terminalType}`;
      // A negative acknowledgement is a rejection, not permission to forget
      // the only durable copy of the Worker terminal.
      if (message.accepted === true && state.outbox.delete(key)) await persist();
      else if (state.outbox.has(key)) scheduleFlush(2500);
      return;
    }
    return baseHandleServerMessage(message);
  };

  const recoverableTerminal = payload => {
    const type = String(payload?.type || "");
    const requestId = String(payload?.request_id || "");
    return Boolean(requestId) && TERMINAL_TYPES.has(type);
  };

  const eventKey = payload => `${String(payload?.request_id || "")}:${String(payload?.type || "")}`;

  async function persist() {
    const events = [...state.outbox.values()]
      .sort((a, b) => Number(a.queued_at_ms || 0) - Number(b.queued_at_ms || 0))
      .slice(-MAX_EVENTS);
    try {
      await chrome.storage.local.set({[STORAGE_KEY]: events});
      return true;
    } catch {
      return false;
    }
  }

  async function restore() {
    try {
      const saved = await chrome.storage.local.get({[STORAGE_KEY]: []});
      const now = Date.now();
      for (const raw of Array.isArray(saved?.[STORAGE_KEY]) ? saved[STORAGE_KEY] : []) {
        const payload = raw?.payload;
        const queuedAt = Number(raw?.queued_at_ms || 0);
        if (!recoverableTerminal(payload) || !queuedAt || now - queuedAt > RETENTION_MS) continue;
        state.outbox.set(eventKey(payload), {
          payload,
          queued_at_ms: queuedAt,
          last_sent_at_ms: Number(raw?.last_sent_at_ms || 0),
          attempts: Math.max(0, Number(raw?.attempts || 0)),
        });
      }
      await persist();
    } finally {
      state.restoring = false;
      scheduleFlush(0);
    }
  }

  async function queueTerminal(payload) {
    if (!recoverableTerminal(payload)) return false;
    const copy = JSON.parse(JSON.stringify(payload));
    const previous = new Map(state.outbox);
    const key = eventKey(copy);
    const existing = state.outbox.get(key);
    state.outbox.set(key, {
      payload: copy,
      queued_at_ms: Number(existing?.queued_at_ms || Date.now()),
      last_sent_at_ms: Number(existing?.last_sent_at_ms || 0),
      attempts: Math.max(0, Number(existing?.attempts || 0)),
    });
    if (state.outbox.size > MAX_EVENTS || !(await persist())) {
      state.outbox.clear();
      for (const [previousKey, item] of previous) state.outbox.set(previousKey, item);
      return false;
    }
    return true;
  }

  async function flush() {
    if (state.restoring || state.flushing || !state.outbox.size || !socketReady()) return false;
    state.flushing = true;
    try {
      for (const [key, item] of [...state.outbox.entries()]) {
        if (Date.now() - Number(item.queued_at_ms || 0) > RETENTION_MS) {
          state.outbox.delete(key);
          continue;
        }
        const now = Date.now();
        if (now - Number(item.last_sent_at_ms || 0) < 2500) continue;
        const payload = {
          ...item.payload,
          diagnostics: {
            ...(item.payload?.diagnostics || {}),
            worker_transport_replayed: true,
            worker_transport_recovery: "transport-outbox-v47",
            worker_transport_replay_attempt: Number(item.attempts || 0) + 1,
          },
        };
        const sent = await baseTrySendSocket(payload).catch(() => false);
        if (!sent) break;
        // Socket.send only proves local enqueue. Keep the terminal durable
        // until the server explicitly acknowledges its request/type pair.
        const current = state.outbox.get(key);
        if (current) {
          current.last_sent_at_ms = now;
          current.attempts = Number(current.attempts || 0) + 1;
          state.outbox.set(key, current);
        }
        await persist();
      }
      await persist();
      return state.outbox.size === 0;
    } finally {
      state.flushing = false;
    }
  }

  function scheduleFlush(delay = 50) {
    clearTimeout(state.flushTimer);
    state.flushTimer = setTimeout(() => flush().catch(() => {}), Math.max(0, Number(delay || 0)));
  }

  // All current background modules call this shared global helper. A terminal
  // event produced while the WebSocket is briefly down is acknowledged locally
  // only after it has been durably queued, then replayed on the replacement
  // socket. Non-terminal traffic keeps the original best-effort semantics.
  trySendSocket = async function trySendSocketWithWorkerRecovery(payload) {
    if (recoverableTerminal(payload)) {
      // Persist before the first send. A successful WebSocket.send() is not
      // an application-level acknowledgement from the server. If storage
      // fails, report failure instead of claiming the terminal is recoverable.
      if (!(await queueTerminal(payload))) return false;
      scheduleFlush(0);
      return true;
    }
    const sent = await baseTrySendSocket(payload).catch(() => false);
    if (sent && state.outbox.size) scheduleFlush(0);
    return sent;
  };

  // A connected socket sends status/heartbeat traffic regularly; the wrapped
  // trySendSocket above schedules a flush on the first successful send. This
  // slow fallback also covers unusual reconnects with no immediate status event.
  setInterval(() => {
    if (state.outbox.size && socketReady()) scheduleFlush(0);
  }, 1500);

  restore().catch(() => { state.restoring = false; });
})();
