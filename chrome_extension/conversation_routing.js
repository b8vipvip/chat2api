(() => {
  const KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chat2apiConversationRoutesV1";
  const NEW_CHAT_URL = "https://chatgpt.com/";
  const IDLE_CLOSE_MS = 300000;
  const SLOW_LOAD_MS = 8000;
  const HARD_SLOW_LOAD_MS = 15000;
  const MAX_TURNS = 32;
  const MAX_TEXT_CHARS = 96000;
  const MAX_ATTACHMENTS = 16;
  const ALARM_PREFIX = "chat2api-route-close:";
  const state = { loaded: false, routes: {}, openings: new Map(), activeRequests: new Map() };
  globalThis[KEY] = state;

  const sleepLocal = ms => new Promise(resolve => setTimeout(resolve, ms));

  function conversationId(url = "") {
    try {
      const parsed = new URL(url);
      if (!["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(parsed.hostname)) return null;
      const match = parsed.pathname.match(/\/c\/([^/?#]+)/i);
      return match ? decodeURIComponent(match[1]) : null;
    } catch (_) { return null; }
  }

  function routingKey(message) {
    const value = message?.routing?.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  function requestKind(message) {
    if (message?.type === "voice.request") return "voice";
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
    await chrome.storage.local.set({ [STORAGE_KEY]: state.routes });
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
        if (!isChatGptUrl(url) || url.includes("/images")) {
          await sleepLocal(180);
          continue;
        }
        if (tab.status && tab.status !== "complete") {
          await sleepLocal(180);
          continue;
        }
        await ensureContent(tabId);
        return { tab, load_ms: Date.now() - started };
      } catch (error) {
        lastError = error;
      }
      await sleepLocal(220);
    }
    throw lastError || new Error("Timed out waiting for routed ChatGPT conversation");
  }

  async function liveTab(route) {
    if (!Number.isInteger(route?.tab_id)) return null;
    try {
      const tab = await chrome.tabs.get(route.tab_id);
      if (!isChatGptUrl(tab.url || tab.pendingUrl || "")) return null;
      return tab;
    } catch (_) { return null; }
  }

  async function clearCloseAlarm(route) {
    if (!Number.isInteger(route?.window_id)) return;
    try { await chrome.alarms.clear(`${ALARM_PREFIX}${route.window_id}`); } catch (_) {}
    route.close_after = null;
  }

  async function scheduleClose(key, route) {
    if (!Number.isInteger(route?.window_id) || !route.window_owned) return;
    route.last_active_at = Date.now();
    route.close_after = route.last_active_at + IDLE_CLOSE_MS;
    await chrome.alarms.create(`${ALARM_PREFIX}${route.window_id}`, { when: route.close_after });
    await persist();
  }

  async function navigateFresh(route, reason) {
    if (!Number.isInteger(route.tab_id)) throw new Error("Cannot rotate conversation without a routed tab");
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
    return ready.tab;
  }

  async function openWindowForRoute(key, route) {
    const budgetReason = routeOverBudget(route);
    if (budgetReason) {
      route.conversation_id = null;
      route.conversation_url = null;
      route.generation = Number(route.generation || 1) + 1;
      route.turn_count = 0;
      route.text_chars = 0;
      route.attachment_count = 0;
      route.slow_load_strikes = 0;
      route.last_rotation_reason = budgetReason;
    }

    const requestedUrl = route.conversation_url || NEW_CHAT_URL;
    const expectedConversation = route.conversation_id;
    const created = await chrome.windows.create({ url: requestedUrl, focused: false, type: "normal" });
    if (!created?.id) throw new Error("Chrome did not create a routed ChatGPT window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item.id)) || null;
    }
    if (!tab?.id) throw new Error("Routed ChatGPT window contains no usable tab");

    route.window_id = created.id;
    route.tab_id = tab.id;
    route.window_owned = true;
    route.last_active_at = Date.now();
    await chrome.storage.local.set({ boundTabId: tab.id, autoBind: false, modelsUpdatedAt: 0 });

    let ready = await waitForChatReady(tab.id);
    route.last_open_ms = ready.load_ms;

    if (expectedConversation) {
      const actualConversation = conversationId(ready.tab.url || ready.tab.pendingUrl || "");
      if (actualConversation !== expectedConversation) {
        ready.tab = await navigateFresh(route, "saved-conversation-unavailable");
      } else {
        if (ready.load_ms >= HARD_SLOW_LOAD_MS) {
          route.slow_load_strikes = 2;
          ready.tab = await navigateFresh(route, `single-load>=${HARD_SLOW_LOAD_MS}ms`);
        } else if (ready.load_ms >= SLOW_LOAD_MS) {
          route.slow_load_strikes = Number(route.slow_load_strikes || 0) + 1;
          if (route.slow_load_strikes >= 2) ready.tab = await navigateFresh(route, `two-loads>=${SLOW_LOAD_MS}ms`);
        } else {
          route.slow_load_strikes = 0;
        }
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

    // A closed/missing browser window ends that browser conversation session. Never
    // reopen a saved /c/... URL on the next API call; start a fresh ChatGPT chat.
    resetClosedRoute(route, "closed-window-new-chat");
    route.tab_id = null;
    route.window_id = null;
    route.inflight_request_id = null;
    route.close_after = null;
    if (state.openings.has(key)) return state.openings.get(key);
    const promise = openWindowForRoute(key, route).finally(() => state.openings.delete(key));
    state.openings.set(key, promise);
    return promise;
  }

  async function emitDiagnostics(message, route, tab, strategy) {
    const diagnostics = {
      conversation_router: "per-key-v1",
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
      conversation_thresholds: {
        max_turns: MAX_TURNS,
        max_text_chars: MAX_TEXT_CHARS,
        max_attachments: MAX_ATTACHMENTS,
        slow_load_ms: SLOW_LOAD_MS,
        hard_slow_load_ms: HARD_SLOW_LOAD_MS,
      },
      routed_tab_id: tab?.id ?? null,
      routed_window_id: tab?.windowId ?? route.window_id ?? null,
    };
    if (message.type === "chat.request") {
      await trySendSocket({ type: "chat.diagnostics", request_id: message.request_id, diagnostics });
    } else {
      await trySendSocket({
        type: "image.diagnostics",
        kind: message.type === "voice.request" ? "voice" : "image",
        request_id: message.request_id,
        diagnostics,
      });
    }
  }

  async function resolveForRequest(message) {
    const key = routingKey(message);
    if (!key) return resolveTargetTab();
    const route = await routeFor(key);
    const hadLiveTab = Boolean(await liveTab(route));
    await clearCloseAlarm(route);
    const tab = await ensureWindow(key, route);
    route.tab_id = tab.id;
    route.window_id = tab.windowId;
    route.last_active_at = Date.now();
    route.inflight_request_id = message.request_id || null;
    const meta = {
      key,
      kind: requestKind(message),
      prompt_chars: String(message.prompt || "").length,
      attachments: Array.isArray(message.attachments) ? message.attachments.length : (message.audio ? 1 : 0),
      tab_id: tab.id,
      window_id: tab.windowId,
      started_at: Date.now(),
    };
    state.activeRequests.set(message.request_id, meta);
    await persist();
    await emitDiagnostics(message, route, tab, hadLiveTab ? "reuse-live-window" : "new-chat-window");
    return tab;
  }

  globalThis.resolveTargetTabForRequest = resolveForRequest;
  globalThis.chat2apiConversationRoutingConfig = Object.freeze({
    idle_close_ms: IDLE_CLOSE_MS,
    max_turns: MAX_TURNS,
    max_text_chars: MAX_TEXT_CHARS,
    max_attachments: MAX_ATTACHMENTS,
    slow_load_ms: SLOW_LOAD_MS,
    hard_slow_load_ms: HARD_SLOW_LOAD_MS,
  });

  async function keyForRequest(requestId) {
    const active = state.activeRequests.get(requestId);
    if (active?.key) return active.key;
    await ensureLoaded();
    for (const [key, route] of Object.entries(state.routes)) {
      if (route?.inflight_request_id === requestId) return key;
    }
    return null;
  }

  async function captureConversation(route, sender) {
    let url = sender?.tab?.url || "";
    if (!conversationId(url) && Number.isInteger(route.tab_id)) {
      try { url = (await chrome.tabs.get(route.tab_id)).url || url; } catch (_) {}
    }
    const id = conversationId(url);
    if (!id) return false;
    route.conversation_id = id;
    route.conversation_url = url;
    return true;
  }

  async function finishRequest(event, sender) {
    const requestId = event?.request_id;
    if (!requestId) return;
    const key = await keyForRequest(requestId);
    if (!key) return;
    const route = await routeFor(key);
    const active = state.activeRequests.get(requestId) || {};
    await captureConversation(route, sender);

    const completedChat = event.type === "chat.completed";
    const completedVoice = event.type === "image.completed" && event.kind === "voice";
    if (completedChat || completedVoice) {
      const completionChars = completedChat
        ? String(event.text || "").length
        : String(event.voice?.transcript || "").length;
      route.turn_count = Number(route.turn_count || 0) + 1;
      route.text_chars = Number(route.text_chars || 0) + Number(active.prompt_chars || 0) + completionChars;
      route.attachment_count = Number(route.attachment_count || 0) + Number(active.attachments || 0);
    }

    if (route.inflight_request_id === requestId) route.inflight_request_id = null;
    route.last_active_at = Date.now();
    state.activeRequests.delete(requestId);
    await persist();
    await scheduleClose(key, route);
  }

  chrome.runtime.onMessage.addListener((message, sender) => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (event.type === "chat.started") {
      keyForRequest(event.request_id).then(async key => {
        if (!key) return;
        const route = await routeFor(key);
        if (await captureConversation(route, sender)) await persist();
      }).catch(() => {});
      return false;
    }
    if (["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(event.type)) {
      finishRequest(event, sender).catch(() => {});
    }
    return false;
  });

  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
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
        resetClosedRoute(route, "window-closed-new-chat-next-request");
        route.tab_id = null;
        route.window_id = null;
        route.inflight_request_id = null;
        route.close_after = null;
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
        resetClosedRoute(route, "window-closed-new-chat-next-request");
        route.tab_id = null;
        route.window_id = null;
        route.inflight_request_id = null;
        route.close_after = null;
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
      const [key, route] = entry;
      const now = Date.now();
      if (route.inflight_request_id && now - Number(route.last_active_at || 0) < 10 * 60 * 1000) {
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
      route.tab_id = null;
      route.window_id = null;
      route.inflight_request_id = null;
      route.close_after = null;
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
