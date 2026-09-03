(() => {
  const KEY = "__CHAT2API_RESERVE_POOL_V29__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const WARM_POOL_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";
  const OWN_STORAGE_KEY = "chat2apiReservePoolV29";
  const ROUTE_STORAGE_KEY = "chat2apiConversationRoutesV1";
  const WARM_STORAGE_KEY = "chat2apiConversationWarmPoolV2";
  const CONFIG_REFRESH_MS = 5000;
  const RECONCILE_DELAY_MS = 180;
  const CREATE_BATCH = 4;
  const READY_TIMEOUT_MS = 45000;
  const ROUTE_IDLE_CLOSE_MS = 2 * 60 * 1000;
  const MAX_RESERVE_READY_AGE_MS = 30 * 60 * 1000;
  const MAX_TARGET = 32;
  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";

  const state = {
    reserveSlots: new Map(),
    target: 1,
    loaded: false,
    configRefreshedAt: 0,
    configTimer: null,
    reconcileTimer: null,
    reconcileInFlight: null,
    reportTimer: null,
    reportInFlight: null,
    lastReportSignature: "",
  };
  globalThis[KEY] = state;
  globalThis.chat2apiReservePoolV29 = state;

  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver !== "function") return;

  const sleepReserve = ms => new Promise(resolve => setTimeout(resolve, ms));

  function normalizeTarget(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed <= 0) return 1;
    return Math.max(1, Math.min(MAX_TARGET, Math.floor(parsed)));
  }

  function isChatGpt(value = "") {
    if (typeof isChatGptUrl === "function") return isChatGptUrl(value);
    try {
      return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname);
    } catch (_) {
      return false;
    }
  }

  async function settings() {
    if (typeof config === "function") return config().catch(() => ({}));
    return chrome.storage.local.get({
      serverUrl: "",
      clientId: "",
      clientToken: "",
      socketState: "disconnected",
      networkExternalReady: false,
      chatgptLoginState: "unknown",
      chatgptLoginComposerReady: false,
    }).catch(() => ({}));
  }

  async function bulkPrewarmEligible() {
    const stored = await chrome.storage.local.get({
      clientId: "",
      clientToken: "",
      socketState: "disconnected",
      networkExternalReady: false,
      chatgptLoginState: "unknown",
      chatgptLoginComposerReady: false,
    }).catch(() => ({}));
    return Boolean(
      stored.clientId
      && stored.clientToken
      && stored.socketState === "connected"
      && stored.networkExternalReady === true
      && stored.chatgptLoginState === "ready"
      && stored.chatgptLoginComposerReady === true
      && (typeof socketReady !== "function" || socketReady())
    );
  }

  async function tabExists(tabId) {
    if (!Number.isInteger(tabId)) return null;
    try {
      const tab = await chrome.tabs.get(tabId);
      return isChatGpt(tab.url || tab.pendingUrl || "") ? tab : null;
    } catch (_) {
      return null;
    }
  }

  async function liveChatGptWindowIds() {
    try {
      const windows = await chrome.windows.getAll({ populate: true });
      const ids = new Set();
      for (const win of windows || []) {
        if (!Number.isInteger(win?.id)) continue;
        const hasChatGpt = (win.tabs || []).some(tab => isChatGpt(tab?.url || tab?.pendingUrl || ""));
        if (hasChatGpt) ids.add(win.id);
      }
      return ids;
    } catch (_) {
      return new Set();
    }
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
      last_rotation_reason: "reserve-pool-v29-claim",
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

  function routeWindowIds() {
    const router = globalThis[ROUTER_KEY];
    const ids = new Set();
    for (const route of Object.values(router?.routes || {})) {
      if (route?.window_owned !== false && Number.isInteger(route?.window_id)) ids.add(route.window_id);
    }
    return ids;
  }

  function warmWindowIds() {
    const pool = globalThis[WARM_POOL_KEY];
    const ids = new Set();
    for (const warm of pool?.warmSlots?.values?.() || []) {
      if (Number.isInteger(warm?.window_id)) ids.add(warm.window_id);
    }
    return ids;
  }

  async function persistOwn() {
    await chrome.storage.local.set({
      [OWN_STORAGE_KEY]: {
        version: 29,
        target: state.target,
        slots: [...state.reserveSlots.values()].map(item => ({ ...item })),
        updated_at_ms: Date.now(),
      },
    }).catch(() => {});
  }

  function reserveReadyAge(slot, now = Date.now()) {
    const readyAt = Number(slot?.ready_at_ms || 0);
    if (!Number.isFinite(readyAt) || readyAt <= 0) return null;
    return Math.max(0, Number(now || Date.now()) - readyAt);
  }

  function reserveSlotFresh(slot, now = Date.now()) {
    const age = reserveReadyAge(slot, now);
    if (slot?.ready === true || age !== null) {
      return age !== null && age <= MAX_RESERVE_READY_AGE_MS;
    }
    // An opening slot has no ready_at timestamp yet. Preserve it only for the
    // bounded preparation window; an abandoned opener must not survive forever.
    const createdAt = Number(slot?.created_at_ms || 0);
    return Number.isFinite(createdAt)
      && createdAt > 0
      && Math.max(0, Number(now || Date.now()) - createdAt) <= READY_TIMEOUT_MS + 5000;
  }

  async function pruneExpiredReserveSlots(now = Date.now()) {
    const routeIds = routeWindowIds();
    const warmIds = warmWindowIds();
    let expiredCount = 0;
    let closedWindows = 0;
    let detachedOwnedWindows = 0;
    let maxAgeMs = 0;
    for (const [slotKey, slot] of [...state.reserveSlots.entries()]) {
      if (reserveSlotFresh(slot, now)) continue;
      const age = reserveReadyAge(slot, now);
      if (age !== null) maxAgeMs = Math.max(maxAgeMs, age);
      state.reserveSlots.delete(slotKey);
      expiredCount += 1;
      if (routeIds.has(slot?.window_id) || warmIds.has(slot?.window_id)) {
        detachedOwnedWindows += 1;
        continue;
      }
      try {
        if (Number.isInteger(slot?.window_id)) await chrome.windows.remove(slot.window_id);
        closedWindows += 1;
      } catch (_) {}
    }
    if (expiredCount) await persistOwn();
    state.lastFreshnessPrune = {
      version: 39,
      checked_at_ms: Number(now || Date.now()),
      expired_count: expiredCount,
      max_ready_age_ms: maxAgeMs,
      closed_windows: closedWindows,
      detached_owned_windows: detachedOwnedWindows,
    };
    return state.lastFreshnessPrune;
  }

  state.maxReadyAgeMs = MAX_RESERVE_READY_AGE_MS;
  state.readyAge = reserveReadyAge;
  state.isFresh = reserveSlotFresh;
  state.pruneExpired = pruneExpiredReserveSlots;

  async function persistRouter() {
    const router = globalThis[ROUTER_KEY];
    if (!router?.routes || typeof router.routes !== "object") return;
    await chrome.storage.local.set({ [ROUTE_STORAGE_KEY]: router.routes }).catch(() => {});
  }

  async function loadOwn() {
    if (state.loaded) return;
    state.loaded = true;
    const stored = await chrome.storage.local.get({ [OWN_STORAGE_KEY]: null }).catch(() => ({}));
    const value = stored?.[OWN_STORAGE_KEY];
    if (value?.target) state.target = normalizeTarget(value.target);
    const rows = Array.isArray(value?.slots) ? value.slots : [];
    const routeIds = routeWindowIds();
    const warmIds = warmWindowIds();
    const seen = new Set();
    for (const raw of rows) {
      if (!Number.isInteger(raw?.tab_id) || !Number.isInteger(raw?.window_id)) continue;
      if (seen.has(raw.window_id) || routeIds.has(raw.window_id) || warmIds.has(raw.window_id)) continue;
      const tab = await tabExists(raw.tab_id);
      if (!tab || tab.windowId !== raw.window_id) continue;
      if (!reserveSlotFresh(raw)) {
        try { await chrome.windows.remove(raw.window_id); } catch (_) {}
        continue;
      }
      seen.add(raw.window_id);
      state.reserveSlots.set(String(raw.slot_id || `reserve:${raw.window_id}`), {
        ...raw,
        slot_id: String(raw.slot_id || `reserve:${raw.window_id}`),
        tab_id: tab.id,
        window_id: tab.windowId,
        recovered: true,
      });
    }
    await persistOwn();
  }

  async function normalizeOwnership() {
    await loadOwn();
    const routeIds = routeWindowIds();
    const warmIds = warmWindowIds();
    let changed = false;
    for (const [key, slot] of [...state.reserveSlots.entries()]) {
      if (routeIds.has(slot.window_id) || warmIds.has(slot.window_id)) {
        state.reserveSlots.delete(key);
        changed = true;
      }
    }
    if (changed) await persistOwn();
  }

  async function refreshRuntimeConfig(force = false) {
    const now = Date.now();
    if (!force && state.configRefreshedAt && now - state.configRefreshedAt < CONFIG_REFRESH_MS - 500) {
      return state.target;
    }
    const cfg = await settings();
    if (!cfg.clientId || !cfg.clientToken || !cfg.serverUrl) return state.target;
    try {
      const response = await fetch(`${String(cfg.serverUrl).replace(/\/$/, "")}/api/extensions/runtime-config`, {
        method: "GET",
        cache: "no-store",
        headers: {
          "X-Extension-Client-ID": cfg.clientId,
          "X-Extension-Token": cfg.clientToken,
        },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      state.target = normalizeTarget(payload?.reserve_window_target);
      state.configRefreshedAt = now;
      await persistOwn();
      return state.target;
    } catch (error) {
      await chrome.storage.local.set({
        chat2apiReserveConfigErrorV29: String(error?.message || error),
        chat2apiReserveConfigErrorAtV29: now,
      }).catch(() => {});
      return state.target;
    }
  }

  async function patchRouteIdleDeadlines() {
    const router = globalThis[ROUTER_KEY];
    if (!router?.routes || typeof router.routes !== "object") return false;
    const now = Date.now();
    let changed = false;
    for (const route of Object.values(router.routes)) {
      if (!Number.isInteger(route?.window_id) || route.window_owned === false) continue;
      if (route.inflight_request_id) continue;
      const lastActive = Number(route.last_active_at || 0);
      if (!lastActive) continue;
      const expected = lastActive + ROUTE_IDLE_CLOSE_MS;
      if (Math.abs(Number(route.close_after || 0) - expected) <= 1000) continue;
      route.close_after = expected;
      try {
        await chrome.alarms.create(`${ROUTE_ALARM_PREFIX}${route.window_id}`, { when: Math.max(now + 1000, expected) });
      } catch (_) {}
      changed = true;
    }
    if (changed) await persistRouter();
    return changed;
  }

  async function managedSnapshot() {
    await normalizeOwnership();
    await pruneExpiredReserveSlots();
    const live = await liveChatGptWindowIds();
    const managed = new Set();
    const active = new Set();
    const own = new Set();
    const warm = new Set();
    const routed = new Set();
    let oldestSpareReadyAgeMs = 0;

    for (const slot of state.reserveSlots.values()) {
      if (live.has(slot.window_id)) {
        managed.add(slot.window_id);
        own.add(slot.window_id);
        oldestSpareReadyAgeMs = Math.max(oldestSpareReadyAgeMs, reserveReadyAge(slot) || 0);
      }
    }

    const pool = globalThis[WARM_POOL_KEY];
    for (const slot of pool?.warmSlots?.values?.() || []) {
      if (Number.isInteger(slot?.window_id) && live.has(slot.window_id)) {
        managed.add(slot.window_id);
        warm.add(slot.window_id);
        const age = typeof pool?.readyAge === "function"
          ? pool.readyAge(slot)
          : Math.max(0, Date.now() - Number(slot?.ready_at_ms || Date.now()));
        oldestSpareReadyAgeMs = Math.max(oldestSpareReadyAgeMs, Number(age || 0));
      }
    }

    const router = globalThis[ROUTER_KEY];
    for (const route of Object.values(router?.routes || {})) {
      if (route?.window_owned === false || !Number.isInteger(route?.window_id) || !live.has(route.window_id)) continue;
      managed.add(route.window_id);
      routed.add(route.window_id);
      if (route.inflight_request_id) active.add(route.window_id);
    }
    if (router?.activeRequests instanceof Map) {
      for (const request of router.activeRequests.values()) {
        const windowId = Number(request?.window_id);
        if (Number.isInteger(windowId) && live.has(windowId)) {
          managed.add(windowId);
          routed.add(windowId);
          active.add(windowId);
        }
      }
    }

    const tracked = await chrome.storage.local.get({ chatgptExternalWarmWindowIdV28: null }).catch(() => ({}));
    const bootstrapId = tracked.chatgptExternalWarmWindowIdV28;
    if (Number.isInteger(bootstrapId) && live.has(bootstrapId)) managed.add(bootstrapId);

    return {
      total: managed.size,
      active: active.size,
      idle: Math.max(0, managed.size - active.size),
      own: own.size,
      warm: warm.size,
      routed: routed.size,
      target: state.target,
      oldest_spare_ready_age_ms: oldestSpareReadyAgeMs,
      live,
    };
  }

  async function reportStatus(force = false) {
    if (state.reportInFlight) return state.reportInFlight;
    state.reportInFlight = (async () => {
      const snapshot = await managedSnapshot();
      const signature = JSON.stringify([
        snapshot.total,
        snapshot.active,
        snapshot.own,
        snapshot.warm,
        snapshot.routed,
        snapshot.target,
        snapshot.live instanceof Set ? snapshot.live.size : snapshot.total,
      ]);
      if (!force && signature === state.lastReportSignature) return snapshot;
      state.lastReportSignature = signature;
      if (typeof trySendSocket === "function") {
        await trySendSocket({
          type: "extension.status",
          metadata: {
            reserve_window_total: snapshot.total,
            reserve_window_active: snapshot.active,
            reserve_window_idle: snapshot.idle,
            reserve_window_target: snapshot.target,
            reserve_window_all_chatgpt_windows: snapshot.live instanceof Set ? snapshot.live.size : snapshot.total,
            reserve_window_physical_telemetry_version: 83,
            reserve_window_own_spare: snapshot.own,
            reserve_window_warm_spare: snapshot.warm,
            reserve_window_routed: snapshot.routed,
            reserve_window_idle_close_seconds: ROUTE_IDLE_CLOSE_MS / 1000,
            reserve_window_telemetry_version: 29,
            reserve_window_freshness_version: 39,
            reserve_window_max_ready_age_seconds: MAX_RESERVE_READY_AGE_MS / 1000,
            reserve_window_oldest_ready_age_seconds: Math.round(snapshot.oldest_spare_ready_age_ms / 1000),
            reserve_window_updated_at: Date.now(),
          },
        }).catch(() => false);
      }
      return snapshot;
    })().finally(() => { state.reportInFlight = null; });
    return state.reportInFlight;
  }

  function scheduleReport(delayMs = 80, force = false) {
    clearTimeout(state.reportTimer);
    state.reportTimer = setTimeout(() => {
      state.reportTimer = null;
      reportStatus(force).catch(() => {});
    }, Math.max(0, delayMs));
  }

  async function waitReserveReady(tabId, timeoutMs = READY_TIMEOUT_MS) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const tab = await tabExists(tabId);
      if (!tab) throw new Error("Reserve ChatGPT tab disappeared while warming");
      if (!tab.status || tab.status === "complete") {
        try {
          const result = await chrome.scripting.executeScript({
            target: { tabId },
            func: () => {
              const visible = element => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
              };
              return [
                "#prompt-textarea",
                "textarea[placeholder]",
                "div[contenteditable='true'][data-lexical-editor='true']",
                "div[contenteditable='true'].ProseMirror",
              ].some(selector => [...document.querySelectorAll(selector)].some(visible));
            },
          });
          if (result?.[0]?.result === true) {
            if (typeof ensureContent === "function") await ensureContent(tabId).catch(() => {});
            return tab;
          }
        } catch (_) {}
      }
      await sleepReserve(220);
    }
    throw new Error("Timed out preparing reserve ChatGPT window");
  }

  async function createReserveWindow() {
    const createdAt = Date.now();
    const created = await chrome.windows.create({ url: "https://chatgpt.com/", focused: false, type: "normal" });
    if (!Number.isInteger(created?.id)) throw new Error("Chrome did not create reserve ChatGPT window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item?.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item?.id)) || null;
    }
    if (!tab?.id) {
      try { await chrome.windows.remove(created.id); } catch (_) {}
      throw new Error("Reserve ChatGPT window has no usable tab");
    }

    const slotId = `reserve:${created.id}`;
    const slot = {
      slot_id: slotId,
      tab_id: tab.id,
      window_id: created.id,
      created_at_ms: createdAt,
      ready_at_ms: 0,
      ready: false,
    };
    state.reserveSlots.set(slotId, slot);
    await persistOwn();
    scheduleReport(0);

    try {
      const readyTab = await waitReserveReady(tab.id);
      slot.tab_id = readyTab.id;
      slot.window_id = readyTab.windowId;
      slot.ready = true;
      slot.ready_at_ms = Date.now();
      await persistOwn();
      scheduleReport(0);
      return slot;
    } catch (error) {
      state.reserveSlots.delete(slotId);
      await persistOwn();
      try { await chrome.windows.remove(created.id); } catch (_) {}
      scheduleReport(0);
      throw error;
    }
  }

  async function trimOwnReserve(excess) {
    if (excess <= 0) return;
    const rows = [...state.reserveSlots.entries()]
      .sort((left, right) => Number(right[1]?.created_at_ms || 0) - Number(left[1]?.created_at_ms || 0));
    let remaining = excess;
    for (const [key, slot] of rows) {
      if (remaining <= 0) break;
      state.reserveSlots.delete(key);
      try { await chrome.windows.remove(slot.window_id); } catch (_) {}
      remaining -= 1;
    }
    await persistOwn();
  }

  async function reconcileNow() {
    if (state.reconcileInFlight) return state.reconcileInFlight;
    state.reconcileInFlight = (async () => {
      await loadOwn();
      const warmPool = globalThis[WARM_POOL_KEY];
      if (typeof warmPool?.pruneExpired === "function") await warmPool.pruneExpired().catch(() => null);
      await pruneExpiredReserveSlots();
      await refreshRuntimeConfig(false);
      await patchRouteIdleDeadlines();
      let snapshot = await managedSnapshot();

      // reserve_window_target is a spare target, not a total-window cap.
      // Routed conversation windows remain alive for affinity and must not
      // consume a reserve slot. target=3 + routed=1 therefore means total=4.
      let spareTotal = Math.max(0, snapshot.total - snapshot.routed);
      if (spareTotal > state.target && state.reserveSlots.size) {
        await trimOwnReserve(Math.min(spareTotal - state.target, state.reserveSlots.size));
        snapshot = await managedSnapshot();
        spareTotal = Math.max(0, snapshot.total - snapshot.routed);
      }

      if (spareTotal < state.target && await bulkPrewarmEligible()) {
        const warmOpening = Number(globalThis[WARM_POOL_KEY]?.openingSlots?.size || 0);
        const missing = Math.max(0, state.target - spareTotal - warmOpening);
        const batch = Math.min(CREATE_BATCH, missing);
        if (batch > 0) {
          await Promise.all(Array.from({ length: batch }, () => createReserveWindow().catch(() => null)));
          snapshot = await managedSnapshot();
          spareTotal = Math.max(0, snapshot.total - snapshot.routed);
          if (spareTotal < state.target) scheduleReconcile(250);
        }
      }

      await reportStatus(false);
      return snapshot;
    })().finally(() => { state.reconcileInFlight = null; });
    return state.reconcileInFlight;
  }

  function scheduleReconcile(delayMs = RECONCILE_DELAY_MS) {
    clearTimeout(state.reconcileTimer);
    state.reconcileTimer = setTimeout(() => {
      state.reconcileTimer = null;
      reconcileNow().catch(() => {});
    }, Math.max(0, delayMs));
  }

  async function claimOwnReserve(key) {
    if (!key) return null;
    await loadOwn();
    const warmPool = globalThis[WARM_POOL_KEY];
    if (typeof warmPool?.pruneExpired === "function") await warmPool.pruneExpired().catch(() => null);
    await pruneExpiredReserveSlots();
    const router = globalThis[ROUTER_KEY];
    if (!router?.routes || typeof router.routes !== "object") return null;
    const existing = router.routes[key];
    if (existing && await tabExists(existing.tab_id)) return null;

    if (Number(warmPool?.warmSlots?.size || 0) > 0) return null;

    let selectedKey = null;
    let selected = null;
    for (const [slotKey, slot] of state.reserveSlots.entries()) {
      if (slot?.ready !== true) continue;
      if (!reserveSlotFresh(slot)) continue;
      const tab = await tabExists(slot.tab_id);
      if (!tab) continue;
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
    route.prewarm_ready_age_ms = reserveReadyAge(selected) || 0;
    route.prewarm_source = "reserve-pool-v29-freshness-v39";
    route.last_rotation_reason = "reserve-pool-v29-claim";
    router.routes[key] = route;

    state.reserveSlots.delete(selectedKey);
    await Promise.all([persistOwn(), persistRouter()]);
    scheduleReport(0);
    return {
      tab: selected.tab,
      ready_age_ms: route.prewarm_ready_age_ms,
      created_age_ms: route.prewarm_load_ms,
    };
  }

  globalThis.resolveTargetTabForRequest = async function resolveWithReservePool(message) {
    const supplied = Number(message?.routing?.worker_limit);
    if (Number.isFinite(supplied) && supplied > 0) {
      const nextTarget = normalizeTarget(supplied);
      if (nextTarget !== state.target) {
        state.target = nextTarget;
        persistOwn().catch(() => {});
        scheduleReconcile(0);
      }
    }
    const key = String(message?.routing?.api_key_id || "").trim();
    const claimed = key ? await claimOwnReserve(key).catch(() => null) : null;
    const tab = await baseResolver(message);
    if (claimed && message?.request_id && typeof trySendSocket === "function") {
      const eventType = message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics";
      await trySendSocket({
        type: eventType,
        kind: message.type === "voice.request" || message.type === "voice.live.start" ? "voice" : undefined,
        request_id: message.request_id,
        diagnostics: {
          conversation_reserve_prewarm_hit: true,
          conversation_reserve_prewarm_ready_age_ms: Number(claimed.ready_age_ms || 0),
          conversation_reserve_prewarm_created_age_ms: Number(claimed.created_age_ms || 0),
          conversation_prewarm_max_ready_age_ms: MAX_RESERVE_READY_AGE_MS,
          conversation_prewarm_freshness_gate: "spare-max-ready-age-v39",
          routed_tab_id: tab?.id ?? null,
          routed_window_id: tab?.windowId ?? null,
        },
      }).catch(() => false);
    }
    scheduleReport(0);
    scheduleReconcile(120);
    return tab;
  };

  state.snapshot = managedSnapshot;
  state.reconcile = reconcileNow;
  state.report = reportStatus;
  state.refreshConfig = refreshRuntimeConfig;
  state.patchIdleDeadlines = patchRouteIdleDeadlines;

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["chat.started", "chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(event.type)) return false;
    scheduleReport(event.type === "chat.started" ? 80 : 260);
    scheduleReconcile(event.type === "chat.started" ? 120 : 320);
    return false;
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.socketState?.newValue === "connected") {
      refreshRuntimeConfig(true).finally(() => scheduleReconcile(0));
      return;
    }
    if (
      changes.networkExternalReady
      || changes.chatgptLoginState
      || changes.chatgptLoginComposerReady
      || changes[WARM_STORAGE_KEY]
      || changes[ROUTE_STORAGE_KEY]
      || changes.chatgptExternalWarmWindowIdV28
    ) {
      if (changes[ROUTE_STORAGE_KEY]) patchRouteIdleDeadlines().catch(() => {});
      scheduleReport(80);
      scheduleReconcile(160);
    }
  });

  chrome.windows.onRemoved.addListener(() => {
    scheduleReport(220);
    scheduleReconcile(300);
  });
  chrome.tabs.onRemoved.addListener(() => {
    scheduleReport(220);
    scheduleReconcile(300);
  });

  state.configTimer = setInterval(() => {
    refreshRuntimeConfig(false).finally(() => scheduleReconcile(0));
  }, CONFIG_REFRESH_MS);

  setTimeout(async () => {
    await loadOwn();
    await refreshRuntimeConfig(true);
    await patchRouteIdleDeadlines();
    await reportStatus(true);
    scheduleReconcile(0);
  }, 200);
})();
