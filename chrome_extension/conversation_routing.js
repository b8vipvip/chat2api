(() => {
  const KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chat2apiConversationRoutesV1";
  const NEW_CHAT_URL = "https://chatgpt.com/";
  const IDLE_CLOSE_MS = 5 * 1000; // Completed API window closes after 5s; v132 restores baseline total.
  const SLOW_LOAD_MS = 8000;
  const HARD_SLOW_LOAD_MS = 15000;
  const MAX_TURNS = 32;
  const MAX_TEXT_CHARS = 96000;
  const MAX_ATTACHMENTS = 16;
  const ALARM_PREFIX = "chat2api-route-close:";
  const MIN_CHROME_ALARM_MS = 30 * 1000;
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
    // A failed route's window cannot re-enter the persistent standby pool
    // between clearing its route identity and Chrome actually removing it.
    retiringWindows: new Set(),
    // A server terminal can release its key before the asynchronous background
    // route cleanup completes. Serialize only the same key, never all Workers.
    terminalSettlements: new Map(),
    // The router alone assigns isolated route-window leases to parallel requests.
    routeAllocations: new Map(),
    closeTimers: new Map(), // best-effort 5s close; persistent Chrome alarm is fallback
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


  function clearCloseTimer(windowId) {
    const timer = state.closeTimers.get(Number(windowId));
    if (timer) clearTimeout(timer);
    state.closeTimers.delete(Number(windowId));
  }

  async function clearCloseAlarm(route) {
    if (Number.isInteger(route?.window_id)) {
      clearCloseTimer(route.window_id);
      try { await chrome.alarms.clear(`${ALARM_PREFIX}${route.window_id}`); } catch (_) {}
    }
    if (route) route.close_after = null;
  }

  async function scheduleClose(route) {
    if (!Number.isInteger(route?.window_id) || !route.window_owned || route.inflight_request_id) return;
    const windowId = route.window_id;
    route.last_active_at = Date.now();
    route.close_after = route.last_active_at + IDLE_CLOSE_MS;
    const closeAt = route.close_after;
    await persist();
    clearCloseTimer(windowId);
    // Same route owner: normal 5s close + durable MV3 alarm fallback.
    const timer = setTimeout(() => {
      if (state.closeTimers.get(windowId) === timer) state.closeTimers.delete(windowId);
      closeDueWindow(windowId, closeAt).catch(() => {});
    }, IDLE_CLOSE_MS);
    state.closeTimers.set(windowId, timer);
    await chrome.alarms.create(`${ALARM_PREFIX}${windowId}`, {
      when: Math.max(closeAt, Date.now() + MIN_CHROME_ALARM_MS),
    });
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
      conversation_request_scoped_window: route.request_scoped === true,
      conversation_server_spare_window_claim: message?.routing?.worker_verified_spare_window_claim ?? null,
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

  async function resolveForRequestOwned(message, baseKey) {
    const requestId = String(message?.request_id || "");
    if (!requestId) throw new Error("Routed request requires a request ID");
    await ensureLoaded();

    // Standalone API requests are strictly request-scoped: even when another
    // request for this API key finished less than five seconds ago, its route
    // and ChatGPT conversation cannot be assigned again.
    const isolated = ["chat.request", "image.request", "voice.request"].includes(message?.type);
    const liveSession = message?.type === "voice.live.start";
    let key = null;
    let route = null;
    if (isolated || liveSession) {
      key = liveSession ? `${baseKey}::live::${requestId}`
        : `${baseKey}::request::${requestId}`;
      const settlement = state.terminalSettlements.get(key);
      if (settlement) await settlement.catch(() => {});
      route = await routeFor(key);
      if (route.inflight_request_id && route.inflight_request_id !== requestId) {
        const busy = new Error("Request-scoped ChatGPT window belongs to another request");
        busy.code = "worker_request_window_busy";
        throw busy;
      }
    } else {
      // Continuous voice / legacy control sessions preserve existing routing.
      const siblingKeys = Object.keys(state.routes).filter(candidate =>
        candidate !== baseKey && state.routes[candidate]?.api_key_id === baseKey);
      for (const candidate of [baseKey, ...siblingKeys]) {
        const settlement = state.terminalSettlements.get(candidate);
        if (settlement) await settlement.catch(() => {});
        const item = await routeFor(candidate);
        if (item.inflight_request_id && item.inflight_request_id !== requestId &&
            !state.activeRequests.has(String(item.inflight_request_id))) {
          await retireRoute(candidate, item, item.inflight_request_id,
            "restore-stale-router-lease-v147");
        }
        if (item.inflight_request_id && item.inflight_request_id !== requestId) continue;
        key = candidate;
        route = item;
        break;
      }
      if (!route) {
        key = `${baseKey}::parallel::${requestId}`;
        route = await routeFor(key);
      }
    }
    route.api_key_id = baseKey;
    route.request_scoped = isolated || liveSession;
    route.live_session = liveSession;
    route.request_id = requestId;

    const hadLiveTab = Boolean(await liveTab(route));
    await clearCloseAlarm(route);
    const tab = await ensureWindow(key, route);
    route.tab_id = tab.id;
    route.window_id = tab.windowId;
    route.last_active_at = Date.now();
    route.inflight_request_id = requestId;
    state.activeRequests.set(requestId, {
      key,
      kind: requestKind(message),
      prompt_chars: String(message.prompt || "").length,
      attachments: Array.isArray(message.attachments) ? message.attachments.length : (message.audio ? 1 : 0),
      tab_id: tab.id,
      window_id: tab.windowId,
      started_at: Date.now(),
    });
    await persist();
    await emitDiagnostics(message, route, tab, hadLiveTab ? "retry-same-request-window" : "claim-isolated-request-window");
    return tab;
  }

  async function resolveForRequest(message) {
    const baseKey = routingKey(message);
    if (!baseKey) return resolveTargetTab();
    // Serialize only short window allocation for the same logical key; model
    // generation proceeds concurrently once each request owns its own window.
    const previous = state.routeAllocations.get(baseKey);
    const pending = Promise.resolve(previous).catch(() => {})
      .then(() => resolveForRequestOwned(message, baseKey));
    state.routeAllocations.set(baseKey, pending);
    try {
      return await pending;
    } finally {
      if (state.routeAllocations.get(baseKey) === pending) {
        state.routeAllocations.delete(baseKey);
      }
    }
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
    // Revalidate after each asynchronous boundary. A previous request's
    // terminal cleanup may lag the next server-authorized dispatch; it must
    // never clear the replacement request's lease or close its window.
    if (route?.inflight_request_id && requestId && route.inflight_request_id !== requestId) return false;
    const windowId = Number.isInteger(route?.window_id) ? route.window_id : null;
    await clearCloseAlarm(route);
    if (route?.inflight_request_id && requestId && route.inflight_request_id !== requestId) return false;
    if (requestId) state.retiredRequests.add(requestId);
    if (Number.isInteger(windowId)) state.retiringWindows.add(windowId);
    try {
      if (requestId) state.activeRequests.delete(requestId);
      resetClosedRoute(route, reason || "terminal-failure");
      route.last_active_at = Date.now();
      await persist();
      if (Number.isInteger(windowId)) {
        // If a newer request already took this physical window, only the
        // original router owner may decide to retire it later.
        const taken = [...state.activeRequests.values()].some(item => item?.window_id === windowId)
          || Object.values(state.routes).some(item => item?.window_id === windowId && item?.inflight_request_id);
        if (!taken) {
          try { await chrome.windows.remove(windowId); } catch (_) {}
        }
      }
    } finally {
      if (Number.isInteger(windowId)) state.retiringWindows.delete(windowId);
    }
    if (route?.request_scoped === true && state.routes[key] === route) {
      delete state.routes[key];
      await persist();
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

  // v229: same router owns the complete GPT-Live physical lease lifecycle.
  // Content audio turns, response.done and barge-in are NOT session terminals.
  // Recover the exact leased tab from persisted routes after MV3 suspension.
  async function liveTabForRequest(requestId) {
    const key = await keyForRequest(requestId);
    if (!key) return null;
    const route = state.routes[key];
    if (!route?.live_session || route.inflight_request_id !== String(requestId)
        || !Number.isInteger(route.tab_id) || !Number.isInteger(route.window_id)) return null;
    try {
      const tab = await chrome.tabs.get(route.tab_id);
      return tab?.id === route.tab_id && tab.windowId === route.window_id ? tab : null;
    } catch (_) { return null; }
  }

  async function finishLiveSession(requestId, reason = "voice-live-stopped") {
    const key = await keyForRequest(requestId);
    if (!key) return false;
    const route = state.routes[key];
    if (!route?.live_session || route.inflight_request_id !== String(requestId)) return false;
    // A second stop/control from an old client must never close a replacement.
    route.inflight_request_id = null;
    route.live_session = false;
    route.last_rotation_reason = String(reason || "voice-live-stopped").slice(0,90);
    route.last_active_at = Date.now();
    state.activeRequests.delete(String(requestId));
    await persist();
    await scheduleClose(route); // exactly the existing v30 5s + MV3 alarm
    return true;
  }
  state.liveTabForRequest = liveTabForRequest;
  state.finishLiveSession = finishLiveSession;

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
  function enqueueTerminalSettlement(event, sender) {
    const requestId = String(event?.request_id || "");
    if (!requestId) return;
    const knownKey = state.activeRequests.get(requestId)?.key
      || Object.entries(state.routes).find(([, route]) => route?.inflight_request_id === requestId)?.[0];
    // Record the pending cleanup synchronously, before the server sees the
    // terminal and admits the next same-key request.
    const perform = () => SUCCESS_TYPES.has(event.type)
      ? completeRequest(event, sender)
      : failRequest(requestId, `${event.type}-router-retire-v130`);
    if (!knownKey) {
      perform().catch(() => {});
      return;
    }
    const previous = state.terminalSettlements.get(knownKey);
    const pending = Promise.resolve(previous).catch(() => {}).then(perform);
    state.terminalSettlements.set(knownKey, pending);
    pending.finally(() => {
      if (state.terminalSettlements.get(knownKey) === pending) {
        state.terminalSettlements.delete(knownKey);
      }
    }).catch(() => {});
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
    if (SUCCESS_TYPES.has(event.type) || FAILURE_TYPES.has(event.type)) {
      enqueueTerminalSettlement(event, sender);
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


  // A physically removed in-flight tab has no content script left to explain
  // why the API remains running. Record owner-scoped, metadata-only evidence.
  // This is never a second terminal path; only request-v6 decides terminal.
  function reportUnexpectedWindowLoss(route, requestId, kind, id) {
    if (!requestId || typeof trySendSocket !== "function") return;
    const active = state.activeRequests.get(requestId);
    const payload = {
      type: "chat.diagnostics", request_id: requestId,
      diagnostics: {
        worker_window_lost_during_request: true,
        worker_window_loss_kind: kind,
        worker_window_loss_id: Number(id) || null,
        worker_window_loss_request_scoped: route?.request_scoped === true,
        worker_window_loss_owned_tab: Number(active?.tab_id) || null,
        worker_window_loss_owned_window: Number(active?.window_id) || null,
        worker_window_loss_at_ms: Date.now(),
        worker_window_loss_terminal_authority: "request-v6",
      },
    };
    trySendSocket(payload).catch(() => false);
  }

  chrome.tabs.onRemoved.addListener(tabId => {
    ensureLoaded().then(async () => {
      let changed = false;
      for (const [key, route] of Object.entries(state.routes)) {
        if (route?.tab_id !== tabId) continue;
        clearCloseTimer(route.window_id);
        if (Number.isInteger(route.window_id)) {
          chrome.alarms.clear(`${ALARM_PREFIX}${route.window_id}`).catch(() => {});
        }
        const requestId = String(route.inflight_request_id || "");
        if (requestId) reportUnexpectedWindowLoss(route, requestId, "tab-removed", tabId);
        if (requestId) state.activeRequests.delete(requestId);
        if (route.request_scoped === true) delete state.routes[key];
        else resetClosedRoute(route, "window-closed-new-chat-next-request");
        changed = true;
      }
      if (changed) await persist();
    }).catch(() => {});
  });

  chrome.windows.onRemoved.addListener(windowId => {
    ensureLoaded().then(async () => {
      let changed = false;
      clearCloseTimer(windowId);
      chrome.alarms.clear(`${ALARM_PREFIX}${windowId}`).catch(() => {});
      for (const [key, route] of Object.entries(state.routes)) {
        if (route?.window_id !== windowId) continue;
        const requestId = String(route.inflight_request_id || "");
        if (requestId) reportUnexpectedWindowLoss(route, requestId, "window-removed", windowId);
        if (requestId) state.activeRequests.delete(requestId);
        if (route.request_scoped === true) delete state.routes[key];
        else resetClosedRoute(route, "window-closed-new-chat-next-request");
        changed = true;
      }
      if (changed) await persist();
    }).catch(() => {});
  });

  async function closeDueWindow(windowId, expectedCloseAt = null) {
    await ensureLoaded();
    windowId = Number(windowId);
    if (!Number.isInteger(windowId)) return false;
    const entry = Object.entries(state.routes).find(([, item]) => item?.window_id === windowId);
    if (!entry) return false;
    const [key, route] = entry;
    if (expectedCloseAt !== null && Number(route.close_after) !== Number(expectedCloseAt)) return false;
    if (route.inflight_request_id || !Number(route.close_after)) return false;
    if (Date.now() + 100 < Number(route.close_after)) return false;
    if (state.retiringWindows.has(windowId)) return false;
    state.retiringWindows.add(windowId);
    try {
      await chrome.windows.remove(windowId);
      // Recheck exact identity after asynchronous removal.
      if (state.routes[key] === route && route.window_id === windowId && !route.inflight_request_id) {
        if (route.request_scoped === true) delete state.routes[key];
        else resetClosedRoute(route, "idle-window-closed-new-chat-next-request");
        await persist();
      }
    } catch (_) {
      return false; // keep owner and window on a failed close
    } finally {
      state.retiringWindows.delete(windowId);
      clearCloseTimer(windowId);
      chrome.alarms.clear(`${ALARM_PREFIX}${windowId}`).catch(() => {});
    }
    const settings = await config().catch(() => ({}));
    if (settings.boundTabId && !await chrome.tabs.get(settings.boundTabId).catch(() => null)) {
      await chrome.storage.local.set({ boundTabId: null });
    }
    await sendExtensionStatus(false).catch(() => {});
    return true;
  }

  chrome.alarms.onAlarm.addListener(alarm => {
    if (!alarm?.name?.startsWith(ALARM_PREFIX)) return;
    const windowId = Number(alarm.name.slice(ALARM_PREFIX.length));
    if (!Number.isInteger(windowId)) return;
    closeDueWindow(windowId).catch(() => {});
  });

  ensureLoaded().catch(() => {});
})();