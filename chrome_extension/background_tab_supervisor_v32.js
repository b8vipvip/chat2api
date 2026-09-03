(() => {
  const KEY = "__CHAT2API_TAB_SUPERVISOR_V32__";
  if (globalThis[KEY]) return;

  const INIT_TAB_KEY = "chat2apiInitializationTabIdV32";
  const INIT_WINDOW_KEY = "chat2apiInitializationWindowIdV32";
  const INIT_URL = "https://chatgpt.com/";
  const RESERVE_STORAGE_KEY = "chat2apiReservePoolV29";
  const STARTUP_GRACE_MS = 2500;
  const NEW_TAB_GRACE_MS = 20000;
  const RECONCILE_DELAY_MS = 180;
  const ALARM_NAME = "chat2api-tab-supervisor-v32";
  const FALLBACK_TARGET = 3;

  const state = {
    startedAt: Date.now(),
    startupChatTabs: new Set(),
    createdAt: new Map(),
    reconcileTimer: null,
    reconcileInFlight: null,
    baseChatTabs: typeof globalThis.chatTabs === "function" ? globalThis.chatTabs : null,
    lastSnapshot: null,
  };
  globalThis[KEY] = state;
  const createManagedWindow = (options, reason) => typeof globalThis.chat2apiCreateWindowStaggered === "function"
    ? globalThis.chat2apiCreateWindowStaggered(options, { reason })
    : chrome.windows.create(options);

  function isChatTab(tab) {
    if (!Number.isInteger(tab?.id)) return false;
    const url = String(tab.url || tab.pendingUrl || "");
    return typeof isChatGptUrl === "function" && isChatGptUrl(url);
  }

  async function allChatTabs() {
    const tabs = await chrome.tabs.query({}).catch(() => []);
    return (tabs || []).filter(isChatTab);
  }

  async function liveTab(tabId) {
    if (!Number.isInteger(tabId)) return null;
    try {
      const tab = await chrome.tabs.get(tabId);
      return isChatTab(tab) ? tab : null;
    } catch (_) {
      return null;
    }
  }

  function addOwned(map, tabId, kind, { active = false, lastActive = 0, priority = 50 } = {}) {
    if (!Number.isInteger(tabId)) return;
    const current = map.get(tabId);
    if (!current || priority < current.priority || active) {
      map.set(tabId, {
        tab_id: tabId,
        kind,
        active: Boolean(active || current?.active),
        last_active_at: Math.max(Number(lastActive || 0), Number(current?.last_active_at || 0)),
        priority: Math.min(priority, Number(current?.priority ?? priority)),
      });
    }
  }

  async function targetLimit() {
    const reserve = globalThis.__CHAT2API_RESERVE_POOL_V29__;
    const liveTarget = Number(reserve?.target || 0);
    if (Number.isFinite(liveTarget) && liveTarget > 0) return Math.max(1, Math.min(32, Math.floor(liveTarget)));
    const stored = await chrome.storage.local.get({ [RESERVE_STORAGE_KEY]: null }).catch(() => ({}));
    const persisted = Number(stored?.[RESERVE_STORAGE_KEY]?.target || 0);
    if (Number.isFinite(persisted) && persisted > 0) return Math.max(1, Math.min(32, Math.floor(persisted)));
    const workers = globalThis.__CHAT2API_CONVERSATION_WORKERS_V24__;
    const workerLimit = Number(workers?.maxWorkers || 0);
    if (Number.isFinite(workerLimit) && workerLimit > 0) return Math.max(1, Math.min(32, Math.floor(workerLimit)));
    return FALLBACK_TARGET;
  }

  async function ownership() {
    const stored = await chrome.storage.local.get({
      [INIT_TAB_KEY]: null,
      [INIT_WINDOW_KEY]: null,
      chatgptLoginProbeTabId: null,
      chatgptLoginProbeWindowId: null,
      boundTabId: null,
      chatgptExternalWarmTabIdV28: null,
    }).catch(() => ({}));

    const protectedTabs = new Set();
    const workerTabs = new Map();
    const loginTabId = stored.chatgptLoginProbeTabId;
    const initTabId = stored[INIT_TAB_KEY];
    if (Number.isInteger(initTabId)) protectedTabs.add(initTabId);
    if (Number.isInteger(loginTabId)) protectedTabs.add(loginTabId);

    // Agent remote login controls the real Chrome/Xvfb surface and does not
    // necessarily create the Extension's popup login probe. The currently
    // visible ChatGPT tab is therefore protected independently of ownership so
    // a long CAPTCHA/login flow can never be reclaimed as an orphan.
    try {
      const active = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      for (const tab of active || []) {
        if (isChatTab(tab)) protectedTabs.add(tab.id);
      }
    } catch (_) {}

    if (Number.isInteger(stored.boundTabId) && stored.boundTabId !== initTabId) {
      addOwned(workerTabs, stored.boundTabId, "bound", { priority: 40 });
    }
    if (Number.isInteger(stored.chatgptExternalWarmTabIdV28) && stored.chatgptExternalWarmTabIdV28 !== initTabId) {
      addOwned(workerTabs, stored.chatgptExternalWarmTabIdV28, "external-warm", { priority: 15 });
    }

    const router = globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
    for (const route of Object.values(router?.routes || {})) {
      const tabId = route?.tab_id;
      if (!Number.isInteger(tabId) || tabId === initTabId) continue;
      const active = Boolean(route?.inflight_request_id) || Boolean(router?.activeRequests instanceof Map && [...router.activeRequests.values()].some(item => item?.tab_id === tabId));
      addOwned(workerTabs, tabId, "route", {
        active,
        lastActive: Number(route?.last_active_at || 0),
        priority: 30,
      });
    }

    const warm = globalThis.__CHAT2API_CONVERSATION_WARM_POOL_V2__;
    for (const slot of warm?.warmSlots?.values?.() || []) {
      if (slot?.tab_id !== initTabId) addOwned(workerTabs, slot?.tab_id, "warm", { priority: 10, lastActive: Number(slot?.ready_at_ms || slot?.created_at_ms || 0) });
    }

    const reserve = globalThis.__CHAT2API_RESERVE_POOL_V29__;
    for (const slot of reserve?.reserveSlots?.values?.() || []) {
      if (slot?.tab_id !== initTabId) addOwned(workerTabs, slot?.tab_id, "reserve", { priority: 5, lastActive: Number(slot?.ready_at_ms || slot?.created_at_ms || 0) });
    }

    for (const request of globalThis.__CHAT2API_CONVERSATION_WORKERS_V24__?.requestRoutes?.values?.() || []) {
      if (Number.isInteger(request?.tabId)) {
        addOwned(workerTabs, request.tabId, "request", { active: true, priority: 100, lastActive: Date.now() });
      }
    }

    for (const [tabId, row] of workerTabs.entries()) {
      if (row.active) protectedTabs.add(tabId);
    }
    return { stored, protectedTabs, workerTabs, initTabId, loginTabId };
  }

  async function storeInitialization(tab) {
    if (!isChatTab(tab)) return null;
    await chrome.storage.local.set({
      [INIT_TAB_KEY]: tab.id,
      [INIT_WINDOW_KEY]: tab.windowId,
      chat2apiInitializationVersionV32: 32,
      chat2apiInitializationUpdatedAtV32: Date.now(),
    }).catch(() => {});
    return tab;
  }

  async function ensureInitializationTab() {
    const stored = await chrome.storage.local.get({ [INIT_TAB_KEY]: null }).catch(() => ({}));
    const existing = await liveTab(stored[INIT_TAB_KEY]);
    if (existing) return existing;

    const owned = await ownership();
    const tabs = await allChatTabs();
    const adopt = tabs.find(tab => !owned.workerTabs.has(tab.id) && tab.id !== owned.loginTabId);
    if (adopt) return storeInitialization(adopt);

    const created = await createManagedWindow({ url: INIT_URL, focused: false, type: "normal" }, "initialization");
    if (!Number.isInteger(created?.id)) throw new Error("Chrome did not create the Worker initialization window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item?.id)) : null;
    if (!tab) {
      const rows = await chrome.tabs.query({ windowId: created.id }).catch(() => []);
      tab = rows.find(item => Number.isInteger(item?.id)) || null;
    }
    if (!tab?.id) throw new Error("Worker initialization window has no ChatGPT tab");
    state.createdAt.set(tab.id, Date.now());
    return storeInitialization(tab);
  }

  async function patchChatTabs() {
    if (!state.baseChatTabs || globalThis.chatTabs?.__chat2apiSupervisorV32) return;
    const wrapped = async (...args) => {
      const rows = await state.baseChatTabs(...args);
      const stored = await chrome.storage.local.get({ [INIT_TAB_KEY]: null }).catch(() => ({}));
      const initTabId = stored[INIT_TAB_KEY];
      return (rows || []).filter(tab => tab?.id !== initTabId);
    };
    wrapped.__chat2apiSupervisorV32 = true;
    globalThis.chatTabs = wrapped;
  }

  async function removeTabs(tabIds) {
    const unique = [...new Set(tabIds.filter(Number.isInteger))];
    if (!unique.length) return 0;
    for (const tabId of unique) {
      try { await chrome.tabs.remove(tabId); } catch (_) {}
      state.createdAt.delete(tabId);
    }
    return unique.length;
  }

  async function reconcileNow() {
    if (state.reconcileInFlight) return state.reconcileInFlight;
    state.reconcileInFlight = (async () => {
      const init = await ensureInitializationTab();
      await patchChatTabs();
      const current = await ownership();
      const tabs = await allChatTabs();
      const now = Date.now();
      const liveIds = new Set(tabs.map(tab => tab.id));
      const orphanIds = [];

      for (const tab of tabs) {
        if (current.protectedTabs.has(tab.id) || current.workerTabs.has(tab.id)) continue;
        const createdAt = Number(state.createdAt.get(tab.id) || 0);
        const startupRestored = state.startupChatTabs.has(tab.id);
        if (!startupRestored && createdAt && now - createdAt < NEW_TAB_GRACE_MS) continue;
        orphanIds.push(tab.id);
      }

      let closedOrphans = 0;
      if (now - state.startedAt >= STARTUP_GRACE_MS) closedOrphans = await removeTabs(orphanIds);

      const target = await targetLimit();
      const surviving = [...current.workerTabs.values()].filter(row => liveIds.has(row.tab_id));
      // reserve_window_target is a SPARE target. Routed same-key conversation
      // windows are intentionally retained by their own idle alarm and must never
      // be reclaimed merely because spare + routed exceeds the spare target.
      const spareKinds = new Set(["reserve", "warm", "external-warm"]);
      const spareSurviving = surviving.filter(row => spareKinds.has(row.kind));
      const routedSurviving = surviving.filter(row => row.kind === "route" || row.kind === "request");
      const excess = Math.max(0, spareSurviving.length - target);
      let closedOverflow = 0;
      if (excess > 0) {
        const candidates = spareSurviving
          .filter(row => !row.active && row.tab_id !== current.loginTabId && row.tab_id !== init.id)
          .sort((a, b) => a.priority - b.priority || Number(a.last_active_at || 0) - Number(b.last_active_at || 0));
        closedOverflow = await removeTabs(candidates.slice(0, excess).map(row => row.tab_id));
      }

      const snapshot = {
        version: 32,
        target,
        initialization_tab_id: init.id,
        login_tab_id: Number.isInteger(current.loginTabId) ? current.loginTabId : null,
        chatgpt_tabs_seen: tabs.length,
        managed_worker_tabs: surviving.length,
        spare_worker_tabs: spareSurviving.length,
        routed_worker_tabs: routedSurviving.length,
        worker_target_semantics: "spares-only-v85",
        active_worker_tabs: surviving.filter(row => row.active).length,
        protected_interactive_tabs: [...current.protectedTabs].filter(tabId => tabId !== init.id && tabId !== current.loginTabId && !current.workerTabs.has(tabId)).length,
        orphan_tabs_closed: closedOrphans,
        overflow_tabs_closed: closedOverflow,
        checked_at_ms: Date.now(),
      };
      state.lastSnapshot = snapshot;
      await chrome.storage.local.set({ chat2apiTabSupervisorV32: snapshot }).catch(() => {});
      if ((closedOrphans || closedOverflow) && typeof sendExtensionStatus === "function" && typeof socketReady === "function" && socketReady()) {
        setTimeout(() => sendExtensionStatus(false).catch(() => {}), 350);
      }
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

  state.reconcile = reconcileNow;
  state.ensureInitializationTab = ensureInitializationTab;
  state.snapshot = () => state.lastSnapshot;

  chrome.tabs.onCreated.addListener(tab => {
    if (Number.isInteger(tab?.id)) state.createdAt.set(tab.id, Date.now());
    scheduleReconcile(NEW_TAB_GRACE_MS + 250);
  });
  chrome.tabs.onUpdated.addListener((_tabId, changeInfo, tab) => {
    if (changeInfo.url || changeInfo.status === "complete") scheduleReconcile(500);
  });
  chrome.tabs.onRemoved.addListener(tabId => {
    state.createdAt.delete(tabId);
    chrome.storage.local.get({ [INIT_TAB_KEY]: null }).then(stored => {
      if (stored[INIT_TAB_KEY] === tabId) chrome.storage.local.set({ [INIT_TAB_KEY]: null, [INIT_WINDOW_KEY]: null }).catch(() => {});
    }).catch(() => {});
    scheduleReconcile(450);
  });
  chrome.windows.onRemoved.addListener(() => scheduleReconcile(450));
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes[RESERVE_STORAGE_KEY] || changes.boundTabId || changes.chatgptLoginProbeTabId || changes.chatgptExternalWarmTabIdV28) scheduleReconcile(250);
  });
  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm?.name === ALARM_NAME) reconcileNow().catch(() => {});
  });

  chrome.tabs.query({}).then(tabs => {
    for (const tab of tabs || []) if (isChatTab(tab)) state.startupChatTabs.add(tab.id);
    setTimeout(() => reconcileNow().catch(() => {}), STARTUP_GRACE_MS);
  }).catch(() => setTimeout(() => reconcileNow().catch(() => {}), STARTUP_GRACE_MS));
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: 1 }).catch(() => {});
})();
