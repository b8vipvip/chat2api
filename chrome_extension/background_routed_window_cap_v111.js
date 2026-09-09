(() => {
  const KEY = "__CHAT2API_ROUTED_WINDOW_CAP_V111__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const WORKERS_KEY = "__CHAT2API_CONVERSATION_WORKERS_V25__";
  const MANAGER_KEY = "__CHAT2API_WINDOW_MANAGER_V88__";
  const RESERVE_KEY = "__CHAT2API_RESERVE_POOL_V29__";
  const ROUTE_STORAGE_KEY = "chat2apiConversationRoutesV1";
  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";
  const ALARM_NAME = "chat2api-routed-window-cap-v111";

  const state = {
    version: 111,
    reconcileInFlight: null,
    lastResult: null,
  };
  globalThis[KEY] = state;

  function concurrencyCap() {
    const workers = globalThis[WORKERS_KEY];
    const parsed = Number(workers?.maxWorkers || 0);
    if (!Number.isFinite(parsed) || parsed < 1) return null;
    return Math.max(1, Math.min(32, Math.floor(parsed)));
  }

  function activeWindowIds(router) {
    const active = new Set();
    for (const route of Object.values(router?.routes || {})) {
      if (route?.inflight_request_id && Number.isInteger(route?.window_id)) active.add(route.window_id);
    }
    if (router?.activeRequests instanceof Map) {
      for (const request of router.activeRequests.values()) {
        const windowId = Number(request?.window_id);
        if (Number.isInteger(windowId)) active.add(windowId);
      }
    }
    return active;
  }

  async function liveWindow(windowId) {
    if (!Number.isInteger(windowId)) return false;
    try {
      await chrome.windows.get(windowId);
      return true;
    } catch (_) {
      return false;
    }
  }

  async function persistRoutes(router) {
    if (!router?.routes || typeof router.routes !== "object") return;
    await chrome.storage.local.set({ [ROUTE_STORAGE_KEY]: router.routes }).catch(() => {});
  }

  async function closeIdleRoute(router, route) {
    const windowId = Number(route?.window_id);
    for (const [key, candidate] of Object.entries(router?.routes || {})) {
      if (Number(candidate?.window_id) === windowId) delete router.routes[key];
    }
    try { await chrome.alarms.clear(`${ROUTE_ALARM_PREFIX}${windowId}`); } catch (_) {}
    const manager = globalThis[MANAGER_KEY];
    if (manager?.protectedUntil instanceof Map && Number.isInteger(windowId)) {
      manager.protectedUntil.delete(windowId);
    }
    if (Number.isInteger(windowId) && await liveWindow(windowId)) {
      try { await chrome.windows.remove(windowId); } catch (_) {}
    }
  }

  async function reconcile(reason = "scheduled") {
    if (state.reconcileInFlight) return state.reconcileInFlight;
    state.reconcileInFlight = (async () => {
      const router = globalThis[ROUTER_KEY];
      const cap = concurrencyCap();
      if (!router?.routes || typeof router.routes !== "object" || !cap) return null;

      const active = activeWindowIds(router);
      const byWindow = new Map();
      for (const route of Object.values(router.routes)) {
        const windowId = Number(route?.window_id);
        if (route?.window_owned === false || !Number.isInteger(windowId)) continue;
        const current = byWindow.get(windowId);
        if (!current || Number(route?.last_active_at || 0) > Number(current?.last_active_at || 0)) {
          byWindow.set(windowId, route);
        }
      }

      const routedCount = byWindow.size;
      const excess = Math.max(0, routedCount - cap);
      if (!excess) {
        state.lastResult = { reason, cap, routed_before: routedCount, routed_after: routedCount, closed: 0, active: active.size, at: Date.now() };
        return state.lastResult;
      }

      const idle = [...byWindow.entries()]
        .filter(([windowId, route]) => !active.has(windowId) && !route?.inflight_request_id)
        .sort((left, right) => Number(left[1]?.last_active_at || 0) - Number(right[1]?.last_active_at || 0));
      let closed = 0;
      for (const [, route] of idle) {
        if (closed >= excess) break;
        await closeIdleRoute(router, route);
        closed += 1;
      }
      if (closed) await persistRoutes(router);

      const reserve = globalThis[RESERVE_KEY];
      if (reserve && typeof reserve.reconcile === "function") {
        await reserve.reconcile().catch(() => null);
      }
      if (reserve && typeof reserve.report === "function") {
        await reserve.report(true).catch(() => null);
      }
      state.lastResult = {
        reason,
        cap,
        routed_before: routedCount,
        routed_after: Math.max(active.size, routedCount - closed),
        closed,
        active: active.size,
        deferred: Math.max(0, excess - closed),
        at: Date.now(),
      };
      return state.lastResult;
    })().finally(() => { state.reconcileInFlight = null; });
    return state.reconcileInFlight;
  }

  state.reconcile = reconcile;

  const capacity = globalThis.__CHAT2API_CAPACITY_CONTROL_V35__;
  if (capacity && typeof capacity.handle === "function" && capacity.handle.__chat2apiRoutedWindowCapV111 !== true) {
    const baseHandle = capacity.handle.bind(capacity);
    const wrappedHandle = async message => {
      const result = await baseHandle(message);
      if (String(message?.action || "") === "workers.resize") await reconcile("workers.resize").catch(() => null);
      return result;
    };
    wrappedHandle.__chat2apiRoutedWindowCapV111 = true;
    capacity.handle = wrappedHandle;
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const type = String(message?.event?.type || "");
    if (["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(type)) {
      setTimeout(() => reconcile(type).catch(() => {}), 80);
    }
    return false;
  });

  chrome.alarms.create(ALARM_NAME, { periodInMinutes: 1 });
  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm?.name === ALARM_NAME) reconcile("alarm").catch(() => {});
  });
  setTimeout(() => reconcile("startup").catch(() => {}), 250);
})();
