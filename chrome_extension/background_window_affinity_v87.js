(() => {
  const KEY = "__CHAT2API_WINDOW_AFFINITY_V87__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const RESERVE_KEY = "__CHAT2API_RESERVE_POOL_V29__";
  const WARM_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";
  const ROUTE_STORAGE_KEY = "chat2apiConversationRoutesV1";
  const RESERVE_STORAGE_KEY = "chat2apiReservePoolV29";
  const WARM_STORAGE_KEY = "chat2apiConversationWarmPoolV2";
  const DISABLED_KEY = "chat2apiWorkerMasterDisabledV61";
  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";
  const IDLE_CLOSE_MS = 5 * 60 * 1000;
  const LEASE_TOUCH_AGE_MS = 10 * 60 * 1000;
  const LEASE_REFRESH_INTERVAL_MS = 5 * 60 * 1000;

  const state = {
    version: 87,
    reserve_fallback_claims: 0,
    successful_route_leases: 0,
    blocked_early_route_closes: 0,
    spare_leases_refreshed: 0,
    protectedWindows: new Map(),
    refreshTimer: null,
    last: null,
  };
  globalThis[KEY] = state;

  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver !== "function") return;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function routeKey(message) {
    const value = message?.routing?.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  function requestModel(message) {
    return String(message?.options?.model || message?.model || "").trim().toLowerCase();
  }

  async function liveTab(tabId) {
    if (!Number.isInteger(tabId)) return null;
    try {
      const tab = await chrome.tabs.get(tabId);
      const url = String(tab?.url || tab?.pendingUrl || "");
      if (typeof isChatGptUrl === "function" && !isChatGptUrl(url)) return null;
      if (typeof isChatGptUrl !== "function" && !/^https:\/\/(?:www\.)?(?:chatgpt\.com|chat\.openai\.com)\//i.test(url)) return null;
      return tab;
    } catch (_) {
      return null;
    }
  }

  async function readiness(tabId) {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const visible = element => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
          };
          const composer = [
            "#prompt-textarea",
            "textarea[placeholder]",
            "div[contenteditable='true'][data-lexical-editor='true']",
            "div[contenteditable='true'].ProseMirror",
          ].some(selector => [...document.querySelectorAll(selector)].some(visible));
          const root = [...document.querySelectorAll("form[data-type='unified-composer'], form")]
            .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || document;
          const rejected = element => /send|submit|voice|microphone|mic|audio|attach|upload|file|tool|添加|附件|上传|语音|麦克风|发送/i.test(
            `${element?.getAttribute?.("aria-label") || ""} ${element?.getAttribute?.("data-testid") || ""} ${element?.innerText || element?.textContent || ""}`,
          );
          const picker = [
            "button[class*='composer-pill'][aria-haspopup='menu']",
            "button[class*='composer-pill'][aria-haspopup='listbox']",
            "button[data-testid*='model' i]",
            "button[aria-label*='model' i]",
            "button[aria-label*='模型']",
            "button[aria-haspopup='menu']",
            "button[aria-haspopup='listbox']",
          ].some(selector => [...root.querySelectorAll(selector)].some(element => visible(element) && !element.disabled && !rejected(element)));
          return { composer, model_picker: picker, document_ready: document.readyState !== "loading" };
        },
      });
      return results?.[0]?.result || { composer: false, model_picker: false, document_ready: false };
    } catch (_) {
      return { composer: false, model_picker: false, document_ready: false };
    }
  }

  async function immediateWarmClaimable(message) {
    const warmPool = globalThis[WARM_KEY];
    const slots = [...(warmPool?.warmSlots?.values?.() || [])];
    if (!slots.length) return false;
    const stored = await chrome.storage.local.get({ accountType: "unknown" }).catch(() => ({}));
    const accountType = String(stored.accountType || "unknown").toLowerCase();
    const requirePicker = message?.type === "chat.request"
      && !(requestModel(message) === "gpt-5.5-mini" && accountType === "free");
    for (const slot of slots) {
      const tab = await liveTab(slot?.tab_id);
      if (!tab) continue;
      const ready = await readiness(tab.id);
      if (ready.composer && (!requirePicker || ready.model_picker)) return true;
    }
    return false;
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
      last_open_ms: 0,
      last_rotation_reason: "reserve-fallback-v87",
      tab_id: null,
      window_id: null,
      window_owned: true,
      inflight_request_id: null,
      last_active_at: 0,
      close_after: null,
      prewarm_claimed_at: null,
      prewarm_load_ms: 0,
    };
  }

  async function persistRouter() {
    const router = globalThis[ROUTER_KEY];
    if (!router?.routes) return;
    await chrome.storage.local.set({ [ROUTE_STORAGE_KEY]: router.routes }).catch(() => {});
  }

  async function persistReserve() {
    const reserve = globalThis[RESERVE_KEY];
    if (!reserve?.reserveSlots) return;
    await chrome.storage.local.set({
      [RESERVE_STORAGE_KEY]: {
        version: 29,
        target: Number(reserve.target || 1),
        slots: [...reserve.reserveSlots.values()].map(item => ({ ...item })),
        updated_at_ms: Date.now(),
      },
    }).catch(() => {});
  }

  async function persistWarm() {
    const warm = globalThis[WARM_KEY];
    if (!warm?.warmSlots) return;
    await chrome.storage.local.set({
      [WARM_STORAGE_KEY]: {
        version: 23,
        slots: [...warm.warmSlots.values()].map(item => ({ ...item })),
      },
    }).catch(() => {});
  }

  async function claimReadyReserve(key, message) {
    const router = globalThis[ROUTER_KEY];
    const reserve = globalThis[RESERVE_KEY];
    if (!router?.routes || !reserve?.reserveSlots) return null;

    const existing = router.routes[key];
    if (existing && await liveTab(existing.tab_id)) return null;
    if (await immediateWarmClaimable(message)) return null;

    let selectedKey = null;
    let selected = null;
    for (const [slotKey, slot] of reserve.reserveSlots.entries()) {
      if (slot?.ready !== true) continue;
      const tab = await liveTab(slot.tab_id);
      if (!tab) continue;
      const ready = await readiness(tab.id);
      if (!ready.composer) continue;
      selectedKey = slotKey;
      selected = { ...slot, tab };
      break;
    }
    if (!selected || !selectedKey) return null;

    const route = existing || freshRoute(key);
    route.conversation_id = null;
    route.conversation_url = null;
    route.tab_id = selected.tab.id;
    route.window_id = selected.tab.windowId;
    route.window_owned = true;
    route.inflight_request_id = null;
    route.last_active_at = Date.now();
    route.close_after = null;
    route.prewarm_claimed_at = Date.now();
    route.prewarm_load_ms = Math.max(0, Date.now() - Number(selected.created_at_ms || Date.now()));
    route.prewarm_ready_age_ms = Math.max(0, Date.now() - Number(selected.ready_at_ms || Date.now()));
    route.prewarm_source = "reserve-fallback-v87";
    route.last_rotation_reason = "reserve-fallback-v87";
    router.routes[key] = route;
    reserve.reserveSlots.delete(selectedKey);
    await Promise.all([persistRouter(), persistReserve()]);

    state.reserve_fallback_claims += 1;
    state.last = {
      action: "reserve-fallback-claimed",
      request_id: String(message?.request_id || ""),
      api_key_id: key,
      tab_id: selected.tab.id,
      window_id: selected.tab.windowId,
      at_ms: Date.now(),
    };
    await chrome.storage.local.set({ chat2apiWindowAffinityV87: state.last }).catch(() => {});
    if (message?.request_id && typeof trySendSocket === "function") {
      await trySendSocket({
        type: message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics",
        request_id: message.request_id,
        diagnostics: {
          conversation_reserve_fallback_v87: true,
          conversation_reserve_fallback_reason: "warm-not-immediately-claimable",
          routed_tab_id: selected.tab.id,
          routed_window_id: selected.tab.windowId,
        },
      }).catch(() => false);
    }
    setTimeout(() => reserve.reconcile?.().catch?.(() => {}), 800);
    return selected.tab;
  }

  globalThis.resolveTargetTabForRequest = async function resolveWithHealthyReserveFallback(message) {
    const key = routeKey(message);
    if (key) {
      const router = globalThis[ROUTER_KEY];
      const route = router?.routes?.[key];
      if (Number.isInteger(route?.window_id)) state.protectedWindows.delete(route.window_id);
      await claimReadyReserve(key, message).catch(() => null);
    }
    return baseResolver(message);
  };

  async function protectCompletedRoute(requestId, sender) {
    await sleep(0);
    const router = globalThis[ROUTER_KEY];
    if (!router?.routes) return false;
    const tabId = Number.isInteger(sender?.tab?.id) ? sender.tab.id : null;
    const windowId = Number.isInteger(sender?.tab?.windowId) ? sender.tab.windowId : null;
    let selected = null;
    for (const [key, route] of Object.entries(router.routes)) {
      if (tabId !== null && route?.tab_id === tabId) { selected = { key, route }; break; }
      if (windowId !== null && route?.window_id === windowId) selected = { key, route };
    }
    if (!selected || !Number.isInteger(selected.route?.window_id)) return false;

    const now = Date.now();
    selected.route.last_active_at = now;
    selected.route.close_after = now + IDLE_CLOSE_MS;
    await chrome.alarms.create(`${ROUTE_ALARM_PREFIX}${selected.route.window_id}`, { when: selected.route.close_after }).catch(() => {});
    await persistRouter();
    state.protectedWindows.set(selected.route.window_id, {
      key: selected.key,
      request_id: String(requestId || ""),
      until: selected.route.close_after,
    });
    state.successful_route_leases += 1;
    state.last = {
      action: "successful-route-protected-5m",
      request_id: String(requestId || ""),
      api_key_id: selected.key,
      window_id: selected.route.window_id,
      close_after: selected.route.close_after,
      at_ms: now,
    };
    await chrome.storage.local.set({ chat2apiWindowAffinityV87: state.last }).catch(() => {});
    return true;
  }

  const baseWindowRemove = chrome.windows.remove.bind(chrome.windows);
  try {
    chrome.windows.remove = async function removeWithSuccessfulRouteLease(windowId) {
      const protection = state.protectedWindows.get(windowId);
      if (!protection || Date.now() >= Number(protection.until || 0)) {
        state.protectedWindows.delete(windowId);
        return baseWindowRemove(windowId);
      }
      const stored = await chrome.storage.local.get({ [DISABLED_KEY]: false }).catch(() => ({}));
      const router = globalThis[ROUTER_KEY];
      const route = router?.routes?.[protection.key];
      const stillSameRoute = route?.window_id === windowId && !route?.inflight_request_id;
      if (stored?.[DISABLED_KEY] === true || !stillSameRoute) {
        state.protectedWindows.delete(windowId);
        return baseWindowRemove(windowId);
      }
      state.blocked_early_route_closes += 1;
      state.last = {
        action: "blocked-early-success-route-close",
        request_id: protection.request_id,
        api_key_id: protection.key,
        window_id: windowId,
        protected_until: protection.until,
        at_ms: Date.now(),
      };
      await chrome.storage.local.set({ chat2apiWindowAffinityV87: state.last }).catch(() => {});
      await chrome.alarms.create(`${ROUTE_ALARM_PREFIX}${windowId}`, { when: protection.until }).catch(() => {});
      return undefined;
    };
  } catch (_) {}

  async function refreshHealthySpareLeases() {
    const now = Date.now();
    let changedReserve = false;
    let changedWarm = false;
    let refreshed = 0;
    const reserve = globalThis[RESERVE_KEY];
    const warm = globalThis[WARM_KEY];

    for (const slot of reserve?.reserveSlots?.values?.() || []) {
      if (slot?.ready !== true) continue;
      const age = Math.max(0, now - Number(slot.ready_at_ms || 0));
      if (age < LEASE_TOUCH_AGE_MS) continue;
      const tab = await liveTab(slot.tab_id);
      if (!tab) continue;
      const ready = await readiness(tab.id);
      if (!ready.composer) continue;
      slot.ready_at_ms = now;
      changedReserve = true;
      refreshed += 1;
    }

    for (const slot of warm?.warmSlots?.values?.() || []) {
      const age = Math.max(0, now - Number(slot?.ready_at_ms || slot?.created_at_ms || 0));
      if (age < LEASE_TOUCH_AGE_MS) continue;
      const tab = await liveTab(slot?.tab_id);
      if (!tab) continue;
      const ready = await readiness(tab.id);
      if (!ready.composer) continue;
      slot.ready_at_ms = now;
      changedWarm = true;
      refreshed += 1;
    }

    if (changedReserve) await persistReserve();
    if (changedWarm) await persistWarm();
    if (refreshed) {
      state.spare_leases_refreshed += refreshed;
      state.last = { action: "healthy-spare-lease-refreshed", count: refreshed, at_ms: now };
      await chrome.storage.local.set({ chat2apiWindowAffinityV87: state.last }).catch(() => {});
    }
    return refreshed;
  }

  chrome.runtime.onMessage.addListener((message, sender) => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (event.type === "chat.completed" || event.type === "image.completed") {
      protectCompletedRoute(event.request_id, sender).catch(() => {});
    }
    return false;
  });

  state.refreshHealthySpareLeases = refreshHealthySpareLeases;
  state.claimReadyReserve = claimReadyReserve;
  state.protectCompletedRoute = protectCompletedRoute;
  state.constants = Object.freeze({
    idle_close_ms: IDLE_CLOSE_MS,
    spare_lease_touch_age_ms: LEASE_TOUCH_AGE_MS,
    spare_lease_refresh_interval_ms: LEASE_REFRESH_INTERVAL_MS,
  });

  setTimeout(() => refreshHealthySpareLeases().catch(() => {}), 1000);
  state.refreshTimer = setInterval(() => refreshHealthySpareLeases().catch(() => {}), LEASE_REFRESH_INTERVAL_MS);
})();
