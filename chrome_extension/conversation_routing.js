(() => {
  const KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chat2apiConversationRoutesV1";
  const NEW_CHAT_URL = "https://chatgpt.com/";
  const IDLE_CLOSE_MS = 5 * 60 * 1000;
  const SLOW_LOAD_MS = 8000;
  const HARD_SLOW_LOAD_MS = 15000;
  const MAX_TURNS = 32;
  const MAX_TEXT_CHARS = 96000;
  const MAX_ATTACHMENTS = 16;
  const ALARM_PREFIX = "chat2api-route-close:";
  const FAILURE_TYPES = new Set(["chat.error", "chat.cancelled", "image.error", "image.cancelled", "voice.error", "voice.cancelled"]);
  const SUCCESS_TYPES = new Set(["chat.completed", "image.completed"]);
  const state = {
    revision: 30,
    authority: "single-route-window-authority-v30",
    loaded: false,
    routes: {},
    openings: new Map(),
    activeRequests: new Map(),
    retiredRequests: new Set(),
  };
  globalThis[KEY] = state;

  const sleepLocal = ms => new Promise(resolve => setTimeout(resolve, ms));
  const createManagedWindow = (options, reason) => typeof globalThis.chat2apiCreateWindowStaggered === "function"
    ? globalThis.chat2apiCreateWindowStaggered(options, { reason })
    : chrome.windows.create(options);

  function isChatGpt(value = "") {
    try {
      return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname);
    } catch (_) { return false; }
  }

  function conversationId(url = "") {
    try {
      const parsed = new URL(url);
      if (!isChatGpt(parsed.href)) return null;
      const match = parsed.pathname.match(/\/c\/([^/?#]+)/i);
      return match ? decodeURIComponent(match[1]) : null;
    } catch (_) { return null; }
  }

  function routingKey(message) {
    const value = message?.routing?.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  function requestKind(message) {
    if (message?.type === "voice.request" || message?.type === "voice.live.start") return "voice";
    if (message?.type === "image.request") return "image";
    return "chat";
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
    };
  }

  async function ensureLoaded() {
    if (state.loaded) return;
    const stored = await chrome.storage.local.get(STORAGE_KEY).catch(() => ({}));
    const value = stored?.[STORAGE_KEY];
    state.routes = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    state.loaded = true;
  }

  async function persist() {
    await chrome.storage.local.set({ [STORAGE_KEY]: state.routes }).catch(() => {});
  }

  async function routeFor(key) {
    await ensureLoaded();
    if (!state.routes[key]) state.routes[key] = freshRoute(key);
    return state.routes[key];
  }

  function routeOverBudget(route) {
    if (!route) return null;
    if (Number(route.turn_count || 0) >= MAX_TURNS) return `turns>=${MAX_TURNS}`;
    if (Number(route.text_chars || 0) >= MAX_TEXT_CHARS) return `text_chars>=${MAX_TEXT_CHARS}`;
    if (Number(route.attachment_count || 0) >= MAX_ATTACHMENTS) return `attachments>=${MAX_ATTACHMENTS}`;
    if (Number(route.slow_load_strikes || 0) >= 2) return "slow_load_strikes>=2";
    return null;
  }

  function resetClosedRoute(route, reason = "closed-window-new-chat") {
    if (!route) return false;
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
    route.inflight_request_id = null;
    route.close_after = null;
    if (hadSession) {
      route.generation = Number(route.generation || 1) + 1;
      route.last_rotation_reason = reason;
    }
    return hadSession;
  }

  async function waitForChatReady(tabId, timeoutMs = 30000) {
    const started = Date.now();
    const deadline = started + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const tab = await chrome.tabs.get(tabId);
        const url = tab.url || tab.pendingUrl || "";
        if (!isChatGpt(url) || url.includes("/images") || (tab.status && tab.status !== "complete")) {
          await sleepLocal(180);
          continue;
        }
        await ensureContent(tabId);
        return { tab, load_ms: Date.now() - started };
      } catch (error) { lastError = error; }
      await sleepLocal(220);
    }
    throw lastError || new Error("Timed out waiting for routed ChatGPT conversation");
  }

  async function liveTab(route) {
    if (!Number.isInteger(route?.tab_id)) return null;
    try {
      const tab = await chrome.tabs.get(route.tab_id);
      return isChatGpt(tab.url || tab.pendingUrl || "") ? tab : null;
    } catch (_) { return null; }
  }

  async function clearCloseAlarm(route) {
    if (Number.isInteger(route?.window_id)) {
      try { await chrome.alarms.clear(`${ALARM_PREFIX}${route.window_id}`); } catch (_) {}
    }
    if (route) route.close_after = null;
  }

  async function scheduleClose(route) {
    if (!Number.isInteger(route?.window_id) || !route.window_owned || route.inflight_request_id) return;
    route.last_active_at = Date.now();
    route.close_after = route.last_active_at + IDLE_CLOSE_MS;
    await chrome.alarms.create(`${ALARM_PREFIX}${route.window_id}`, { when: route.close_after });
    await persist();
  }

  async function navigateFresh(route, reason) {
    if (!Number.isInteger(route?.tab_id)) throw new Error("Cannot rotate conversation without a routed tab");
    await chrome.tabs.update(route.tab_id, { url: NEW_CHAT_URL, active: true });
    const ready = await waitForChatReady(route.tab_id);
    route.conversation_id = null;
    route.conversation_url = null;
    route.generation = Number(route.generation || 1) + 1;
    route.turn_count = 0;
    route.text_chars = 0;
    route.attachment_count = 0;
    route.slow_load_strikes = 0;
    route.last_open_ms = ready.load_ms;
    route.last_rotation_reason = reason || "budget";
    await persist();
    return ready.tab;
  }

  async function openWindowForRoute(key, route) {
    const requestedUrl = route.conversation_url || NEW_CHAT_URL;
    const expectedConversation = route.conversation_id;
    const created = await createManagedWindow({ url: requestedUrl, focused: false, type: "normal" }, "routed-conversation-authority-v30");
    if (!created?.id) throw new Error("Chrome did not create a routed ChatGPT window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item.id)) || null;
    }
    if (!tab?.id) {
      try { await chrome.windows.remove(created.id); } catch (_) {}
      throw new Error("Routed ChatGPT window contains no usable tab");
    }

    route.window_id = created.id;
    route.tab_id = tab.id;
    route.window_owned = true;
    route.last_active_at = Date.now();
    await chrome.storage.local.set({ boundTabId: tab.id, autoBind: false, modelsUpdatedAt: 0 });
    let ready;
    try {
      ready = await waitForChatReady(tab.id);
    } catch (error) {
      // Creation and its failure cleanup are one atomic router-owned operation.
      try { await chrome.windows.remove(created.id); } catch (_) {}
      resetClosedRoute(route, "route-open-readiness-failed");
      await persist();
      throw error;
    }
    route.last_open_ms = ready.load_ms;

    if (expectedConversation) {
      const actualConversation = conversationId(ready.tab.url || ready.tab.pendingUrl || "");
      if (actualConversation !== expectedConversation) {
        ready.tab = await navigateFresh(route, "saved-conversation-unavailable");
      } else if (ready.load_ms >= HARD_SLOW_LOAD_MS) {
        route.slow_load_strikes = 2;
        ready.tab = await navigateFresh(route, `single-load>=${HARD_SLOW_LOAD_MS}ms`);
      } else if (ready.load_ms >= SLOW_LOAD_MS) {
        route.slow_load_strikes = Number(route.slow_load_strikes || 0) + 1;
        if (route.slow_load_strikes >= 2) ready.tab = await navigateFresh(route, `two-loads>=${SLOW_LOAD_MS}ms`);
      } else {
        route.slow_load_strikes = 0;
      }
    }
    await persist();
    return ready.tab;
  }

  async function ensureWindow(key, route) {
    const existing = await liveTab(route);
    if (existing) {
      const budgetReason = routeOverBudget(route);
      if (budgetReason) return navigateFresh(route, budgetReason);
      try { await chrome.tabs.update(existing.id, { active: true }); } catch (_) {}
      return chrome.tabs.get(existing.id).catch(() => existing);
    }
    resetClosedRoute(route, "closed-window-new-chat");
    if (state.openings.has(key)) return state.openings.get(key);
    const promise = openWindowForRoute(key, route).finally(() => state.openings.delete(key));
    state.openings.set(key, promise);
    return promise;
  }

  async function emitDiagnostics(message, route, tab, strategy) {
    const diagnostics = {
      conversation_router: "single-authority-v30",
      conversation_api_key_id: route.api_key_id,
      conversation_id: route.conversation_id,
      conversation_generation: route.generation,
      conversation_turn_count: route.turn_count,
      conversation_text_chars: route.text_chars,
      conversation_attachment_count: route.attachment_count,
      conversation_last_open_ms: route.last_open_ms,
      conversation_slow_load_strikes: route.slow_load_strikes,
      conversation_strategy: strategy,
      conversation_idle_close_ms: IDLE_CLOSE_MS,
      route_window_authority: state.authority,
      browser_side_same_api_queue: false,
      routed_tab_id: tab?.id ?? null,
      routed_window_id: tab?.windowId ?? route.window_id ?? null,
    };
    const payload = message.type === "chat.request"
      ? { type: "chat.diagnostics", request_id: message.request_id, diagnostics }
      : { type: "image.diagnostics", kind: message.type === "voice.request" ? "voice" : "image", request_id: message.request_id, diagnostics };
    await trySendSocket(payload).catch(() => false);
  }

  async function resolveForRequest(message) {
    const key = routingKey(message);
    if (!key) return resolveTargetTab();
    const requestId = String(message?.request_id || "");
    const route = await routeFor(key);
    if (route.inflight_request_id && route.inflight_request_id !== requestId) {
      throw new Error(`Server scheduler invariant violated: logical API ${key} already owns request ${route.inflight_request_id}`);
    }
    const hadLiveTab = Boolean(await liveTab(route));
    await clearCloseAlarm(route);
    const tab = await ensureWindow(key, route);
    route.tab_id = tab.id;
    route.window_id = tab.windowId;
    route.last_active_at = Date.now();
    route.inflight_request_id = requestId || null;
    if (requestId) {
      state.activeRequests.set(requestId, {
        key,
        kind: requestKind(message),
        prompt_chars: String(message.prompt || "").length,
        attachments: Array.isArray(message.attachments) ? message.attachments.length : (message.audio ? 1 : 0),
        tab_id: tab.id,
        window_id: tab.windowId,
        started_at: Date.now(),
      });
    }
    await persist();
    await emitDiagnostics(message, route, tab, hadLiveTab ? "reuse-live-window" : "new-chat-window");
    return tab;
  }

  globalThis.resolveTargetTabForRequest = resolveForRequest;
  globalThis.chat2apiConversationRoutingConfig = Object.freeze({
    revision: 30,
    authority: state.authority,
    idle_close_ms: IDLE_CLOSE_MS,
    max_turns: MAX_TURNS,
    max_text_chars: MAX_TEXT_CHARS,
    max_attachments: MAX_ATTACHMENTS,
    slow_load_ms: SLOW_LOAD_MS,
    hard_slow_load_ms: HARD_SLOW_LOAD_MS,
  });

  async function keyForRequest(requestId) {
    const active = state.activeRequests.get(String(requestId || ""));
    if (active?.key) return active.key;
    await ensureLoaded();
    for (const [key, route] of Object.entries(state.routes)) {
      if (String(route?.inflight_request_id || "") === String(requestId || "")) return key;
    }
    return null;
  }

  async function captureConversation(route, sender) {
    let url = sender?.tab?.url || "";
    if (!conversationId(url) && Number.isInteger(route?.tab_id)) {
      try { url = (await chrome.tabs.get(route.tab_id)).url || url; } catch (_) {}
    }
    const id = conversationId(url);
    if (!id) return false;
    route.conversation_id = id;
    route.conversation_url = url;
    return true;
  }

  async function retireRoute(key, route, requestId, reason) {
    requestId = String(requestId || "");
    if (requestId && state.retiredRequests.has(requestId)) return false;
    if (requestId) state.retiredRequests.add(requestId);
    const windowId = Number.isInteger(route?.window_id) ? route.window_id : null;
    if (route?.inflight_request_id && requestId && route.inflight_request_id !== requestId) return false;
    await clearCloseAlarm(route);
    if (requestId) state.activeRequests.delete(requestId);
    resetClosedRoute(route, reason || "terminal-failure");
    route.last_active_at = Date.now();
    await persist();
    if (Number.isInteger(windowId)) {
      try { await chrome.windows.remove(windowId); } catch (_) {}
    }
    setTimeout(() => state.retiredRequests.delete(requestId), 60000);
    await sendExtensionStatus(false).catch(() => {});
    return true;
  }

  async function failRequest(requestId, reason = "dispatch-failure") {
    const key = await keyForRequest(requestId);
    if (!key) return false;
    const route = await routeFor(key);
    return retireRoute(key, route, requestId, reason);
  }
  state.failRequest = failRequest;
  state.retireRoute = retireRoute;

  async function completeRequest(event, sender) {
    const requestId = String(event?.request_id || "");
    if (!requestId) return;
    const key = await keyForRequest(requestId);
    if (!key) return;
    const route = await routeFor(key);
    const active = state.activeRequests.get(requestId) || {};
    await captureConversation(route, sender);
    const completedChat = event.type === "chat.completed";
    const completedVoice = event.type === "image.completed" && event.kind === "voice";
    if (completedChat || completedVoice) {
      const completionChars = completedChat ? String(event.text || "").length : String(event.voice?.transcript || "").length;
      route.turn_count = Number(route.turn_count || 0) + 1;
      route.text_chars = Number(route.text_chars || 0) + Number(active.prompt_chars || 0) + completionChars;
      route.attachment_count = Number(route.attachment_count || 0) + Number(active.attachments || 0);
    }
    if (route.inflight_request_id === requestId) route.inflight_request_id = null;
    route.last_active_at = Date.now();
    state.activeRequests.delete(requestId);
    await persist();
    await scheduleClose(route);
  }

  chrome.runtime.onMessage.addListener((message, sender) => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    const requestId = String(event.request_id || "");
    if (!requestId) return false;
    if (event.type === "chat.started") {
      keyForRequest(requestId).then(async key => {
        if (!key) return;
        const route = await routeFor(key);
        if (await captureConversation(route, sender)) await persist();
      }).catch(() => {});
      return false;
    }
    if (SUCCESS_TYPES.has(event.type)) {
      completeRequest(event, sender).catch(() => {});
    } else if (FAILURE_TYPES.has(event.type)) {
      failRequest(requestId, `${event.type}-router-retire-v30`).catch(() => {});
    }
    return false;
  });

  chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    if (!changeInfo.url || !conversationId(changeInfo.url)) return;
    ensureLoaded().then(async () => {
      let changed = false;
      for (const route of Object.values(state.routes)) {
        if (route?.tab_id !== tabId) continue;
        route.conversation_id = conversationId(changeInfo.url);
        route.conversation_url = changeInfo.url;
        route.last_active_at = Date.now();
        changed = true;
      }
      if (changed) await persist();
    }).catch(() => {});
  });

  chrome.tabs.onRemoved.addListener(tabId => {
    ensureLoaded().then(async () => {
      let changed = false;
      for (const route of Object.values(state.routes)) {
        if (route?.tab_id !== tabId) continue;
        const requestId = String(route.inflight_request_id || "");
        resetClosedRoute(route, "window-closed-new-chat-next-request");
        if (requestId) state.activeRequests.delete(requestId);
        changed = true;
      }
      if (changed) await persist();
    }).catch(() => {});
  });

  chrome.windows.onRemoved.addListener(windowId => {
    ensureLoaded().then(async () => {
      let changed = false;
      for (const route of Object.values(state.routes)) {
        if (route?.window_id !== windowId) continue;
        const requestId = String(route.inflight_request_id || "");
        resetClosedRoute(route, "window-closed-new-chat-next-request");
        if (requestId) state.activeRequests.delete(requestId);
        changed = true;
      }
      if (changed) await persist();
    }).catch(() => {});
  });

  chrome.alarms.onAlarm.addListener(alarm => {
    if (!alarm?.name?.startsWith(ALARM_PREFIX)) return;
    const windowId = Number(alarm.name.slice(ALARM_PREFIX.length));
    if (!Number.isInteger(windowId)) return;
    ensureLoaded().then(async () => {
      const entry = Object.entries(state.routes).find(([, route]) => route?.window_id === windowId);
      if (!entry) return;
      const [, route] = entry;
      const now = Date.now();
      if (route.inflight_request_id) {
        route.close_after = now + IDLE_CLOSE_MS;
        await chrome.alarms.create(alarm.name, { when: route.close_after });
        await persist();
        return;
      }
      if (Number(route.close_after || 0) > now + 500) {
        await chrome.alarms.create(alarm.name, { when: route.close_after });
        return;
      }
      try { await chrome.windows.remove(windowId); } catch (_) {}
      resetClosedRoute(route, "idle-window-closed-new-chat-next-request");
      await persist();
      const settings = await config().catch(() => ({}));
      if (settings.boundTabId && !await chrome.tabs.get(settings.boundTabId).catch(() => null)) {
        await chrome.storage.local.set({ boundTabId: null });
      }
      await sendExtensionStatus(false).catch(() => {});
    }).catch(() => {});
  });

  ensureLoaded().catch(() => {});
})();
