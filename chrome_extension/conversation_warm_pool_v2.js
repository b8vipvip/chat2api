(() => {
  const KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const STORAGE_KEY = "chat2apiConversationWarmPoolV2";
  const WARM_URL = "https://chatgpt.com/";
  const READY_TIMEOUT_MS = 45000;
  const state = {
    warm: null,
    opening: null,
    replenishTimer: null,
    claimedRequests: new Set(),
  };
  globalThis[KEY] = state;

  const sleepWarm = ms => new Promise(resolve => setTimeout(resolve, ms));
  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver !== "function") return;

  function routeKey(message) {
    const value = message?.routing?.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  async function routerState(timeoutMs = 4000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const router = globalThis[ROUTER_KEY];
      if (router?.loaded && router.routes && typeof router.routes === "object") return router;
      await sleepWarm(80);
    }
    return globalThis[ROUTER_KEY] || null;
  }

  function freshRoute(key) {
    return {
      api_key_id: key,
      conversation_id: null,
      conversation_url: null,
      generation: 1,
      turn_count: 0,
      text_chars: 0,
      attachment_count: 0,
      slow_load_strikes: 0,
      last_open_ms: null,
      last_rotation_reason: null,
      tab_id: null,
      window_id: null,
      window_owned: true,
      inflight_request_id: null,
      last_active_at: 0,
      close_after: null,
      prewarm_claimed_at: null,
      prewarm_load_ms: null,
    };
  }

  function resetForWarmClaim(route) {
    if (!route) return false;
    const hadClosedSession = Boolean(
      route.conversation_id ||
      route.conversation_url ||
      Number(route.turn_count || 0) ||
      Number(route.text_chars || 0) ||
      Number(route.attachment_count || 0) ||
      Number.isInteger(route.tab_id) ||
      Number.isInteger(route.window_id)
    );
    route.conversation_id = null;
    route.conversation_url = null;
    route.turn_count = 0;
    route.text_chars = 0;
    route.attachment_count = 0;
    route.slow_load_strikes = 0;
    route.last_open_ms = null;
    route.inflight_request_id = null;
    route.close_after = null;
    if (hadClosedSession) route.generation = Number(route.generation || 1) + 1;
    return hadClosedSession;
  }

  async function tabExists(tabId) {
    if (!Number.isInteger(tabId)) return null;
    try {
      const tab = await chrome.tabs.get(tabId);
      if (!isChatGptUrl(tab.url || tab.pendingUrl || "")) return null;
      return tab;
    } catch (_) {
      return null;
    }
  }

  async function composerReady(tabId) {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const selector = "#prompt-textarea, textarea[placeholder], div[contenteditable='true'][data-lexical-editor='true'], div[contenteditable='true'].ProseMirror";
          const node = document.querySelector(selector);
          if (!node) return false;
          const rect = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
        },
      });
      return Boolean(results?.[0]?.result);
    } catch (_) {
      return false;
    }
  }

  async function waitWarmReady(tabId, timeoutMs = READY_TIMEOUT_MS) {
    const started = Date.now();
    const deadline = started + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      const tab = await tabExists(tabId);
      if (!tab) {
        await sleepWarm(180);
        continue;
      }
      try {
        if (await composerReady(tabId)) {
          await ensureContent(tabId);
          return { tab: await chrome.tabs.get(tabId), load_ms: Date.now() - started };
        }
      } catch (error) {
        lastError = error;
      }
      await sleepWarm(220);
    }
    throw lastError || new Error("Timed out prewarming the ChatGPT composer");
  }

  async function clearStoredWarm() {
    state.warm = null;
    await chrome.storage.local.remove(STORAGE_KEY).catch(() => {});
  }

  async function warmUsedByRoute(tabId) {
    const router = await routerState();
    if (!router?.routes) return false;
    return Object.values(router.routes).some(route => route?.tab_id === tabId);
  }

  async function recoverStoredWarm() {
    const stored = await chrome.storage.local.get(STORAGE_KEY).catch(() => ({}));
    const value = stored?.[STORAGE_KEY];
    if (!value || !Number.isInteger(value.tab_id)) return null;
    const tab = await tabExists(value.tab_id);
    if (!tab || await warmUsedByRoute(value.tab_id)) {
      await clearStoredWarm();
      return null;
    }
    try {
      const ready = await waitWarmReady(value.tab_id, 8000);
      state.warm = { ...value, tab_id: ready.tab.id, window_id: ready.tab.windowId, recovered: true };
      return state.warm;
    } catch (_) {
      await clearStoredWarm();
      return null;
    }
  }

  async function createWarmWindow() {
    const createdAt = Date.now();
    const created = await chrome.windows.create({ url: WARM_URL, focused: false, type: "normal" });
    if (!created?.id) throw new Error("Chrome did not create the ChatGPT warm-up window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item.id)) || null;
    }
    if (!tab?.id) throw new Error("The ChatGPT warm-up window contains no usable tab");

    try {
      const ready = await waitWarmReady(tab.id);
      const warm = {
        tab_id: ready.tab.id,
        window_id: ready.tab.windowId,
        created_at_ms: createdAt,
        ready_at_ms: Date.now(),
        load_ms: ready.load_ms,
        strategy: "composer-controller-ready",
      };
      state.warm = warm;
      await chrome.storage.local.set({ [STORAGE_KEY]: warm });
      return warm;
    } catch (error) {
      try { await chrome.windows.remove(created.id); } catch (_) {}
      throw error;
    }
  }

  async function ensureWarmWindow() {
    if (state.warm) {
      const tab = await tabExists(state.warm.tab_id);
      if (tab && !await warmUsedByRoute(tab.id)) return state.warm;
      await clearStoredWarm();
    }
    if (state.opening) return state.opening;
    state.opening = (async () => {
      const recovered = await recoverStoredWarm();
      if (recovered) return recovered;
      return createWarmWindow();
    })().finally(() => { state.opening = null; });
    return state.opening;
  }

  function scheduleWarm(delayMs = 1200) {
    clearTimeout(state.replenishTimer);
    state.replenishTimer = setTimeout(async () => {
      const settings = await config().catch(() => ({}));
      if (!settings.clientId || !settings.clientToken || settings.socketState !== "connected") return;
      await ensureWarmWindow().catch(async error => {
        await chrome.storage.local.set({ chat2apiWarmupError: String(error?.message || error), chat2apiWarmupErrorAt: Date.now() }).catch(() => {});
      });
    }, delayMs);
  }

  async function liveRouteTab(route) {
    if (!Number.isInteger(route?.tab_id)) return null;
    return tabExists(route.tab_id);
  }

  async function claimWarmWindow(key, message) {
    const router = await routerState();
    if (!router?.routes) return null;
    let route = router.routes[key];
    if (route && await liveRouteTab(route)) return null;

    route = route || freshRoute(key);
    const freshAfterClosedWindow = resetForWarmClaim(route);
    router.routes[key] = route;

    const warm = await ensureWarmWindow().catch(() => null);
    if (!warm) return null;
    const tab = await tabExists(warm.tab_id);
    if (!tab) {
      await clearStoredWarm();
      return null;
    }

    route.tab_id = tab.id;
    route.window_id = tab.windowId;
    route.window_owned = true;
    route.last_active_at = Date.now();
    route.close_after = null;
    route.last_open_ms = 0;
    route.prewarm_claimed_at = Date.now();
    route.prewarm_load_ms = Number(warm.load_ms || 0);
    route.last_rotation_reason = freshAfterClosedWindow
      ? "prewarmed-after-closed-window"
      : "prewarmed-first-request";

    state.warm = null;
    await chrome.storage.local.remove(STORAGE_KEY).catch(() => {});
    if (message?.request_id) state.claimedRequests.add(message.request_id);

    // Do not create another Chrome window while this request is actively using the
    // claimed warm window. The extension is already busy, so an immediate refill
    // cannot serve another request and only looks like routing opened a fresh window.
    // The terminal-event listener below replenishes the one-slot pool after the
    // current interaction completes/errors/cancels.
    return { tab, warm, route, freshAfterClosedWindow };
  }

  globalThis.resolveTargetTabForRequest = async function resolvePrewarmedConversation(message) {
    const key = routeKey(message);
    let claimed = null;
    if (key) claimed = await claimWarmWindow(key, message);
    const tab = await baseResolver(message);
    if (claimed && message?.request_id) {
      const stableAge = Math.max(0, Date.now() - Number(claimed.warm.ready_at_ms || Date.now()));
      const eventType = message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics";
      await trySendSocket({
        type: eventType,
        kind: message.type === "voice.request" ? "voice" : (message.type === "image.request" ? "image" : undefined),
        request_id: message.request_id,
        diagnostics: {
          conversation_router: "per-key-v1+warm-pool-v2",
          conversation_strategy: "claim-prewarmed-window",
          conversation_prewarm_hit: true,
          conversation_prewarm_load_ms: claimed.warm.load_ms,
          conversation_prewarm_ready_age_ms: stableAge,
          conversation_fresh_after_closed_window: claimed.freshAfterClosedWindow,
          conversation_warm_replenish_on_claim: false,
          conversation_warm_replenish_after_terminal: true,
          routed_tab_id: tab?.id ?? null,
          routed_window_id: tab?.windowId ?? null,
        },
      }).catch(() => {});
    }
    return tab;
  };

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(event.type)) return false;
    const claimed = Boolean(event.request_id && state.claimedRequests.delete(event.request_id));
    // Replenish only after the claimed interaction has ended. For non-claimed
    // requests this also acts as a safety refill if an earlier warm-up failed.
    scheduleWarm(claimed ? 900 : 1400);
    return false;
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.socketState?.newValue === "connected") scheduleWarm(600);
  });

  chrome.tabs.onRemoved.addListener(tabId => {
    if (state.warm?.tab_id === tabId) clearStoredWarm().catch(() => {});
  });

  setTimeout(async () => {
    const settings = await config().catch(() => ({}));
    if (settings.clientId && settings.clientToken && settings.socketState === "connected") scheduleWarm(300);
  }, 300);
})();
