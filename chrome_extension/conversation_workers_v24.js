(() => {
  const KEY = "__CHAT2API_CONVERSATION_WORKERS_V24__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const MAX_WORKERS_PER_KEY = 3; // Historical/default contract; runtime value is server-driven.
  const MIN_WORKERS_PER_KEY = 1;
  const HARD_MAX_WORKERS_PER_KEY = 32;
  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver !== "function") return;

  const state = {
    requestRoutes: new Map(),
    maxWorkers: MAX_WORKERS_PER_KEY,
  };
  globalThis[KEY] = state;
  globalThis.chat2apiConversationWorkersV24 = state;

  function logicalKey(message) {
    const routing = message?.routing || {};
    const value = routing.logical_api_key_id || routing.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  function workerLimit(message) {
    const supplied = Number(message?.routing?.worker_limit);
    const value = Number.isFinite(supplied) && supplied > 0 ? Math.floor(supplied) : Number(state.maxWorkers || MAX_WORKERS_PER_KEY);
    const limited = Math.max(MIN_WORKERS_PER_KEY, Math.min(HARD_MAX_WORKERS_PER_KEY, value || MAX_WORKERS_PER_KEY));
    state.maxWorkers = limited;
    return limited;
  }

  function workerRouteKey(baseKey, index) {
    return index <= 1 ? baseKey : `${baseKey}::worker${index}`;
  }

  function routeIsBusy(router, route, requestId) {
    const inflight = String(route?.inflight_request_id || "");
    if (!inflight || inflight === requestId) return false;
    const active = router?.activeRequests;
    if (active instanceof Map && !active.has(inflight)) {
      route.inflight_request_id = null;
      return false;
    }
    return true;
  }

  async function chooseWorker(message) {
    const requestId = String(message?.request_id || "");
    const baseKey = logicalKey(message);
    if (!baseKey || !requestId) return null;

    const existing = state.requestRoutes.get(requestId);
    if (existing) return existing;

    const limit = workerLimit(message);
    const router = globalThis[ROUTER_KEY];
    const routes = router?.routes && typeof router.routes === "object" ? router.routes : {};
    for (let index = 1; index <= limit; index += 1) {
      const routeKey = workerRouteKey(baseKey, index);
      const route = routes[routeKey];
      if (routeIsBusy(router, route, requestId)) continue;
      const selected = {
        requestId,
        baseKey,
        routeKey,
        workerIndex: index,
        workerLimit: limit,
        tabId: null,
        windowId: null,
      };
      state.requestRoutes.set(requestId, selected);
      return selected;
    }
    throw new Error(`All ${limit} conversation workers are busy for this API key`);
  }

  state.requestTarget = function requestTarget(requestId) {
    return state.requestRoutes.get(String(requestId || "")) || null;
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
          extension_worker_router: "per-api-key-v24-server-limit",
          extension_worker_index: selected.workerIndex,
          extension_worker_limit: selected.workerLimit,
          extension_worker_route_key: selected.routeKey,
          extension_worker_logical_api_key_id: selected.baseKey,
          extension_worker_limit_source: Number(message?.routing?.worker_limit) > 0 ? "server-routing" : "default",
          routed_tab_id: selected.tabId,
          routed_window_id: selected.windowId,
        },
      }).catch(() => {});
      return tab;
    } catch (error) {
      state.requestRoutes.delete(selected.requestId);
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
    if (requestId) state.requestRoutes.delete(requestId);
    return false;
  });
})();
