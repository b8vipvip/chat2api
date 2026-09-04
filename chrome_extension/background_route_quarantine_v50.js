(() => {
  const KEY = "__CHAT2API_ROUTE_QUARANTINE_V50__";
  if (globalThis[KEY]) return;

  const WORKERS_KEY = "__CHAT2API_CONVERSATION_WORKERS_V25__";
  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const STORAGE_KEY = "chat2apiConversationRoutesV1";
  const COMPLETION_SETTLE_MS = 1200;
  const ERROR_RECYCLE_DELAY_MS = 100;
  const POLL_MS = 100;

  const state = {
    version: 50,
    revision: 86,
    pending: new Map(),
    quarantined: new Map(),
    captured: 0,
    recycled: 0,
    settled: 0,
    preserved_after_completion: 0,
    forced_after_completion: 0,
  };
  globalThis[KEY] = state;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const workers = globalThis[WORKERS_KEY];
  const router = globalThis[ROUTER_KEY];
  if (!workers?.releaseRequest || !router?.activeRequests) return;

  const baseReleaseRequest = workers.releaseRequest.bind(workers);

  function routeFor(routeKey) {
    return router?.routes && routeKey ? router.routes[routeKey] : null;
  }

  function snapshotBeforeRelease(requestId) {
    requestId = String(requestId || "");
    const selected = workers.requestTarget?.(requestId) || null;
    const active = router.activeRequests?.get?.(requestId) || null;
    const routeKey = String(selected?.routeKey || active?.key || "");
    const route = routeFor(routeKey);
    if (!routeKey || !route) return null;

    const snapshot = {
      requestId,
      routeKey,
      tabId: Number.isInteger(selected?.tabId) ? selected.tabId : (Number.isInteger(route.tab_id) ? route.tab_id : null),
      windowId: Number.isInteger(selected?.windowId) ? selected.windowId : (Number.isInteger(route.window_id) ? route.window_id : null),
      capturedAt: Date.now(),
      sentinel: `quarantine:${requestId}`,
    };

    // Keep a very short terminal handoff sentinel so a second request cannot
    // enter the tab while content_request_v6 is still running its finally block.
    // v86 deliberately never converts a successful completion into a route
    // recycle merely because that finally block is slow.
    route.inflight_request_id = snapshot.sentinel;
    router.activeRequests.set(snapshot.sentinel, {
      key: routeKey,
      kind: "quarantine",
      prompt_chars: 0,
      attachments: 0,
      tab_id: snapshot.tabId,
      window_id: snapshot.windowId,
      started_at: snapshot.capturedAt,
    });
    state.pending.set(requestId, snapshot);
    state.quarantined.set(routeKey, snapshot);
    state.captured += 1;
    return snapshot;
  }

  workers.releaseRequest = function releaseRequestWithQuarantine(requestId) {
    snapshotBeforeRelease(requestId);
    return baseReleaseRequest(requestId);
  };

  async function persistRoutes() {
    if (!router?.routes) return;
    await chrome.storage.local.set({ [STORAGE_KEY]: router.routes }).catch(() => {});
  }

  function clearSentinel(snapshot) {
    if (!snapshot) return;
    const route = routeFor(snapshot.routeKey);
    if (route?.inflight_request_id === snapshot.sentinel) route.inflight_request_id = null;
    router.activeRequests?.delete?.(snapshot.sentinel);
    if (state.quarantined.get(snapshot.routeKey)?.requestId === snapshot.requestId) {
      state.quarantined.delete(snapshot.routeKey);
    }
    state.pending.delete(snapshot.requestId);
  }

  async function controllerStatus(snapshot) {
    if (!Number.isInteger(snapshot?.tabId)) return null;
    try {
      const result = await chrome.tabs.sendMessage(snapshot.tabId, { type: "chat2api.lifecycle-status.v50" });
      return result && typeof result === "object" ? result : null;
    } catch (_) {
      return null;
    }
  }

  async function resetFailedRoute(snapshot, reason) {
    if (!snapshot) return;
    await sleep(ERROR_RECYCLE_DELAY_MS);
    const route = routeFor(snapshot.routeKey);
    if (route) {
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
      route.close_after = null;
      route.last_active_at = Date.now();
      route.last_rotation_reason = reason || "terminal-route-quarantine-v50";
      route.generation = Number(route.generation || 1) + 1;
    }
    if (Number.isInteger(snapshot.windowId)) {
      try { await chrome.alarms.clear(`chat2api-route-close:${snapshot.windowId}`); } catch (_) {}
      try { await chrome.windows.remove(snapshot.windowId); } catch (_) {}
    }
    clearSentinel(snapshot);
    await persistRoutes();
    state.recycled += 1;
    await chrome.storage.local.set({
      chat2apiRouteQuarantineV50: {
        request_id: snapshot.requestId,
        route_key: snapshot.routeKey,
        action: "recycled",
        reason: String(reason || "terminal-error"),
        revision: 86,
        at_ms: Date.now(),
      },
    }).catch(() => {});
  }

  async function settleCompletedRoute(snapshot) {
    if (!snapshot) return;
    const deadline = Date.now() + COMPLETION_SETTLE_MS;
    let last = null;
    while (Date.now() < deadline) {
      last = await controllerStatus(snapshot);
      if (!last?.active || String(last.active_request_id || "") !== snapshot.requestId) {
        clearSentinel(snapshot);
        await persistRoutes();
        state.settled += 1;
        await chrome.storage.local.set({
          chat2apiRouteQuarantineV50: {
            request_id: snapshot.requestId,
            route_key: snapshot.routeKey,
            action: "settled-preserved",
            revision: 86,
            at_ms: Date.now(),
          },
        }).catch(() => {});
        return;
      }
      await sleep(POLL_MS);
    }

    // v50 used to close the entire routed window here after 2.5 seconds. That
    // turned a successful API response into an affinity reset whenever the
    // content finally block lagged. Successful terminals must be authoritative:
    // release the sentinel and keep the route for its normal 5-minute idle
    // deadline. content_request_lifecycle_v50 remains the final guard against a
    // genuinely overlapping request on the same tab.
    clearSentinel(snapshot);
    await persistRoutes();
    state.preserved_after_completion += 1;
    await chrome.storage.local.set({
      chat2apiRouteQuarantineV50: {
        request_id: snapshot.requestId,
        route_key: snapshot.routeKey,
        action: "completion-cleanup-lag-preserved",
        active_request_id: String(last?.active_request_id || ""),
        revision: 86,
        at_ms: Date.now(),
      },
    }).catch(() => {});
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    const requestId = String(event.request_id || "");
    if (!requestId) return false;
    const terminal = [
      "chat.completed", "chat.error", "chat.cancelled",
      "image.completed", "image.error", "image.cancelled",
    ];
    if (!terminal.includes(event.type)) return false;

    // releaseRequest is invoked by conversation_workers_v25's earlier terminal
    // listener and captures ownership before deletion. Defer one microtask so
    // this listener can consume that snapshot without racing async route cleanup.
    queueMicrotask(() => {
      const snapshot = state.pending.get(requestId);
      if (!snapshot) return;
      if (event.type === "chat.completed" || event.type === "image.completed") {
        settleCompletedRoute(snapshot).catch(async () => {
          // A bookkeeping failure after a successful answer is not a reason to
          // destroy the conversation. Preserve it and let the router's normal
          // idle/budget policy decide when to rotate.
          clearSentinel(snapshot);
          await persistRoutes();
          state.preserved_after_completion += 1;
        });
      } else {
        resetFailedRoute(snapshot, `${event.type}-quarantine-v50`).catch(() => {});
      }
    });
    return false;
  });

  state.controllerStatus = controllerStatus;
  state.resetFailedRoute = resetFailedRoute;
  state.settleCompletedRoute = settleCompletedRoute;
  state.constants = Object.freeze({ completion_settle_ms: COMPLETION_SETTLE_MS, error_recycle_delay_ms: ERROR_RECYCLE_DELAY_MS, success_recycle: false });
})();
