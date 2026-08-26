(() => {
  const KEY = "__CHAT2API_BACKGROUND_REQUEST_RECOVERY_V40__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const DISPATCH_KEY = "__CHAT2API_CONVERSATION_DISPATCH_V1__";
  const STORAGE_KEY = "chat2apiConversationRoutesV1";
  const CANCEL_RECYCLE_DELAY_MS = 2500;
  const TERMINAL_RECYCLE_DELAY_MS = 250;
  const state = {
    recycled: new Set(),
    terminalSeen: new Set(),
    pending: new Map(),
    recycleRequest: null,
  };
  globalThis[KEY] = state;

  async function persistRoutes(router) {
    if (!router?.routes || typeof router.routes !== "object") return;
    await chrome.storage.local.set({ [STORAGE_KEY]: router.routes }).catch(() => {});
  }

  function routeEntryForRequest(router, requestId) {
    if (!router?.routes || !requestId) return null;
    const active = router.activeRequests?.get?.(requestId);
    if (active?.key && router.routes[active.key]) {
      return { key: active.key, route: router.routes[active.key] };
    }
    for (const [key, route] of Object.entries(router.routes)) {
      if (String(route?.inflight_request_id || "") === requestId) return { key, route };
    }
    return null;
  }

  async function recycleRequest(requestId, reason) {
    requestId = String(requestId || "");
    if (!requestId || state.recycled.has(requestId)) return false;

    const router = globalThis[ROUTER_KEY];
    const dispatch = globalThis[DISPATCH_KEY];
    const routedTarget = dispatch?.requestTabs?.get?.(requestId) || null;
    const entry = routeEntryForRequest(router, requestId);
    const route = entry?.route || null;
    const windowId = Number.isInteger(route?.window_id)
      ? route.window_id
      : (Number.isInteger(routedTarget?.windowId) ? routedTarget.windowId : null);

    if (!route && !Number.isInteger(windowId)) return false;
    state.recycled.add(requestId);

    if (route) {
      const hadSession = Boolean(
        route.conversation_id || route.conversation_url || Number(route.turn_count || 0) ||
        Number(route.text_chars || 0) || Number(route.attachment_count || 0) ||
        Number.isInteger(route.tab_id) || Number.isInteger(route.window_id)
      );
      route.conversation_id = null;
      route.conversation_url = null;
      route.turn_count = 0;
      route.text_chars = 0;
      route.attachment_count = 0;
      route.slow_load_strikes = 0;
      route.last_open_ms = null;
      route.tab_id = null;
      route.window_id = null;
      route.window_owned = true;
      route.inflight_request_id = null;
      route.close_after = null;
      route.last_active_at = Date.now();
      route.last_rotation_reason = reason || "terminal-request-recycle";
      if (hadSession) route.generation = Number(route.generation || 1) + 1;
      router.activeRequests?.delete?.(requestId);
      await persistRoutes(router);
    }

    dispatch?.requestTabs?.delete?.(requestId);
    if (Number.isInteger(windowId)) {
      try { await chrome.alarms.clear(`chat2api-route-close:${windowId}`); } catch (_) {}
      try { await chrome.windows.remove(windowId); } catch (_) {}
    }

    await chrome.storage.local.set({
      chat2apiRequestRecoveryV40: {
        request_id: requestId,
        reason: String(reason || "terminal-request-recycle"),
        recycled_at_ms: Date.now(),
        window_id: windowId,
      },
    }).catch(() => {});
    return true;
  }

  function scheduleRecycle(requestId, reason, waitMs) {
    requestId = String(requestId || "");
    if (!requestId || state.recycled.has(requestId)) return;
    clearTimeout(state.pending.get(requestId));
    const timer = setTimeout(() => {
      state.pending.delete(requestId);
      recycleRequest(requestId, reason).catch(() => {});
    }, Math.max(0, Number(waitMs || 0)));
    state.pending.set(requestId, timer);
  }

  state.recycleRequest = recycleRequest;
  state.scheduleRecycle = scheduleRecycle;

  // A server-side watchdog sends chat.cancel when a generation stops making
  // progress. content_request_v5 normally answers with chat.cancelled quickly.
  // If that content path is wedged too, close the routed window from the service
  // worker so a poisoned ChatGPT transport cannot remain reusable forever.
  const baseHandleServerMessage = handleServerMessage;
  handleServerMessage = async function handleRequestRecovery(message) {
    const result = await baseHandleServerMessage(message);
    if (message?.type === "chat.cancel" && message?.request_id) {
      const requestId = String(message.request_id);
      setTimeout(async () => {
        if (state.terminalSeen.has(requestId)) return;
        const recycled = await recycleRequest(requestId, "cancel-timeout-recycle").catch(() => false);
        if (!recycled) return;
        try {
          await trySendSocket({
            type: "chat.cancelled",
            request_id: requestId,
            reason: "ChatGPT route was recycled after cancellation did not settle",
          });
        } catch (_) {}
      }, CANCEL_RECYCLE_DELAY_MS);
    }
    return result;
  };

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    const requestId = String(event.request_id || "");
    if (!requestId) return false;

    if (["chat.error", "image.error", "chat.cancelled", "image.cancelled"].includes(event.type)) {
      state.terminalSeen.add(requestId);
      scheduleRecycle(requestId, `${event.type}-recycle`, TERMINAL_RECYCLE_DELAY_MS);
    } else if (["chat.completed", "image.completed"].includes(event.type)) {
      state.terminalSeen.add(requestId);
      clearTimeout(state.pending.get(requestId));
      state.pending.delete(requestId);
      state.recycled.delete(requestId);
    }
    return false;
  });
})();
