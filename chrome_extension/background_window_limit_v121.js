(() => {
  const KEY = "__CHAT2API_ROUTED_WINDOW_LIMIT_V121__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const ROUTE_STORAGE_KEY = "chat2apiConversationRoutesV1";
  const LIMIT_STORAGE_KEY = "chat2apiRoutedWindowLimitV121";
  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";
  const TERMINAL_TYPES = new Set([
    "chat.completed", "chat.error", "chat.cancelled",
    "image.completed", "image.error", "image.cancelled",
    "voice.error", "voice.cancelled",
  ]);

  const state = {
    version: 121,
    revision: 1,
    limit: null,
    source: "unset",
    loaded: false,
    gate: Promise.resolve(),
    reservations: new Set(),
    lastResult: null,
  };
  globalThis[KEY] = state;

  function normalizeLimit(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 1 || parsed > 32) return null;
    return Math.floor(parsed);
  }

  async function ensureLoaded() {
    if (state.loaded) return;
    const stored = await chrome.storage.local.get(LIMIT_STORAGE_KEY).catch(() => ({}));
    const value = stored?.[LIMIT_STORAGE_KEY];
    if (value && typeof value === "object") {
      state.limit = normalizeLimit(value.limit);
      state.source = String(value.source || "stored");
    }
    state.loaded = true;
  }

  async function persistLimit() {
    if (!state.limit) {
      await chrome.storage.local.remove(LIMIT_STORAGE_KEY).catch(() => {});
      return;
    }
    await chrome.storage.local.set({
      [LIMIT_STORAGE_KEY]: {
        limit: state.limit,
        source: state.source,
        updated_at: new Date().toISOString(),
      },
    }).catch(() => {});
  }

  function router() {
    const value = globalThis[ROUTER_KEY];
    return value && value.routes && typeof value.routes === "object" ? value : null;
  }

  function routeKey(message) {
    const value = String(message?.routing?.api_key_id || "").trim();
    return value || null;
  }

  function windowsById(value) {
    const result = new Map();
    for (const [key, route] of Object.entries(value?.routes || {})) {
      const windowId = Number(route?.window_id);
      if (!Number.isInteger(windowId) || route?.window_owned === false) continue;
      const previous = result.get(windowId);
      if (!previous || Number(route?.last_active_at || 0) > Number(previous.route?.last_active_at || 0)) {
        result.set(windowId, { key, route });
      }
    }
    return result;
  }

  function activeWindowIds(value) {
    const active = new Set();
    for (const route of Object.values(value?.routes || {})) {
      const windowId = Number(route?.window_id);
      if (route?.inflight_request_id && Number.isInteger(windowId)) active.add(windowId);
    }
    if (value?.activeRequests instanceof Map) {
      for (const request of value.activeRequests.values()) {
        const windowId = Number(request?.window_id);
        if (Number.isInteger(windowId)) active.add(windowId);
      }
    }
    return active;
  }

  async function persistRoutes(value) {
    await chrome.storage.local.set({ [ROUTE_STORAGE_KEY]: value.routes }).catch(() => {});
  }

  async function closeIdleEntry(value, entry, reason) {
    const route = entry?.route;
    const windowId = Number(route?.window_id);
    if (!route || !Number.isInteger(windowId) || route.inflight_request_id) return false;
    try { await chrome.alarms.clear(`${ROUTE_ALARM_PREFIX}${windowId}`); } catch (_) {}
    try { await chrome.windows.remove(windowId); } catch (_) {}

    route.conversation_id = null;
    route.conversation_url = null;
    route.turn_count = 0;
    route.text_chars = 0;
    route.attachment_count = 0;
    route.slow_load_strikes = 0;
    route.last_open_ms = null;
    route.tab_id = null;
    route.window_id = null;
    route.inflight_request_id = null;
    route.close_after = null;
    route.last_active_at = Date.now();
    route.generation = Number(route.generation || 1) + 1;
    route.last_rotation_reason = reason || "worker-window-limit-evicted";
    return true;
  }

  async function reconcile(reason = "scheduled") {
    await ensureLoaded();
    const value = router();
    const limit = normalizeLimit(state.limit);
    if (!value || !limit) return null;

    const byWindow = windowsById(value);
    const active = activeWindowIds(value);
    const excess = Math.max(0, byWindow.size - limit);
    if (!excess) {
      state.lastResult = {
        ok: true,
        reason,
        limit,
        source: state.source,
        routed_before: byWindow.size,
        routed_after: byWindow.size,
        closed: 0,
        active: active.size,
        reserved: state.reservations.size,
        at: Date.now(),
      };
      return state.lastResult;
    }

    const idle = [...byWindow.values()]
      .filter(entry => !active.has(Number(entry.route?.window_id)) && !entry.route?.inflight_request_id)
      .sort((left, right) => Number(left.route?.last_active_at || 0) - Number(right.route?.last_active_at || 0));
    let closed = 0;
    for (const entry of idle) {
      if (closed >= excess) break;
      if (await closeIdleEntry(value, entry, "worker-window-limit-reconcile")) closed += 1;
    }
    if (closed) await persistRoutes(value);
    state.lastResult = {
      ok: true,
      reason,
      limit,
      source: state.source,
      routed_before: byWindow.size,
      routed_after: Math.max(active.size, byWindow.size - closed),
      closed,
      active: active.size,
      reserved: state.reservations.size,
      deferred: Math.max(0, excess - closed),
      at: Date.now(),
    };
    return state.lastResult;
  }

  async function setLimit(target, source = "explicit") {
    const limit = normalizeLimit(target);
    if (!limit) throw new Error("Window limit must be an integer between 1 and 32");
    await ensureLoaded();
    state.limit = limit;
    state.source = String(source || "explicit").slice(0, 40);
    await persistLimit();
    const result = await reconcile("limit-updated");
    return {
      ok: true,
      limit,
      source: state.source,
      reconcile: result,
    };
  }

  function serial(task) {
    const next = state.gate.catch(() => {}).then(task);
    state.gate = next.catch(() => {});
    return next;
  }

  async function reserveForRequest(message) {
    await ensureLoaded();
    const value = router();
    const limit = normalizeLimit(state.limit);
    const key = routeKey(message);
    if (!value || !limit || !key) return false;

    const existing = value.routes?.[key];
    if (Number.isInteger(existing?.window_id)) return false;
    if (state.reservations.has(key)) return false;

    return serial(async () => {
      const current = value.routes?.[key];
      if (Number.isInteger(current?.window_id) || state.reservations.has(key)) return false;
      let byWindow = windowsById(value);
      const active = activeWindowIds(value);
      const effective = byWindow.size + state.reservations.size;
      if (effective >= limit) {
        const idle = [...byWindow.values()]
          .filter(entry => entry.key !== key && !active.has(Number(entry.route?.window_id)) && !entry.route?.inflight_request_id)
          .sort((left, right) => Number(left.route?.last_active_at || 0) - Number(right.route?.last_active_at || 0));
        const victim = idle[0];
        if (!victim || !await closeIdleEntry(value, victim, "worker-window-limit-admission")) {
          const error = new Error(`Worker window limit exhausted (${effective}/${limit})`);
          error.code = "worker_window_limit_exhausted";
          error.retry_after_ms = 350;
          throw error;
        }
        await persistRoutes(value);
        byWindow = windowsById(value);
      }
      if (byWindow.size + state.reservations.size >= limit) {
        const error = new Error(`Worker window limit exhausted (${byWindow.size + state.reservations.size}/${limit})`);
        error.code = "worker_window_limit_exhausted";
        error.retry_after_ms = 350;
        throw error;
      }
      state.reservations.add(key);
      return true;
    });
  }

  async function applyRoutingLimit(message) {
    const limit = normalizeLimit(message?.routing?.worker_window_limit);
    if (!limit) return;
    const source = String(message?.routing?.worker_window_limit_source || "server");
    await ensureLoaded();
    if (state.limit === limit && state.source === source) return;
    state.limit = limit;
    state.source = source.slice(0, 40);
    await persistLimit();
    await reconcile("routing-config");
  }

  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver === "function" && !baseResolver.__chat2apiWindowLimitV121) {
    const wrappedResolver = async message => {
      await applyRoutingLimit(message);
      const key = routeKey(message);
      const reserved = await reserveForRequest(message);
      try {
        return await baseResolver(message);
      } finally {
        if (reserved && key) state.reservations.delete(key);
      }
    };
    wrappedResolver.__chat2apiWindowLimitV121 = true;
    globalThis.resolveTargetTabForRequest = wrappedResolver;
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const type = String(message?.event?.type || "");
    if (TERMINAL_TYPES.has(type)) setTimeout(() => reconcile(type).catch(() => {}), 120);
    return false;
  });

  state.setLimit = setLimit;
  state.reconcile = reconcile;
  state.snapshot = () => ({
    limit: state.limit,
    source: state.source,
    reservations: state.reservations.size,
    last_result: state.lastResult,
  });

  ensureLoaded().then(() => reconcile("startup")).catch(() => {});
})();
