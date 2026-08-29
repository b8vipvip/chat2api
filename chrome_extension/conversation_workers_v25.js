(() => {
  const KEY = "__CHAT2API_CONVERSATION_WORKERS_V25__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const RESERVE_KEY = "__CHAT2API_RESERVE_POOL_V29__";
  const MAX_WORKERS_PER_KEY = 3;
  const MIN_WORKERS_PER_KEY = 1;
  const HARD_MAX_WORKERS_PER_KEY = 32;
  const ROUTE_RESERVATION_STALE_MS = 30000;
  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver !== "function") return;

  const state = {
    requestRoutes: new Map(),
    routeReservations: new Map(),
    maxWorkers: MAX_WORKERS_PER_KEY,
    lastAuthoritativeLimitSource: null,
    lastLimitSource: "default",
  };
  globalThis[KEY] = state;
  globalThis.chat2apiConversationWorkersV25 = state;
  // Keep the historical alias for diagnostic/admin code that only knows v24.
  globalThis.chat2apiConversationWorkersV24 = state;

  function logicalKey(message) {
    const routing = message?.routing || {};
    const value = routing.logical_api_key_id || routing.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  function normalizeWorkerLimit(value, fallback = MAX_WORKERS_PER_KEY) {
    const parsed = Number(value);
    const candidate = Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
    return Math.max(MIN_WORKERS_PER_KEY, Math.min(HARD_MAX_WORKERS_PER_KEY, candidate || MAX_WORKERS_PER_KEY));
  }

  function runtimeConfigLimit() {
    const reserve = globalThis[RESERVE_KEY];
    const refreshedAt = Number(reserve?.configRefreshedAt || 0);
    const target = Number(reserve?.target);
    if (!refreshedAt || !Number.isFinite(target) || target <= 0) return null;
    return normalizeWorkerLimit(target);
  }

  function workerLimitConfig(message) {
    const supplied = Number(message?.routing?.worker_limit);
    if (Number.isFinite(supplied) && supplied > 0) {
      const limit = normalizeWorkerLimit(supplied);
      state.maxWorkers = limit;
      state.lastAuthoritativeLimitSource = "server-routing";
      state.lastLimitSource = "server-routing";
      return { limit, source: "server-routing" };
    }

    const runtimeLimit = runtimeConfigLimit();
    if (runtimeLimit) {
      state.maxWorkers = runtimeLimit;
      state.lastAuthoritativeLimitSource = "runtime-config";
      state.lastLimitSource = "runtime-config";
      return { limit: runtimeLimit, source: "runtime-config" };
    }

    const limit = normalizeWorkerLimit(state.maxWorkers);
    const source = state.lastAuthoritativeLimitSource
      ? `cached-${state.lastAuthoritativeLimitSource}`
      : "default";
    state.lastLimitSource = source;
    return { limit, source };
  }

  function workerLimit(message) {
    return workerLimitConfig(message).limit;
  }

  function workerRouteKey(baseKey, index) {
    return index <= 1 ? baseKey : `${baseKey}::worker${index}`;
  }

  function reservationBusy(router, routeKey, requestId) {
    const reservation = state.routeReservations.get(routeKey);
    if (!reservation || reservation.requestId === requestId) return false;
    const active = router?.activeRequests;
    if (active instanceof Map && active.has(reservation.requestId)) return true;
    if (Date.now() - Number(reservation.reservedAt || 0) < ROUTE_RESERVATION_STALE_MS) return true;
    state.routeReservations.delete(routeKey);
    return false;
  }

  function routeIsBusy(router, route, routeKey, requestId) {
    if (reservationBusy(router, routeKey, requestId)) return true;
    const inflight = String(route?.inflight_request_id || "");
    if (!inflight || inflight === requestId) return false;
    const active = router?.activeRequests;
    if (active instanceof Map && !active.has(inflight)) {
      route.inflight_request_id = null;
      return false;
    }
    return true;
  }

  function reserve(selected) {
    state.routeReservations.set(selected.routeKey, {
      requestId: selected.requestId,
      reservedAt: Date.now(),
    });
  }

  function releaseReservation(selected) {
    if (!selected) return;
    const reservation = state.routeReservations.get(selected.routeKey);
    if (reservation?.requestId === selected.requestId) state.routeReservations.delete(selected.routeKey);
  }

  async function chooseWorker(message) {
    const requestId = String(message?.request_id || "");
    const baseKey = logicalKey(message);
    if (!baseKey || !requestId) return null;

    const existing = state.requestRoutes.get(requestId);
    if (existing) return existing;

    const limit = workerLimit(message);
    const limitSource = state.lastLimitSource;
    const router = globalThis[ROUTER_KEY];
    const routes = router?.routes && typeof router.routes === "object" ? router.routes : {};
    for (let index = 1; index <= limit; index += 1) {
      const routeKey = workerRouteKey(baseKey, index);
      const route = routes[routeKey];
      if (routeIsBusy(router, route, routeKey, requestId)) continue;
      const selected = {
        requestId,
        baseKey,
        routeKey,
        workerIndex: index,
        workerLimit: limit,
        workerLimitSource: limitSource,
        tabId: null,
        windowId: null,
      };
      // Reserve synchronously before awaiting tab allocation. This is the key v25
      // guarantee: two simultaneous requests with byte-identical prompts but
      // different request_ids cannot both observe the same worker route as free.
      state.requestRoutes.set(requestId, selected);
      reserve(selected);
      return selected;
    }
    throw new Error(`All ${limit} conversation workers are busy for this API key`);
  }

  state.requestTarget = function requestTarget(requestId) {
    return state.requestRoutes.get(String(requestId || "")) || null;
  };
  state.workerLimitConfig = workerLimitConfig;
  state.releaseRequest = function releaseRequest(requestId) {
    const key = String(requestId || "");
    const selected = state.requestRoutes.get(key);
    releaseReservation(selected);
    state.requestRoutes.delete(key);
  };

  globalThis.resolveTargetTabForRequest = async function resolveConcurrentWorker(message) {
    const selected = await chooseWorker(message);
    if (!selected) return baseResolver(message);

    message.routing = {
      ...(message.routing || {}),
      logical_api_key_id: selected.baseKey,
      api_key_id: selected.routeKey,
      worker_index: selected.workerIndex,
      worker_limit: selected.workerLimit,
    };

    try {
      const tab = await baseResolver(message);
      selected.tabId = tab?.id ?? null;
      selected.windowId = tab?.windowId ?? null;
      const eventType = message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics";
      await trySendSocket({
        type: eventType,
        kind: message.type === "voice.request" || message.type === "voice.live.start" ? "voice" : undefined,
        request_id: requestIdOf(message),
        diagnostics: {
          extension_worker_router: "per-api-key-v25-request-reservation",
          extension_worker_index: selected.workerIndex,
          extension_worker_limit: selected.workerLimit,
          extension_worker_route_key: selected.routeKey,
          extension_worker_logical_api_key_id: selected.baseKey,
          extension_worker_limit_source: selected.workerLimitSource,
          extension_worker_request_reservation: true,
          routed_tab_id: selected.tabId,
          routed_window_id: selected.windowId,
        },
      }).catch(() => {});
      return tab;
    } catch (error) {
      state.releaseRequest(selected.requestId);
      throw error;
    }
  };

  function requestIdOf(message) {
    return String(message?.request_id || "");
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(event.type)) return false;
    const requestId = String(event.request_id || "");
    if (requestId) state.releaseRequest(requestId);
    return false;
  });
})();
