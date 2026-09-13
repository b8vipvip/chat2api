(() => {
  const KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_GUARD_V133__";
  if (globalThis[KEY]) return;

  const POOL_KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__";
  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const LOGIN_KEY = "__CHAT2API_LOGIN_READINESS_V27__";
  const ROUTES_STORAGE_KEY = "chat2apiConversationRoutesV1";
  const INIT_TAB_KEY = "chat2apiInitializationTabIdV32";
  const ALARM_NAME = "chat2api-persistent-window-pool-guard-v133";
  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";

  const state = {
    version: 133,
    revision: 133,
    admission_barriers: 0,
    logout_compactions: 0,
    windows_closed: 0,
    routes_detached: 0,
    compactTimer: null,
    compactPromise: null,
    last: null,
  };
  globalThis[KEY] = state;

  function isManagedSurface(value = "") {
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase();
      if (["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(host)) return true;
      return host.endsWith(".openai.com") && /(auth|login|signin|signup)/i.test(`${host}${url.pathname}`);
    } catch (_) {
      return false;
    }
  }

  async function managedWindows() {
    const result = [];
    const windows = await chrome.windows.getAll({ populate: true }).catch(() => []);
    for (const win of windows || []) {
      if (!Number.isInteger(win?.id)) continue;
      const tab = (Array.isArray(win.tabs) ? win.tabs : []).find(item => {
        const url = item?.url || item?.pendingUrl || "";
        return Number.isInteger(item?.id) && isManagedSurface(url);
      });
      if (!tab?.id) continue;
      result.push({ window_id: win.id, tab_id: tab.id, focused: win.focused === true });
    }
    return result;
  }

  function busyWindowIds(router, pool) {
    const ids = new Set();
    for (const windowId of pool?.reservations?.values?.() || []) {
      const parsed = Number(windowId);
      if (Number.isInteger(parsed)) ids.add(parsed);
    }
    for (const route of Object.values(router?.routes || {})) {
      const windowId = Number(route?.window_id);
      if (route?.inflight_request_id && Number.isInteger(windowId)) ids.add(windowId);
    }
    if (router?.activeRequests instanceof Map) {
      for (const request of router.activeRequests.values()) {
        const windowId = Number(request?.window_id);
        if (Number.isInteger(windowId)) ids.add(windowId);
      }
    }
    return ids;
  }

  async function loginSnapshot() {
    const readiness = globalThis[LOGIN_KEY];
    if (typeof readiness?.snapshot === "function") {
      try {
        const value = await readiness.snapshot();
        if (value && typeof value === "object") return value;
      } catch (_) {}
    }
    const stored = await chrome.storage.local.get({
      chatgptLoginState: "unknown",
      chatgptLoginComposerReady: false,
    }).catch(() => ({}));
    return {
      state: String(stored.chatgptLoginState || "unknown"),
      composer_ready: stored.chatgptLoginComposerReady === true,
      window_id: null,
      tab_id: null,
    };
  }

  async function preferredWindowId(rows, login) {
    const live = new Set(rows.map(row => row.window_id));
    if (Number.isInteger(login?.window_id) && live.has(login.window_id)) return login.window_id;

    const stored = await chrome.storage.local.get({ [INIT_TAB_KEY]: null }).catch(() => ({}));
    if (Number.isInteger(stored?.[INIT_TAB_KEY])) {
      try {
        const tab = await chrome.tabs.get(stored[INIT_TAB_KEY]);
        if (Number.isInteger(tab?.windowId) && live.has(tab.windowId)) return tab.windowId;
      } catch (_) {}
    }

    const focused = rows.find(row => row.focused);
    return focused?.window_id ?? rows[0]?.window_id ?? null;
  }

  async function detachIdleRoutes(closeIds, router) {
    if (!router?.routes || !closeIds.size) return 0;
    let changed = 0;
    for (const route of Object.values(router.routes)) {
      const windowId = Number(route?.window_id);
      if (!closeIds.has(windowId) || route?.inflight_request_id) continue;
      try { await chrome.alarms.clear(`${ROUTE_ALARM_PREFIX}${windowId}`); } catch (_) {}
      route.window_id = null;
      route.tab_id = null;
      route.window_owned = false;
      route.close_after = null;
      route.persistent_pool_revision = 133;
      route.last_pool_detach_reason = "explicit-login-required-v133";
      route.last_pool_detach_at = Date.now();
      changed += 1;
    }
    if (changed) {
      await chrome.storage.local.set({ [ROUTES_STORAGE_KEY]: router.routes }).catch(() => {});
      state.routes_detached += changed;
    }
    return changed;
  }

  async function compactExplicitLogout(reason = "login-required") {
    if (state.compactPromise) return state.compactPromise;
    const task = (async () => {
      const login = await loginSnapshot();
      if (String(login?.state || "") !== "login_required") {
        state.last = { ok: true, skipped: true, reason: "login-state-transitional", login_state: String(login?.state || "unknown"), at: Date.now() };
        return state.last;
      }

      const pool = globalThis[POOL_KEY];
      const router = globalThis[ROUTER_KEY];
      const rows = await managedWindows();
      if (rows.length <= 1) {
        state.last = { ok: true, skipped: true, reason: "already-compact", login_state: "login_required", total: rows.length, at: Date.now() };
        return state.last;
      }

      const busy = busyWindowIds(router, pool);
      const preferred = await preferredWindowId(rows, login);
      const keep = new Set(busy);
      if (Number.isInteger(preferred)) keep.add(preferred);
      if (!keep.size && rows[0]) keep.add(rows[0].window_id);

      // During an explicit logout, one interactive/login surface is enough. Busy
      // request windows remain protected until their terminal path releases them.
      const desired = Math.max(1, busy.size, keep.size);
      const removable = rows.filter(row => !keep.has(row.window_id));
      const closeRows = removable.slice(0, Math.max(0, rows.length - desired));
      const closeIds = new Set(closeRows.map(row => row.window_id));
      const detached = await detachIdleRoutes(closeIds, router);

      let closed = 0;
      for (const row of closeRows) {
        try {
          await chrome.windows.remove(row.window_id);
          closed += 1;
        } catch (_) {}
      }

      state.logout_compactions += 1;
      state.windows_closed += closed;
      state.last = {
        ok: true,
        reason,
        login_state: "login_required",
        before: rows.length,
        desired,
        protected_busy: busy.size,
        detached,
        closed,
        after_expected: Math.max(0, rows.length - closed),
        at: Date.now(),
      };
      if (typeof sendExtensionStatus === "function") {
        setTimeout(() => sendExtensionStatus(false).catch(() => {}), 250);
      }
      return state.last;
    })().finally(() => {
      if (state.compactPromise === task) state.compactPromise = null;
    });
    state.compactPromise = task;
    return task;
  }

  function scheduleCompaction(reason = "login-required", delay = 200) {
    if (state.compactTimer) clearTimeout(state.compactTimer);
    state.compactTimer = setTimeout(() => {
      state.compactTimer = null;
      compactExplicitLogout(reason).catch(() => {});
    }, Math.max(0, Number(delay || 0)));
  }

  // v132 can repair/warm a missing slot while request admission is happening.
  // Serialise those phases by completing the current reconciliation before the
  // router leases a slot. This prevents a transient N+1 physical window when a
  // request arrives during pool repair.
  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver === "function" && !baseResolver.__chat2apiPersistentPoolGuardV133) {
    const wrappedResolver = async message => {
      const pool = globalThis[POOL_KEY];
      if (pool && typeof pool.reconcile === "function") {
        state.admission_barriers += 1;
        await pool.reconcile("request-admission-barrier-v133");
      }
      return baseResolver(message);
    };
    wrappedResolver.__chat2apiPersistentPoolGuardV133 = true;
    globalThis.resolveTargetTabForRequest = wrappedResolver;
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.chatgptLoginState?.newValue === "login_required") scheduleCompaction("storage-login-required", 120);
  });

  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm?.name === ALARM_NAME) compactExplicitLogout("periodic-login-required-check").catch(() => {});
  });
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: 1 }).catch?.(() => {});

  state.compactExplicitLogout = compactExplicitLogout;
  state.scheduleCompaction = scheduleCompaction;
  setTimeout(() => scheduleCompaction("startup-login-state-check", 0), 900);
})();
