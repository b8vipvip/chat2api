(() => {
  const KEY = "__CHAT2API_PERSISTENT_ROUTE_RESTORE_V132__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const POOL_KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__";
  const ROUTES_STORAGE_KEY = "chat2apiConversationRoutesV1";
  const NEW_CHAT_URL = "https://chatgpt.com/";
  const state = { version: 132, resets: 0 };
  globalThis[KEY] = state;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function isChatGpt(value = "") {
    try {
      return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname.toLowerCase());
    } catch (_) { return false; }
  }

  function conversationId(value = "") {
    try {
      const url = new URL(value);
      if (!isChatGpt(url.href)) return null;
      const match = url.pathname.match(/\/c\/([^/?#]+)/i);
      return match ? decodeURIComponent(match[1]) : null;
    } catch (_) { return null; }
  }

  function routingKey(message) {
    const value = String(message?.routing?.api_key_id || "").trim();
    return value || null;
  }

  function routeFor(key) {
    const router = globalThis[ROUTER_KEY];
    return key && router?.routes && typeof router.routes === "object" ? router.routes[key] || null : null;
  }

  async function persistRoutes() {
    const router = globalThis[ROUTER_KEY];
    if (!router?.routes) return;
    await chrome.storage.local.set({ [ROUTES_STORAGE_KEY]: router.routes }).catch(() => {});
  }

  async function tabSnapshot(tabId) {
    try { return await chrome.tabs.get(tabId); } catch (_) { return null; }
  }

  async function waitForConversationDecision(tabId, expectedId, timeoutMs = 4000) {
    const deadline = Date.now() + timeoutMs;
    let last = null;
    while (Date.now() < deadline) {
      last = await tabSnapshot(tabId);
      const url = last?.url || last?.pendingUrl || "";
      const actual = conversationId(url);
      if (actual === expectedId) return { matched: true, tab: last, actual };
      if (!isChatGpt(url) || last?.status !== "complete") {
        await sleep(120);
        continue;
      }
      await sleep(160);
    }
    const url = last?.url || last?.pendingUrl || "";
    return { matched: conversationId(url) === expectedId, tab: last, actual: conversationId(url) };
  }

  async function waitFresh(tabId, timeoutMs = 30000) {
    const started = Date.now();
    const deadline = started + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const tab = await chrome.tabs.get(tabId);
        const url = tab?.url || tab?.pendingUrl || "";
        if (isChatGpt(url) && tab?.status === "complete") {
          if (typeof ensureContent === "function") await ensureContent(tabId);
          return { tab, load_ms: Date.now() - started };
        }
      } catch (error) { lastError = error; }
      await sleep(160);
    }
    throw lastError || new Error("Timed out restoring persistent route to a fresh ChatGPT page");
  }

  async function resetUnavailableSavedConversation(route, tabId) {
    await chrome.tabs.update(tabId, { url: NEW_CHAT_URL, active: true });
    const ready = await waitFresh(tabId);
    route.conversation_id = null;
    route.conversation_url = null;
    route.generation = Number(route.generation || 1) + 1;
    route.turn_count = 0;
    route.text_chars = 0;
    route.attachment_count = 0;
    route.slow_load_strikes = 0;
    route.last_open_ms = ready.load_ms;
    route.last_rotation_reason = "persistent-pool-saved-conversation-unavailable";
    route.last_active_at = Date.now();
    // A standby stops being pool capacity as soon as it is assigned to a key.
    // Keep route ownership true so conversation_routing can re-arm the five-minute
    // same-key lease after this request reaches a successful terminal state.
    route.window_owned = true;
    route.close_after = null;
    route.persistent_pool_revision = 132;
    await persistRoutes();
    state.resets += 1;
    return ready.tab;
  }

  const pool = globalThis[POOL_KEY];
  if (pool && typeof pool.snapshot === "function" && !pool.snapshot.__chat2apiPersistentSnapshotV132) {
    const baseSnapshot = pool.snapshot.bind(pool);
    const wrappedSnapshot = async (...args) => ({
      ...(await baseSnapshot(...args)),
      speculative_windows: false,
      prewarmed_windows: true,
      standby_excludes_routed_windows: true,
    });
    wrappedSnapshot.__chat2apiPersistentSnapshotV132 = true;
    pool.snapshot = wrappedSnapshot;
  }

  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver === "function" && !baseResolver.__chat2apiPersistentRouteRestoreV132) {
    const wrappedResolver = async message => {
      const key = routingKey(message);
      const before = routeFor(key);
      const expectedId = String(before?.conversation_id || "").trim();
      const tab = await baseResolver(message);
      if (!key || !expectedId || !Number.isInteger(tab?.id)) return tab;

      const route = routeFor(key);
      if (!route || String(route.conversation_id || "") !== expectedId) return tab;
      const decision = await waitForConversationDecision(tab.id, expectedId);
      if (decision.matched) return decision.tab || tab;
      return resetUnavailableSavedConversation(route, tab.id);
    };
    wrappedResolver.__chat2apiPersistentRouteRestoreV132 = true;
    globalThis.resolveTargetTabForRequest = wrappedResolver;
  }
})();
