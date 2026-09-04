(() => {
  const KEY = "__CHAT2API_ORPHAN_ROUTE_CLEANUP_V87__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const STORAGE_KEY = "chat2apiConversationRoutesV1";
  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";
  const state = {
    version: 87,
    checked: 0,
    closed: 0,
    last: null,
  };
  globalThis[KEY] = state;

  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver !== "function") return;

  function routeKey(message) {
    const value = message?.routing?.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  async function liveTab(tabId) {
    if (!Number.isInteger(tabId)) return null;
    try {
      const tab = await chrome.tabs.get(tabId);
      const url = String(tab?.url || tab?.pendingUrl || "");
      const chatgpt = typeof isChatGptUrl === "function"
        ? isChatGptUrl(url)
        : /^https:\/\/(?:www\.)?(?:chatgpt\.com|chat\.openai\.com)\//i.test(url);
      return chatgpt ? tab : null;
    } catch (_) {
      return null;
    }
  }

  async function liveWindow(windowId) {
    if (!Number.isInteger(windowId)) return null;
    try { return await chrome.windows.get(windowId, { populate: false }); }
    catch (_) { return null; }
  }

  async function persist(router) {
    await chrome.storage.local.set({ [STORAGE_KEY]: router.routes }).catch(() => {});
  }

  async function cleanupBeforeResolve(message) {
    const key = routeKey(message);
    const router = globalThis[ROUTER_KEY];
    const route = key && router?.routes ? router.routes[key] : null;
    if (!route) return false;
    state.checked += 1;

    // Never touch a route that is still explicitly owned by an in-flight
    // request/quarantine sentinel. Its terminal owner decides whether to keep or
    // recycle the window.
    if (route.inflight_request_id) return false;

    const tab = await liveTab(route.tab_id);
    const sameWindow = Boolean(tab && Number.isInteger(route.window_id) && tab.windowId === route.window_id);
    if (sameWindow) return false;

    const oldWindowId = Number.isInteger(route.window_id) ? route.window_id : null;
    const oldTabId = Number.isInteger(route.tab_id) ? route.tab_id : null;
    const oldWindow = await liveWindow(oldWindowId);

    // A route whose recorded tab disappeared is not reusable. Clear its stale
    // identity before the affinity/warm-pool resolver chooses the replacement;
    // if the automation-owned browser window itself still exists, close it so a
    // pasted-but-never-submitted draft cannot remain orphaned on screen.
    route.conversation_id = null;
    route.conversation_url = null;
    route.tab_id = null;
    route.window_id = null;
    route.window_owned = true;
    route.close_after = null;
    route.turn_count = 0;
    route.text_chars = 0;
    route.attachment_count = 0;
    route.slow_load_strikes = 0;
    route.last_open_ms = null;
    route.last_active_at = Date.now();
    route.last_rotation_reason = "orphan-route-cleanup-v87";
    route.generation = Number(route.generation || 1) + 1;
    await persist(router);

    if (oldWindow && oldWindowId !== null) {
      try { await chrome.alarms.clear(`${ROUTE_ALARM_PREFIX}${oldWindowId}`); } catch (_) {}
      try { await chrome.windows.remove(oldWindowId); state.closed += 1; } catch (_) {}
    }

    state.last = {
      action: "orphan-route-cleaned",
      api_key_id: key,
      request_id: String(message?.request_id || ""),
      old_tab_id: oldTabId,
      old_window_id: oldWindowId,
      old_window_closed: Boolean(oldWindow),
      at_ms: Date.now(),
    };
    await chrome.storage.local.set({ chat2apiOrphanRouteCleanupV87: state.last }).catch(() => {});
    return true;
  }

  globalThis.resolveTargetTabForRequest = async function resolveAfterOrphanCleanup(message) {
    await cleanupBeforeResolve(message).catch(() => false);
    return baseResolver(message);
  };

  state.cleanupBeforeResolve = cleanupBeforeResolve;
})();
