(() => {
  const KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chat2apiConversationRoutesV1";
  const NEW_CHAT_URL = "https://chatgpt.com/";
  const IDLE_CLOSE_MS = 5 * 60 * 1000;
  const REQUEST_WINDOW_CLOSE_MS = 5 * 1000;
  // Chrome 120+ may clamp alarms below 30s. Keep a 5s in-memory timer and a
  // 30s persistent alarm as a service-worker-restart fallback.
  const CHROME_ALARM_MIN_DELAY_MS = 30 * 1000;
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
    // A failed route's window cannot re-enter the persistent standby pool
    // between clearing its route identity and Chrome actually removing it.
    retiringWindows: new Set(),
    // A server terminal can release its key before the asynchronous background
    // route cleanup completes. Serialize only the same key, never all Workers.
    terminalSettlements: new Map(),
    // The router alone assigns isolated route-window leases to parallel requests.
    routeAllocations: new Map(),
    closeTimers: new Map(),
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

  function routePolicyForRequest(message) {
    const apiKeyId = routingKey(message);
    if (!apiKeyId) return { key: null, logical_api_key_id: null, lifecycle_mode: "unrouted", request_scoped: false, continuous: false, close_delay_ms: IDLE_CLOSE_MS };
    const requestId = String(message?.request_id || "").trim();
    const type = String(message?.type || "");
    if (requestId && type === "voice.live.start") {
      return {
        key: `${apiKeyId}::voice-live::${requestId}`,
        logical_api_key_id: apiKeyId,
        lifecycle_mode: "continuous-voice-live",
        request_scoped: true,
        continuous: true,
        close_delay_ms: REQUEST_WINDOW_CLOSE_MS,
      };
    }
    if (requestId && ["chat.request", "image.request", "voice.request"].includes(type)) {
      return {
        key: `${apiKeyId}::request::${requestId}`,
        logical_api_key_id: apiKeyId,
        lifecycle_mode: "request-scoped",
        request_scoped: true,
        continuous: false,
        close_delay_ms: REQUEST_WINDOW_CLOSE_MS,
      };
    }
    return {
      key: apiKeyId,
      logical_api_key_id: apiKeyId,
      lifecycle_mode: "api-key-legacy",
      request_scoped: false,
      continuous: false,
      close_delay_ms: IDLE_CLOSE_MS,
    };
  }

  state.routePolicyForRequest = routePolicyForRequest;
  state.request_window_close_ms = REQUEST_WINDOW_CLOSE_MS;

  function requestKind(message) {
    if (message?.type === "voice.request" || message?.type === "voice.live.start") return "voice";
    if (message?.type === "image.request") return "image";
    return "chat";
  }

  function freshRoute(key) {
    return {
      api_key_id: key,
      logical_api_key_id: key,
      request_id: null,
      request_scoped: false,
      lifecycle_mode: "api-key-legacy",
      continuous: false,
      close_delay_ms: IDLE_CLOSE_MS,
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

  function clearWindowCloseTimer(windowId) {
    if (windowId == null) return;
    const id = Number(windowId);
    if (!Number.isInteger(id)) return;
    const timer = state.closeTimers.get(id);
    if (timer) clearTimeout(timer);
    state.closeTimers.delete(id);
  }

  async function clearCloseAlarm(route) {
    const windowId = route?.window_id;
    if (Number.isInteger(windowId)) {
      clearWindowCloseTimer(windowId);
      try { await chrome.alarms.clear(`${ALARM_PREFIX}${windowId}`); } catch (_) {}
    }
    if (route) route.close_after = null;
  }

  async function scheduleClose(route) {
    if (!Number.isInteger(route?.window_id) || !route.window_owned || route.inflight_request_id) return;
    const windowId = Number(route.window_id);
    const delayMs = Math.max(1000, Number(route.close_delay_ms || IDLE_CLOSE_MS));
    route.last_active_at = Date.now();
    route.close_after = route.last_active_at + delayMs;
    await persist();

    clearWindowCloseTimer(windowId);
    const expectedCloseAt = route.close_after;
    const timer = setTimeout(() => {
      if (state.closeTimers.get(windowId) === timer) state.closeTimers.delete(windowId);
      closeDueWindow(windowId, expectedCloseAt).catch(() => {});
    }, delayMs);
    state.closeTimers.set(windowId, timer);

    // Persistent fallback survives MV3 service-worker suspension. Chrome may
    // clamp a short alarm to 30s; the in-memory timer performs the normal 5s close.
    const alarmWhen = delayMs < CHROME_ALARM_MIN_DELAY_MS
      ? Date.now() + CHROME_ALARM_MIN_DELAY_MS
      : route.close_after;
    await chrome.alarms.create(`${ALARM_PREFIX}${windowId}`, { when: alarmWhen });
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
      conversation_api_key_id: route.logical_api_key_id || route.api_key_id,
      conversation_lifecycle_mode: route.lifecycle_mode || "api-key-legacy",
      conversation_request_scoped_window: route.request_scoped === true,
      conversation_continuous_voice_session: route.continuous === true,
      conversation_window_close_delay_ms: Number(route.close_delay_ms || IDLE_CLOSE_MS),
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

    const policy = routePolicyForRequest(message);
    let key = policy.key || baseKey;
    let route = null;

    if (policy.request_scoped) {
      const settlement = state.terminalSettlements.get(key);
      if (settlement) await settlement.catch(() => {});
      route = await routeFor(key);
      route.api_key_id = baseKey;
      route.logical_api_key_id = baseKey;
      route.request_id = requestId;
      route.request_scoped = true;
      route.lifecycle_mode = policy.lifecycle_mode;
      route.continuous = policy.continuous === true;
      route.close_delay_ms = policy.close_delay_ms;

      // Retire legacy per-key leases left by an older Worker bundle. Never
      // interrupt a locally live request; its own terminal will close it.
      const legacyRoute = state.routes[baseKey];
      if (legacyRoute && legacyRoute !== route) {
        const staleRequestId = String(legacyRoute.inflight_request_id || "");
        if (staleRequestId && staleRequestId !== requestId &&
            !state.activeRequests.has(staleRequestId)) {
          legacyRoute.request_scoped = true;
          legacyRoute.lifecycle_mode = "retired-legacy-route";
          legacyRoute.close_delay_ms = REQUEST_WINDOW_CLOSE_MS;
          await retireRoute(baseKey, legacyRoute, staleRequestId, "legacy-api-key-route-stale-v189");
        } else if (!staleRequestId && Number.isInteger(legacyRoute.window_id)) {
          legacyRoute.request_scoped = true;
          legacyRoute.lifecycle_mode = "retired-legacy-route";
          legacyRoute.close_delay_ms = REQUEST_WINDOW_CLOSE_MS;
          await scheduleClose(legacyRoute);
        }
      }

      if (route.inflight_request_id && route.inflight_request_id !== requestId) {
        const oldRequestId = String(route.inflight_request_id);
        if (state.activeRequests.has(oldRequestId)) {
          const busy = new Error("Request-scoped ChatGPT window is still owned by another request");
          busy.code = "worker_request_window_busy";
          busy.retry_after_ms = 250;
          throw busy;
        }
        await retireRoute(key, route, oldRequestId, "restore-stale-request-window-v189");
      }
    } else {
      // Compatibility path for non-request-scoped legacy/control routes.
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
        route.api_key_id = baseKey;
      }
      route.logical_api_key_id = baseKey;
      route.request_id = requestId;
      route.request_scoped = policy.request_scoped === true;
      route.lifecycle_mode = policy.lifecycle_mode;
      route.continuous = policy.continuous === true;
      route.close_delay_ms = policy.close_delay_ms;
    }

    // A pre-ACK retry may legitimately reuse the same request ID/window. Remove
    // only that request's prior retirement marker before taking its lease again.
    state.retiredRequests.delete(requestId);
    const hadLiveTab = Boolean(await liveTab(route));
    await clearCloseAlarm(route);
    const tab = await ensureWindow(key, route);
    route.api_key_id = baseKey;
    route.logical_api_key_id = baseKey;
    route.request_id = requestId;
    route.request_scoped = policy.request_scoped === true;
    route.lifecycle_mode = policy.lifecycle_mode;
    route.continuous = policy.continuous === true;
    route.close_delay_ms = policy.close_delay_ms;
    route.tab_id = tab.id;
    route.window_id = tab.windowId;
    route.last_active_at = Date.now();
    route.inflight_request_id = requestId;
    state.activeRequests.set(requestId, {
      key,
      kind: requestKind(message),
      lifecycle_mode: route.lifecycle_mode,
      continuous: route.continuous,
      prompt_chars: String(message.prompt || "").length,
      attachments: Array.isArray(message.attachments) ? message.attachments.length : (message.audio ? 1 : 0),
      tab_id: tab.id,
      window_id: tab.windowId,
      started_at: Date.now(),
    });
    await persist();
    await emitDiagnostics(message, route, tab, hadLiveTab ? "reuse-same-request-window" : "claim-request-scoped-window");
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
    if (route?.request_id && requestId && route.request_id !== requestId) return false;
    const windowId = Number.isInteger(route?.window_id) ? route.window_id : null;
    await clearCloseAlarm(route);
    if (route?.inflight_request_id && requestId && route.inflight_request_id !== requestId) return false;
    if (requestId) state.retiredRequests.add(requestId);
    if (requestId) state.activeRequests.delete(requestId);

    if (Number.isInteger(windowId) && route?.request_scoped === true) {
      // Request-scoped windows stay visible for 5s after any terminal, then the
      // router closes them. They remain excluded from standby until Chrome removes them.
      route.inflight_request_id = null;
      route.close_delay_ms = REQUEST_WINDOW_CLOSE_MS;
      route.last_active_at = Date.now();
      route.last_rotation_reason = String(reason || "terminal-failure");
      await persist();
      await scheduleClose(route);
    } else {
      // Compatibility cleanup for pre-v189 API-key routes. Keep the old exact-owner
      // race guard so a late terminal cannot close a window already borrowed elsewhere.
      if (Number.isInteger(windowId)) state.retiringWindows.add(windowId);
      try {
        resetClosedRoute(route, reason || "terminal-failure");
        route.last_active_at = Date.now();
        await persist();
        if (Number.isInteger(windowId)) {
          const taken = [...state.activeRequests.values()].some(item => item?.window_id === windowId)
            || Object.values(state.routes).some(item => item !== route && item?.window_id === windowId && item?.inflight_request_id);
          if (!taken) {
            try { await chrome.windows.remove(windowId); } catch (_) {}
          }
        }
      } finally {
        if (Number.isInteger(windowId)) state.retiringWindows.delete(windowId);
      }
      if (route?.request_scoped && state.routes[key] === route) delete state.routes[key];
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

  async function finishLiveSession(requestId) {
    requestId = String(requestId || "");
    if (!requestId) return false;
    await ensureLoaded();
    const active = state.activeRequests.get(requestId);
    const entry = active?.key
      ? [active.key, state.routes[active.key]]
      : Object.entries(state.routes).find(([, item]) =>
          item?.lifecycle_mode === "continuous-voice-live" && String(item?.request_id || "") === requestId);
    if (!entry?.[1]) return false;
    const [key, route] = entry;
    if (route.lifecycle_mode !== "continuous-voice-live" || String(route.request_id || "") !== requestId) return false;
    if (route.inflight_request_id && route.inflight_request_id !== requestId) return false;
    await clearCloseAlarm(route);
    if (route.inflight_request_id === requestId) route.inflight_request_id = null;
    state.activeRequests.delete(requestId);
    route.close_delay_ms = REQUEST_WINDOW_CLOSE_MS;
    route.last_active_at = Date.now();
    route.last_rotation_reason = "voice-live-session-ended-v189";
    await persist();
    await scheduleClose(route);
    return true;
  }
  state.finishLiveSession = finishLiveSession;

  async function completeRequest(event, sender) {
    const requestId = String(event?.request_id || "");
    if (!requestId) return;
    const key = await keyForRequest(requestId);
    if (!key) return;
    const route = await routeFor(key);
    if (route.request_id && route.request_id !== requestId) return;
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
    if (route.lifecycle_mode !== "continuous-voice-live") route.close_delay_ms = REQUEST_WINDOW_CLOSE_MS;
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

  chrome.tabs.onRemoved.addListener(tabId => {
    ensureLoaded().then(async () => {
      let changed = false;
      for (const [key, route] of Object.entries(state.routes)) {
        if (route?.tab_id !== tabId) continue;
        const requestId = String(route.inflight_request_id || "");
        clearWindowCloseTimer(route.window_id);
        if (Number.isInteger(route.window_id)) chrome.alarms.clear(`${ALARM_PREFIX}${route.window_id}`).catch(() => {});
        if (requestId) state.activeRequests.delete(requestId);
        if (route.request_scoped) delete state.routes[key];
        else resetClosedRoute(route, "window-closed-new-chat-next-request");
        changed = true;
      }
      if (changed) await persist();
    }).catch(() => {});
  });

  chrome.windows.onRemoved.addListener(windowId => {
    ensureLoaded().then(async () => {
      let changed = false;
      clearWindowCloseTimer(windowId);
      chrome.alarms.clear(`${ALARM_PREFIX}${Number(windowId)}`).catch(() => {});
      for (const [key, route] of Object.entries(state.routes)) {
        if (Number(route?.window_id) !== Number(windowId)) continue;
        const requestId = String(route.inflight_request_id || "");
        if (requestId) state.activeRequests.delete(requestId);
        if (route.request_scoped) delete state.routes[key];
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
    const entry = Object.entries(state.routes).find(([, route]) => Number(route?.window_id) === windowId);
    if (!entry) {
      clearWindowCloseTimer(windowId);
      await chrome.alarms.clear(`${ALARM_PREFIX}${windowId}`).catch(() => {});
      return false;
    }
    const [key, route] = entry;
    if (expectedCloseAt != null && Number(route.close_after || 0) !== Number(expectedCloseAt)) return false;
    const closeAfter = Number(route.close_after || 0);
    if (!closeAfter) return false;
    if (route.inflight_request_id) {
      // A delayed timer/alarm may race a new request admission. The active
      // lease wins; its terminal will arm a fresh close deadline.
      clearWindowCloseTimer(windowId);
      route.close_after = null;
      await chrome.alarms.clear(`${ALARM_PREFIX}${windowId}`).catch(() => {});
      await persist();
      return false;
    }
    const now = Date.now();
    if (closeAfter > now + 500) {
      const delayMs = closeAfter - now;
      clearWindowCloseTimer(windowId);
      const timer = setTimeout(() => {
        if (state.closeTimers.get(windowId) === timer) state.closeTimers.delete(windowId);
        closeDueWindow(windowId, closeAfter).catch(() => {});
      }, delayMs);
      state.closeTimers.set(windowId, timer);
      const alarmWhen = delayMs < CHROME_ALARM_MIN_DELAY_MS ? now + CHROME_ALARM_MIN_DELAY_MS : closeAfter;
      await chrome.alarms.create(`${ALARM_PREFIX}${windowId}`, { when: alarmWhen });
      return false;
    }

    clearWindowCloseTimer(windowId);
    await chrome.alarms.clear(`${ALARM_PREFIX}${windowId}`).catch(() => {});
    // Recheck exact ownership after asynchronous alarm cleanup.
    if (state.routes[key] !== route || Number(route.window_id) !== windowId || route.inflight_request_id) return false;
    try { await chrome.windows.remove(windowId); } catch (_) {}
    if (state.routes[key] === route && Number(route.window_id) === windowId && !route.inflight_request_id) {
      if (route.request_scoped) delete state.routes[key];
      else resetClosedRoute(route, "idle-window-closed-new-chat-next-request");
      await persist();
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