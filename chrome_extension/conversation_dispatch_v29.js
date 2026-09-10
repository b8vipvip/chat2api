(() => {
  const KEY = "__CHAT2API_CONVERSATION_DISPATCH_V29__";
  if (globalThis[KEY]) return;

  const baseHandleServerMessage = handleServerMessage;
  const BASE_DISPATCH_KEY = "__CHAT2API_CONVERSATION_DISPATCH_V1__";
  const TERMINAL_TYPES = new Set([
    "chat.completed", "chat.error", "chat.cancelled",
    "image.completed", "image.error", "image.cancelled",
    "voice.error", "voice.cancelled",
  ]);
  const ROUTED_TYPES = new Set(["chat.request", "image.request", "voice.request", "voice.live.start"]);
  const CANCEL_TYPES = new Set(["chat.cancel", "image.cancel", "voice.cancel"]);
  const MAX_HOLD_MS = 310000;

  const state = {
    revision: 29,
    strictSerial: true,
    lanes: new Map(),
    entries: new Map(),
  };
  globalThis[KEY] = state;
  globalThis.chat2apiConversationDispatchV29 = state;

  function logicalKey(message) {
    const routing = message?.routing || {};
    const raw = String(routing.logical_api_key_id || routing.api_key_id || "").trim();
    if (!raw) return null;
    return raw.replace(/::worker\d+$/i, "");
  }

  function terminalDeferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
  }

  async function emitQueueDiagnostic(message, entry, stage) {
    const requestId = String(message?.request_id || "");
    if (!requestId) return;
    const eventType = message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics";
    const lane = state.lanes.get(entry.key);
    await trySendSocket({
      type: eventType,
      kind: message.type === "voice.request" || message.type === "voice.live.start" ? "voice" : undefined,
      request_id: requestId,
      diagnostics: {
        extension_api_fifo: "per-logical-api-terminal-fifo-v29",
        extension_api_fifo_revision: 29,
        extension_api_fifo_stage: stage,
        extension_api_fifo_key: entry.key,
        extension_api_fifo_position: entry.position,
        extension_api_fifo_depth: Number(lane?.depth || 0),
        extension_api_fifo_wait_ms: Math.max(0, Date.now() - entry.enqueuedAt),
        extension_worker_strict_serial: true,
        extension_worker_index: 1,
        extension_worker_limit: 1,
      },
    }).catch(() => {});
  }

  function finishEntry(entry, reason) {
    if (!entry || entry.finished) return;
    entry.finished = true;
    entry.terminalReason = reason || entry.terminalReason || "terminal";
    entry.terminal.resolve(entry.terminalReason);
  }

  async function waitForTerminal(entry) {
    let timer = null;
    try {
      await Promise.race([
        entry.terminal.promise,
        new Promise(resolve => {
          timer = setTimeout(() => resolve("fifo-hold-timeout"), MAX_HOLD_MS);
        }),
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async function runQueuedRequest(message, entry) {
    if (entry.cancelled) {
      finishEntry(entry, "cancelled-before-dispatch");
      return null;
    }

    entry.active = true;
    entry.startedAt = Date.now();
    message.routing = {
      ...(message.routing || {}),
      logical_api_key_id: entry.key,
      api_key_id: entry.key,
      worker_index: 1,
      worker_limit: 1,
      strict_api_fifo: true,
      fifo_revision: 29,
    };
    await emitQueueDiagnostic(message, entry, "dispatching");

    try {
      await baseHandleServerMessage(message);
    } catch (error) {
      finishEntry(entry, "dispatch-exception");
      throw error;
    }

    // conversation_dispatch removes requestTabs immediately when route allocation
    // itself fails. In that case there will be no content-script terminal event to
    // unlock this lane, so do not wait for one.
    const baseState = globalThis[BASE_DISPATCH_KEY];
    if (!(baseState?.requestTabs instanceof Map) || !baseState.requestTabs.has(entry.requestId)) {
      finishEntry(entry, "dispatch-finished-without-active-tab");
      return null;
    }

    await waitForTerminal(entry);
    return null;
  }

  function enqueue(message, key) {
    const requestId = String(message?.request_id || "");
    if (!requestId) return baseHandleServerMessage(message);
    const existing = state.entries.get(requestId);
    if (existing) return existing.task;

    const prior = state.lanes.get(key);
    const lane = prior || { tail: Promise.resolve(), depth: 0, sequence: 0 };
    lane.depth += 1;
    lane.sequence += 1;
    const entry = {
      requestId,
      key,
      position: lane.sequence,
      enqueuedAt: Date.now(),
      startedAt: null,
      active: false,
      cancelled: false,
      finished: false,
      terminalReason: null,
      terminal: terminalDeferred(),
      task: null,
    };
    state.entries.set(requestId, entry);
    state.lanes.set(key, lane);

    const task = lane.tail
      .catch(() => {})
      .then(() => runQueuedRequest(message, entry))
      .finally(() => {
        lane.depth = Math.max(0, lane.depth - 1);
        state.entries.delete(requestId);
        if (lane.depth === 0 && lane.tail === task) state.lanes.delete(key);
      });
    entry.task = task;
    lane.tail = task;
    emitQueueDiagnostic(message, entry, lane.depth > 1 ? "queued" : "ready").catch(() => {});
    return task;
  }

  async function cancelQueuedOrActive(message) {
    const requestId = String(message?.request_id || "");
    const entry = state.entries.get(requestId);
    if (!entry) return baseHandleServerMessage(message);

    if (!entry.active) {
      entry.cancelled = true;
      finishEntry(entry, "cancelled-before-dispatch");
      await trySendSocket({
        type: message.type === "image.cancel" ? "image.cancelled" : message.type === "voice.cancel" ? "voice.cancelled" : "chat.cancelled",
        request_id: requestId,
        reason: "Cancelled while waiting in the per-API FIFO queue",
        diagnostics: {
          extension_api_fifo: "per-logical-api-terminal-fifo-v29",
          extension_api_fifo_revision: 29,
          extension_api_fifo_cancelled_while_queued: true,
        },
      }).catch(() => {});
      return null;
    }

    return baseHandleServerMessage(message);
  }

  handleServerMessage = async function handleStrictApiFifoServerMessage(message) {
    if (CANCEL_TYPES.has(message?.type)) return cancelQueuedOrActive(message);
    if (!ROUTED_TYPES.has(message?.type)) return baseHandleServerMessage(message);
    const key = logicalKey(message);
    if (!key) return baseHandleServerMessage(message);
    return enqueue(message, key);
  };

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!TERMINAL_TYPES.has(event.type)) return false;
    const requestId = String(event.request_id || "");
    const entry = state.entries.get(requestId);
    if (entry) finishEntry(entry, event.type);
    return false;
  });
})();