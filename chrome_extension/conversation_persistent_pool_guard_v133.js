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

  // v137: this guard is admission ordering only. Physical window creation,
  // closure and target reconciliation belong exclusively to persistent-pool-v132.
  // Login readiness is evidence consumed by that authority, never a second
  // lifecycle owner. Historical logout-compaction counters remain in state only
  // for backward-compatible diagnostics.
  async function compactExplicitLogout(reason = "login-required") {
    const login = await loginSnapshot();
    state.last = {
      ok: true,
      skipped: true,
      reason: "delegated-to-persistent-window-pool-v137",
      trigger: reason,
      login_state: String(login?.state || "unknown"),
      at: Date.now(),
    };
    const pool = globalThis[POOL_KEY];
    if (pool && typeof pool.reconcile === "function") {
      await pool.reconcile(`login-state:${String(login?.state || "unknown")}`);
    }
    return state.last;
  }

  function scheduleCompaction(reason = "login-state-change", delay = 200) {
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
    if (changes.chatgptLoginState || changes.chatgptLoginComposerReady) scheduleCompaction("storage-login-state-change", 120);
  });

  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm?.name === ALARM_NAME) compactExplicitLogout("periodic-login-required-check").catch(() => {});
  });
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: 1 }).catch?.(() => {});

  state.compactExplicitLogout = compactExplicitLogout;
  state.scheduleCompaction = scheduleCompaction;
  setTimeout(() => scheduleCompaction("startup-login-state-check", 0), 900);
})();
