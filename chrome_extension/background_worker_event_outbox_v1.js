(() => {
  const KEY = "__CHAT2API_BACKGROUND_WORKER_EVENT_OUTBOX_V1__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chat2apiWorkerEventOutboxV1";
  const RETENTION_MS = 15 * 60 * 1000;
  const RETRY_MS = 2500;
  const MAX_EVENTS = 512;
  const MAX_BYTES = 8 * 1024 * 1024;
  const EVENT_TYPES = new Set([
    "chat.delta", "chat.snapshot", "chat.references",
    "chat.tool.call", "chat.tool.result",
  ]);
  const state = {
    events: new Map(),
    restoring: true,
    flushing: false,
    flushTimer: null,
    enqueueChain: Promise.resolve(),
    pendingEnqueues: new Map(),
    nextOrder: 1,
    serverAckNegotiated: false,
    serverAckSupported: false,
  };
  globalThis[KEY] = state;

  const baseTrySendSocket = trySendSocket;
  const baseHandleServerMessage = handleServerMessage;

  // Serialize every mutation of the stored queue, including restoration,
  // ACK removal and retry bookkeeping. A delayed storage write must never
  // overwrite a later committed snapshot or reorder pre-restart events.
  function serialized(operation) {
    const result = state.enqueueChain.then(operation);
    state.enqueueChain = result.catch(() => {});
    return result;
  }

  function stableEventIdentity(payload, allocate = false) {
    const requestId = String(payload?.request_id || "").trim();
    if (!requestId || requestId.length > 128) return null;
    let eventId = String(payload?.worker_event_id || "").trim();
    if (!eventId && payload?.type === "chat.tool.call") {
      const callId = String(payload.call_id || payload.id || payload.tool_call_id || "").trim();
      if (callId) eventId = `${requestId.slice(0, 64)}:tool-call:${callId.slice(0, 48)}`;
    }
    if (!eventId && payload?.type === "chat.tool.result") {
      const resultId = String(payload.result_id || payload.call_id || payload.id || "").trim();
      if (resultId) eventId = `${requestId.slice(0, 64)}:tool-result:${resultId.slice(0, 48)}`;
    }
    // request-v6 and older content controllers emit deltas without an ID.
    // Allocate an identity once, before the first durable write; every
    // transport retry uses the stored value (equal text is not an identity).
    if (!eventId && allocate) eventId = `evt:${crypto.randomUUID()}`;
    if (!/^[A-Za-z0-9._:-]{1,128}$/.test(eventId)) return null;
    return {requestId, eventId, key: `${requestId}:${eventId}`};
  }

  function normalizePayload(payload, allocate = false) {
    if (!payload || typeof payload !== "object" || !EVENT_TYPES.has(String(payload.type || ""))) {
      return null;
    }
    const identity = stableEventIdentity(payload, allocate);
    if (!identity) return null;
    let copy;
    try { copy = JSON.parse(JSON.stringify(payload)); } catch { return null; }
    copy.request_id = identity.requestId;
    copy.worker_event_id = identity.eventId;
    return {payload:copy, ...identity};
  }

  function orderedRows() {
    return [...state.events.values()].sort((a, b) =>
      Number(a.order || 0) - Number(b.order || 0)
    );
  }

  const utf8Encoder = new TextEncoder();

  async function persist() {
    const rows = orderedRows();
    if (rows.length > MAX_EVENTS) return false;
    let bytes = 0;
    try {
      for (const row of rows) bytes += utf8Encoder.encode(JSON.stringify(row.payload)).length;
    } catch { return false; }
    if (bytes > MAX_BYTES) return false;
    try {
      await chrome.storage.local.set({[STORAGE_KEY]: rows});
      return true;
    } catch {
      return false;
    }
  }

  async function queueWorkerEvent(payload) {
    const normalized = normalizePayload(payload, true);
    if (!normalized) return "untracked";
    const requestId = normalized.requestId;
    state.pendingEnqueues.set(requestId, (state.pendingEnqueues.get(requestId) || 0) + 1);
    try {
      return await serialized(async () => {
        const serialized = JSON.stringify(normalized.payload);
        const existing = state.events.get(normalized.key);
        if (existing) {
          if (JSON.stringify(existing.payload) !== serialized) return "conflict";
          scheduleFlush(0);
          return "queued";
        }
        const row = {
          payload: normalized.payload,
          queued_at_ms: Date.now(),
          last_sent_at_ms: 0,
          attempts: 0,
          order: state.nextOrder++,
        };
        state.events.set(normalized.key, row);
        if (!(await persist())) {
          state.events.delete(normalized.key);
          state.nextOrder = Math.max(1, state.nextOrder - 1);
          return "unpersisted";
        }
        scheduleFlush(0);
        return "queued";
      });
    } finally {
      const pending = state.pendingEnqueues.get(requestId) || 0;
      if (pending > 1) state.pendingEnqueues.set(requestId, pending - 1);
      else state.pendingEnqueues.delete(requestId);
      globalThis.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__?.scheduleFlush?.(0);
    }
  }

  async function restore() {
    try {
      const saved = await chrome.storage.local.get({[STORAGE_KEY]: []});
      const now = Date.now();
      for (const raw of Array.isArray(saved?.[STORAGE_KEY]) ? saved[STORAGE_KEY] : []) {
        const normalized = normalizePayload(raw?.payload);
        const queuedAt = Number(raw?.queued_at_ms || 0);
        if (!normalized || !queuedAt || now - queuedAt > RETENTION_MS) continue;
        if (state.events.has(normalized.key)) continue;
        const order = Math.max(1, Number(raw?.order || state.nextOrder++));
        state.nextOrder = Math.max(state.nextOrder, order + 1);
        state.events.set(normalized.key, {
          payload: normalized.payload,
          queued_at_ms: queuedAt,
          last_sent_at_ms: Math.max(0, Number(raw?.last_sent_at_ms || 0)),
          attempts: Math.max(0, Number(raw?.attempts || 0)),
          order,
        });
      }
      await persist();
    } finally {
      state.restoring = false;
      scheduleFlush(0);
    }
  }

  function flush() {
    return serialized(flushSerialized);
  }

  async function flushSerialized() {
    if (
      state.restoring || !state.serverAckSupported
      || state.flushing || !state.events.size || !socketReady()
    ) return false;
    state.flushing = true;
    let sentAny = false;
    let nextWait = RETRY_MS;
    try {
      const rows = [...state.events.entries()].sort((left, right) =>
        Number(left[1].order || 0) - Number(right[1].order || 0)
      );
      const consideredRequests = new Set();
      for (const [key, row] of rows) {
        const requestId = String(row.payload?.request_id || "");
        if (!requestId || consideredRequests.has(requestId)) continue;
        consideredRequests.add(requestId);
        if (Date.now() - Number(row.queued_at_ms || 0) > RETENTION_MS) {
          state.events.delete(key);
          continue;
        }
        const now = Date.now();
        const wait = RETRY_MS - (now - Number(row.last_sent_at_ms || 0));
        if (wait > 0) {
          nextWait = Math.min(nextWait, wait);
          continue;
        }

        // One in-flight event per request preserves its delta order, while
        // independent requests can progress without waiting on one another.
        row.last_sent_at_ms = now;
        row.attempts = Number(row.attempts || 0) + 1;
        await persist();
        const payload = {
          ...row.payload,
          diagnostics: {
            ...(row.payload?.diagnostics || {}),
            worker_event_transport: "durable-outbox-v1",
            worker_event_transport_attempt: row.attempts,
            worker_event_transport_replayed: row.attempts > 1,
          },
        };
        const sent = await baseTrySendSocket(payload).catch(() => false);
        if (!sent) {
          const current = state.events.get(key);
          if (current === row) {
            row.last_sent_at_ms = 0;
            await persist();
          }
        } else {
          sentAny = true;
        }
      }
      await persist();
      // ACK schedules the next event for that request. This timer retries
      // events whose ACK was lost and services the next independent request.
      if (state.events.size) scheduleFlush(nextWait);
      // A pending terminal may have been waiting on an event that just expired.
      globalThis.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__?.scheduleFlush?.(0);
      return sentAny;
    } finally {
      state.flushing = false;
    }
  }

  function scheduleFlush(delay = 50) {
    clearTimeout(state.flushTimer);
    state.flushTimer = setTimeout(
      () => flush().catch(() => {}),
      Math.max(0, Number(delay || 0)),
    );
  }

  handleServerMessage = async function handleServerMessageWithWorkerEventAck(message) {
    if (message?.type === "server.hello") {
      state.serverAckNegotiated = true;
      state.serverAckSupported = Array.isArray(message.capabilities)
        && message.capabilities.includes("worker_event_ack_v1");
      const supportsAck = state.serverAckSupported;
      await serialized(async () => {
        if (!supportsAck && state.events.size) {
          // A rollback/older server cannot ACK this protocol. Drop the stale
          // event queue and let the existing terminal outbox deliver finality.
          state.events.clear();
          await persist();
        } else if (supportsAck) {
          scheduleFlush(0);
        }
        globalThis.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__?.scheduleFlush?.(0);
      });
      return baseHandleServerMessage(message);
    }
    if (message?.type === "chat.event.ack") {
      const identity = stableEventIdentity({
        type: "chat.delta",
        request_id: message.request_id,
        worker_event_id: message.worker_event_id,
      });
      if (!identity) return;
      return serialized(async () => {
        const row = state.events.get(identity.key);
        if (!row) return;
        if (message.accepted === true || message.discarded === true) {
          state.events.delete(identity.key);
          await persist();
          scheduleFlush(0);
          // A terminal must not overtake any earlier durable Worker deltas.
          globalThis.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__?.scheduleFlush?.(0);
        } else {
          row.last_sent_at_ms = 0;
          await persist();
          scheduleFlush(RETRY_MS);
        }
      });
    }
    return baseHandleServerMessage(message);
  };

  trySendSocket = async function trySendSocketWithDurableWorkerEvents(payload) {
    if (
      state.serverAckSupported
      && EVENT_TYPES.has(String(payload?.type || ""))
    ) {
      const result = await queueWorkerEvent(payload);
      if (result === "queued") return true;
      // Never bypass an earlier queued event: a direct send here could
      // overtake the request's in-flight delta and corrupt text offsets.
      // If the bounded durable queue is unavailable/full, fail this auxiliary
      // event closed; the authoritative terminal still travels via its own
      // durable outbox.
      return false;
    }
    return baseTrySendSocket(payload).catch(() => false);
  };

  setInterval(() => {
    if (state.serverAckSupported && state.events.size && socketReady()) scheduleFlush(0);
  }, 1500);

  serialized(restore).catch(() => { state.restoring = false; });
})();
