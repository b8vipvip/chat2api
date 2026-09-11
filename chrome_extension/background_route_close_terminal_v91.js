(() => {
  const KEY = "__CHAT2API_ROUTE_CLOSE_TERMINAL_V91__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const DISPATCH_KEY = "__CHAT2API_CONVERSATION_DISPATCH_V1__";
  const RETENTION_MS = 60000;
  const state = {
    revision: 91,
    policy: "terminal-report-only-v91",
    reported: new Set(),
  };
  globalThis[KEY] = state;

  function terminalType(kind) {
    if (kind === "image") return "image.error";
    if (kind === "voice") return "voice.error";
    return "chat.error";
  }

  function snapshotBy(field, value) {
    const router = globalThis[ROUTER_KEY];
    const routes = router?.routes;
    if (!routes || typeof routes !== "object") return null;
    for (const [key, route] of Object.entries(routes)) {
      if (route?.[field] !== value) continue;
      const requestId = String(route.inflight_request_id || "");
      if (!requestId) return null;
      const active = router.activeRequests?.get?.(requestId) || {};
      return {
        requestId,
        key,
        kind: String(active.kind || "chat"),
        tabId: Number.isInteger(route.tab_id) ? route.tab_id : null,
        windowId: Number.isInteger(route.window_id) ? route.window_id : null,
      };
    }
    return null;
  }

  function report(snapshot, reason) {
    const requestId = String(snapshot?.requestId || "");
    if (!requestId || state.reported.has(requestId)) return false;
    state.reported.add(requestId);

    // Transport bookkeeping may be cleared here, but route/window state remains
    // exclusively owned by conversation_routing.js. Its onRemoved handler runs
    // the lifecycle transition after this listener snapshots the active request.
    globalThis[DISPATCH_KEY]?.requestTabs?.delete?.(requestId);

    const objectName = reason === "routed-tab-removed" ? "tab" : "window";
    const error = `Routed ChatGPT ${objectName} closed while request was active`;
    void trySendSocket({
      type: terminalType(snapshot.kind),
      request_id: requestId,
      error,
      diagnostics: {
        unexpected_route_close_terminal_v91: true,
        route_close_reason: reason,
        route_key: snapshot.key,
        routed_tab_id: snapshot.tabId,
        routed_window_id: snapshot.windowId,
        route_window_authority: "single-route-window-authority-v30",
        route_close_reporter: state.policy,
      },
    }).catch(() => false);

    setTimeout(() => state.reported.delete(requestId), RETENTION_MS);
    return true;
  }

  state.snapshotBy = snapshotBy;
  state.report = report;

  chrome.tabs.onRemoved.addListener(tabId => {
    const snapshot = snapshotBy("tab_id", tabId);
    if (snapshot) report(snapshot, "routed-tab-removed");
  });

  chrome.windows.onRemoved.addListener(windowId => {
    const snapshot = snapshotBy("window_id", windowId);
    if (snapshot) report(snapshot, "routed-window-removed");
  });
})();
