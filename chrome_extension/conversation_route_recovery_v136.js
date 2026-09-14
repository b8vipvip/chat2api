(() => {
  const KEY = "__CHAT2API_CONVERSATION_ROUTE_RECOVERY_V136__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const ROUTES_STORAGE_KEY = "chat2apiConversationRoutesV1";
  const SERVER_SCHEDULER_AUTHORITY = "server-single-authority-scheduler-v58";
  const SERVER_FIFO_REVISION = 58;
  const CANCEL_TYPES = new Set(["chat.cancel", "image.cancel", "voice.cancel"]);
  const state = {
    revision: 140,
    stale_inflight_recoveries: 0,
    orphan_owner_recoveries: 0,
    server_authority_recoveries: 0,
    explicit_cancel_retirements: 0,
    last_recovery: null,
    last_cancel: null,
  };
  globalThis[KEY] = state;

  function routerState() {
    const value = globalThis[ROUTER_KEY];
    return value && typeof value === "object" ? value : null;
  }

  async function ensureRouterLoaded(router) {
    if (!router || router.loaded) return;
    const stored = await chrome.storage.local.get(ROUTES_STORAGE_KEY).catch(() => ({}));
    const value = stored?.[ROUTES_STORAGE_KEY];
    if (router.loaded) return;
    router.routes = value && typeof value === "object" && !Array.isArray(value) ? value : (router.routes || {});
    router.loaded = true;
  }

  function logicalKey(message) {
    const value = message?.routing?.logical_api_key_id || message?.routing?.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  function authoritativeServerAdmission(message) {
    return String(message?.routing?.scheduler_authority || "") === SERVER_SCHEDULER_AUTHORITY
      && Number(message?.routing?.server_api_fifo_revision || 0) >= SERVER_FIFO_REVISION;
  }

  function locallyLiveOwner(router, requestId) {
    requestId = String(requestId || "");
    if (!requestId) return false;
    const active = router?.activeRequests;
    return active instanceof Map && active.has(requestId);
  }

  async function recoverStaleOwner(message) {
    const router = routerState();
    const key = logicalKey(message);
    const requestId = String(message?.request_id || "");
    if (!router || !key || !requestId) return false;
    await ensureRouterLoaded(router);
    const route = router.routes?.[key];
    const staleRequestId = String(route?.inflight_request_id || "");
    if (!staleRequestId || staleRequestId === requestId) return false;
    if (typeof router.failRequest !== "function") return false;

    // The browser map is process-local while route ownership is persisted.
    // A service-worker restart can therefore leave inflight_request_id behind
    // after the server/Broker has already terminally released the request.  A
    // newly dispatched request for the same logical key must not be rejected by
    // that orphaned persisted owner.  Conversely, if the old request is still
    // present in the current process' activeRequests map, it is genuinely live
    // and the strict one-request-per-key invariant must continue to reject the
    // replacement.
    if (locallyLiveOwner(router, staleRequestId)) return false;

    const serverAuthoritative = authoritativeServerAdmission(message);

    // retireRoute keeps a short de-duplication set.  A late terminal callback
    // can mark an old request retired before all persisted state is reconciled;
    // remove only this exact orphan's marker before the exact-owner cleanup so
    // the stale lock cannot become permanent.  Exact request-id checks inside
    // the router still prevent a late old cleanup from clearing a newer owner.
    if (router.retiredRequests instanceof Set) router.retiredRequests.delete(staleRequestId);

    const reason = serverAuthoritative
      ? "server-authority-stale-inflight-v140"
      : "orphaned-persisted-inflight-v140";
    const retired = await router.failRequest(staleRequestId, reason).catch(() => false);
    if (!retired) return false;

    state.stale_inflight_recoveries += 1;
    if (serverAuthoritative) state.server_authority_recoveries += 1;
    else state.orphan_owner_recoveries += 1;
    state.last_recovery = {
      api_key_id: key,
      stale_request_id: staleRequestId,
      replacement_request_id: requestId,
      server_authoritative: serverAuthoritative,
      at_ms: Date.now(),
    };
    return true;
  }

  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver === "function" && !baseResolver.__chat2apiRouteRecoveryV140) {
    const wrappedResolver = async message => {
      await recoverStaleOwner(message);
      return baseResolver(message);
    };
    wrappedResolver.__chat2apiRouteRecoveryV136 = true;
    wrappedResolver.__chat2apiRouteRecoveryV140 = true;
    globalThis.resolveTargetTabForRequest = wrappedResolver;
  }

  const baseHandleServerMessage = globalThis.handleServerMessage;
  if (typeof baseHandleServerMessage === "function" && !baseHandleServerMessage.__chat2apiRouteRecoveryV140) {
    const wrappedHandleServerMessage = async message => {
      if (!CANCEL_TYPES.has(String(message?.type || ""))) {
        return baseHandleServerMessage(message);
      }
      const requestId = String(message?.request_id || "");
      let result;
      let caught = null;
      try {
        result = await baseHandleServerMessage(message);
      } catch (error) {
        caught = error;
      }

      const router = routerState();
      if (requestId && typeof router?.failRequest === "function") {
        if (router.retiredRequests instanceof Set) router.retiredRequests.delete(requestId);
        const retired = await router.failRequest(requestId, "server-cancel-control-v140").catch(() => false);
        if (retired) {
          state.explicit_cancel_retirements += 1;
          state.last_cancel = { request_id: requestId, at_ms: Date.now() };
        }
      }
      if (caught) throw caught;
      return result;
    };
    wrappedHandleServerMessage.__chat2apiRouteRecoveryV136 = true;
    wrappedHandleServerMessage.__chat2apiRouteRecoveryV140 = true;
    globalThis.handleServerMessage = wrappedHandleServerMessage;
  }

  state.recoverStaleOwner = recoverStaleOwner;
  state.locallyLiveOwner = locallyLiveOwner;
})();