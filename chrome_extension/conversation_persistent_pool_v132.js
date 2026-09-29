(() => {
  const KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const LOGIN_KEY = "__CHAT2API_LOGIN_READINESS_V27__";
  const LOGIN_STATE_KEY = "chatgptLoginState";
  const LOGIN_COMPOSER_KEY = "chatgptLoginComposerReady";
  const STORAGE_KEY = "chat2apiPersistentWindowPoolV132";
  const LEGACY_LIMIT_STORAGE_KEY = "chat2apiRoutedWindowLimitV121";
  const ROUTES_STORAGE_KEY = "chat2apiConversationRoutesV1";
  const DISABLED_KEY = "chat2apiWorkerMasterDisabledV61";
  const INIT_TAB_KEY = "chat2apiInitializationTabIdV32";
  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";
  const REPAIR_ALARM = "chat2api-persistent-window-pool-v132";
  const NEW_CHAT_URL = "https://chatgpt.com/";
  const MIN_TARGET = 1;
  const MAX_TARGET = 32;
  const STANDBY_SEMANTICS_REVISION = 152;
  const TERMINAL_TYPES = new Set([
    "chat.completed", "chat.error", "chat.cancelled",
    "image.completed", "image.error", "image.cancelled",
    "voice.error", "voice.cancelled",
  ]);

  const state = {
    version: 132,
    revision: 132,
    policy: "persistent-prewarmed-total-window-pool-v132",
    target: null,
    source: "unset",
    loaded: false,
    gate: Promise.resolve(),
    reservations: new Map(),
    runtimeReadyTabs: new Map(),
    runtimeChecks: 0,
    runtimeFailures: 0,
    reconcilePromise: null,
    reconcileTimer: null,
    lastResult: null,
    created: 0,
    reused: 0,
    closed: 0,
  };
  globalThis[KEY] = state;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function normalizeTarget(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < MIN_TARGET || parsed > MAX_TARGET) return null;
    return Math.floor(parsed);
  }

  function isChatGpt(value = "") {
    try {
      return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname.toLowerCase());
    } catch (_) { return false; }
  }

  function isAuthSurface(value = "") {
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase();
      const path = url.pathname.toLowerCase();
      if (isChatGpt(value) && /(^|\/)(auth|login|signin|sign-in|signup|sign-up)(\/|$)/.test(path)) return true;
      return host.endsWith(".openai.com") && /(auth|login|signin|signup)/.test(`${host}${path}`);
    } catch (_) { return false; }
  }

  function isManagedSurface(value = "") {
    return isChatGpt(value) || isAuthSurface(value);
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
      persistent_pool_revision: 132,
    };
  }

  async function ensureLoaded() {
    if (state.loaded) return;
    const stored = await chrome.storage.local.get({
      [STORAGE_KEY]: null,
      [LEGACY_LIMIT_STORAGE_KEY]: null,
    }).catch(() => ({}));
    const own = stored?.[STORAGE_KEY];
    const legacy = stored?.[LEGACY_LIMIT_STORAGE_KEY];
    const target = normalizeTarget(own?.target) || normalizeTarget(legacy?.limit);
    state.target = target;
    state.source = String(own?.source || legacy?.source || (target ? "restored" : "unset")).slice(0, 40);
    state.loaded = true;
  }

  async function persistTarget() {
    if (!state.target) {
      await chrome.storage.local.remove(STORAGE_KEY).catch(() => {});
      return;
    }
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        version: 132,
        target: state.target,
        source: state.source,
        policy: state.policy,
        semantics: "unassigned-standby-target-v152",
        updated_at: new Date().toISOString(),
      },
    }).catch(() => {});
  }

  function router() {
    const value = globalThis[ROUTER_KEY];
    return value && value.routes && typeof value.routes === "object" ? value : null;
  }

  async function routerReady() {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const value = router();
      if (value?.loaded === true) return value;
      await sleep(15);
    }
    return router();
  }

  async function persistRoutes(value) {
    if (!value?.routes) return;
    await chrome.storage.local.set({ [ROUTES_STORAGE_KEY]: value.routes }).catch(() => {});
  }

  async function disabled() {
    const stored = await chrome.storage.local.get({ [DISABLED_KEY]: false }).catch(() => ({}));
    return stored?.[DISABLED_KEY] === true;
  }

  async function loginReady() {
    const readiness = globalThis[LOGIN_KEY];
    let snapshot = null;
    if (typeof readiness?.snapshot === "function") {
      try { snapshot = await readiness.snapshot(); } catch (_) {}
    }
    if (snapshot?.state) return snapshot.state === "ready" && snapshot.composer_ready === true;
    const stored = await chrome.storage.local.get({
      [LOGIN_STATE_KEY]: "unknown",
      [LOGIN_COMPOSER_KEY]: false,
    }).catch(() => ({}));
    return stored[LOGIN_STATE_KEY] === "ready" && stored[LOGIN_COMPOSER_KEY] === true;
  }

  async function loginSnapshot() {
    const readiness = globalThis[LOGIN_KEY];
    if (typeof readiness?.snapshot === "function") {
      try { return await readiness.snapshot(); } catch (_) {}
    }
    return null;
  }

  async function physicalWindows() {
    const rows = [];
    const windows = await chrome.windows.getAll({ populate: true }).catch(() => []);
    for (const win of windows || []) {
      if (!Number.isInteger(win?.id)) continue;
      const tabs = Array.isArray(win.tabs) ? win.tabs : [];
      let tab = tabs.find(item => Number.isInteger(item?.id) && isChatGpt(item.url || item.pendingUrl || ""));
      if (!tab) tab = tabs.find(item => Number.isInteger(item?.id) && isManagedSurface(item.url || item.pendingUrl || ""));
      if (!tab?.id) continue;
      const url = tab.url || tab.pendingUrl || "";
      rows.push({
        window_id: win.id,
        tab_id: tab.id,
        url,
        status: String(tab.status || ""),
        routable: isChatGpt(url) && !isAuthSurface(url),
        runtime_ready: state.runtimeReadyTabs.get(tab.id) === true,
        focused: win.focused === true,
      });
    }
    return rows;
  }

  function routeEntries(value) {
    return Object.entries(value?.routes || {}).filter(([, route]) => route && typeof route === "object");
  }

  function routeByWindow(value) {
    const result = new Map();
    for (const [key, route] of routeEntries(value)) {
      const windowId = Number(route?.window_id);
      if (!Number.isInteger(windowId)) continue;
      const existing = result.get(windowId);
      if (!existing || Number(route.last_active_at || 0) >= Number(existing.route?.last_active_at || 0)) {
        result.set(windowId, { key, route });
      }
    }
    return result;
  }

  function busyWindowIds(value) {
    const ids = new Set([...state.reservations.values()].filter(Number.isInteger));
    for (const route of Object.values(value?.routes || {})) {
      const windowId = Number(route?.window_id);
      if (route?.inflight_request_id && Number.isInteger(windowId)) ids.add(windowId);
    }
    if (value?.activeRequests instanceof Map) {
      for (const request of value.activeRequests.values()) {
        const windowId = Number(request?.window_id);
        if (Number.isInteger(windowId)) ids.add(windowId);
      }
    }
    return ids;
  }

  function standbyRows(rows, value) {
    const assigned = routeByWindow(value);
    const busy = busyWindowIds(value);
    return rows.filter(row =>
      row.routable &&
      row.runtime_ready === true &&
      !assigned.has(row.window_id) &&
      !busy.has(row.window_id)
    );
  }

  async function clearRouteAlarm(windowId) {
    if (!Number.isInteger(Number(windowId))) return;
    try { await chrome.alarms.clear(`${ROUTE_ALARM_PREFIX}${Number(windowId)}`); } catch (_) {}
  }

  async function captureRouteUrl(route, tabId) {
    if (!route || !Number.isInteger(Number(tabId))) return;
    try {
      const tab = await chrome.tabs.get(Number(tabId));
      const url = tab?.url || tab?.pendingUrl || "";
      const id = conversationId(url);
      if (id) {
        route.conversation_id = id;
        route.conversation_url = url;
      }
    } catch (_) {}
  }

  async function detachRoute(route, reason = "persistent-pool-detach") {
    if (!route) return;
    const windowId = Number(route.window_id);
    const tabId = Number(route.tab_id);
    await captureRouteUrl(route, tabId);
    if (Number.isInteger(windowId)) await clearRouteAlarm(windowId);
    route.window_id = null;
    route.tab_id = null;
    route.window_owned = true;
    route.close_after = null;
    route.persistent_pool_revision = 132;
    route.last_pool_detach_reason = reason;
    route.last_pool_detach_at = Date.now();
  }

  async function ensureRuntimeCurrent(tabId) {
    const id = Number(tabId);
    if (!Number.isInteger(id)) throw new Error("Persistent ChatGPT runtime preflight requires a valid tab id");
    state.runtimeChecks += 1;
    try {
      if (typeof ensureContent === "function") await ensureContent(id);
      state.runtimeReadyTabs.set(id, true);
      return true;
    } catch (error) {
      state.runtimeReadyTabs.set(id, false);
      state.runtimeFailures += 1;
      throw error;
    }
  }

  async function waitForReady(tabId, timeoutMs = 30000) {
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const tab = await chrome.tabs.get(tabId);
        const url = tab?.url || tab?.pendingUrl || "";
        if (!isChatGpt(url) || isAuthSurface(url) || (tab.status && tab.status !== "complete")) {
          await sleep(180);
          continue;
        }
        await ensureRuntimeCurrent(tabId);
        return tab;
      } catch (error) { lastError = error; }
      await sleep(220);
    }
    throw lastError || new Error("Timed out waiting for persistent ChatGPT window readiness");
  }

  async function createStandby(reason = "persistent-pool-warm") {
    const createWindow = typeof globalThis.chat2apiCreateWindowStaggered === "function"
      ? globalThis.chat2apiCreateWindowStaggered
      : chrome.windows.create.bind(chrome.windows);
    const created = await createWindow(
      { url: NEW_CHAT_URL, focused: false, type: "normal" },
      { reason: `persistent-window-pool-v132:${reason}` },
    );
    if (!Number.isInteger(created?.id)) throw new Error("Chrome did not create a persistent ChatGPT window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item?.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item?.id)) || null;
    }
    if (!tab?.id) {
      try { await chrome.windows.remove(created.id); } catch (_) {}
      throw new Error("Persistent ChatGPT window contains no usable tab");
    }
    try {
      tab = await waitForReady(tab.id);
    } catch (error) {
      try { await chrome.windows.remove(created.id); } catch (_) {}
      throw error;
    }
    state.created += 1;
    return {
      window_id: created.id,
      tab_id: tab.id,
      url: tab.url || NEW_CHAT_URL,
      routable: true,
      runtime_ready: true,
      status: tab.status || "complete",
    };
  }

  async function closeWindow(windowId) {
    if (!Number.isInteger(Number(windowId))) return false;
    try {
      await chrome.windows.remove(Number(windowId));
      state.closed += 1;
      return true;
    } catch (_) { return false; }
  }

  async function protectedWindowIds() {
    const ids = new Set();
    const stored = await chrome.storage.local.get({ [INIT_TAB_KEY]: null }).catch(() => ({}));
    if (Number.isInteger(stored?.[INIT_TAB_KEY])) {
      try {
        const tab = await chrome.tabs.get(stored[INIT_TAB_KEY]);
        if (Number.isInteger(tab?.windowId)) ids.add(tab.windowId);
      } catch (_) {}
    }
    const snapshot = await loginSnapshot();
    if (Number.isInteger(snapshot?.window_id)) ids.add(snapshot.window_id);
    return ids;
  }

  async function validateIdleRuntime(value, rows, reason = "runtime-ready-standby-v152") {
    const preflight = globalThis.__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__;
    if (!preflight || typeof ensureContent !== "function") {
      return { rows, checked: 0, failed: 0, closed: 0, skipped: true };
    }
    const assigned = routeByWindow(value);
    const busy = busyWindowIds(value);
    const candidates = rows.filter(row =>
      row.routable &&
      !assigned.has(row.window_id) &&
      !busy.has(row.window_id) &&
      row.runtime_ready !== true
    );
    if (!candidates.length) return { rows, checked: 0, failed: 0, closed: 0, skipped: false };

    const results = await Promise.all(candidates.map(async row => {
      try {
        await ensureRuntimeCurrent(row.tab_id);
        return { row, ok: true };
      } catch (_) {
        return { row, ok: false };
      }
    }));

    let current = rows.slice();
    let failed = 0;
    let closed = 0;
    for (const result of results) {
      const row = result.row;
      if (result.ok) {
        current = current.map(item => item.tab_id === row.tab_id ? { ...item, runtime_ready: true } : item);
        continue;
      }
      failed += 1;
      if (await closeWindow(row.window_id)) {
        current = current.filter(item => item.window_id !== row.window_id);
        state.runtimeReadyTabs.delete(row.tab_id);
        closed += 1;
      }
    }
    return { rows: current, checked: candidates.length, failed, closed, skipped: false, reason };
  }

  function snapshotFrom(rows, value, ready, isDisabled) {
    const target = normalizeTarget(state.target) || 0;
    const assigned = routeByWindow(value);
    const busy = busyWindowIds(value);
    const routableRows = rows.filter(row => row.routable);
    const unroutableRows = rows.filter(row => !row.routable);
    const runtimeReadyRows = routableRows.filter(row => row.runtime_ready === true);
    const routedRows = routableRows.filter(row => assigned.has(row.window_id));
    const standby = standbyRows(rows, value);
    const inUse = routedRows.filter(row => busy.has(row.window_id)).length;
    const leased = routedRows.filter(row => !busy.has(row.window_id)).length;
    const effective = target ? (isDisabled ? Math.min(1, target) : target) : 0;
    return {
      version: 132,
      revision: 132,
      policy: state.policy,
      target,
      configured_target: target,
      effective_target: effective,
      target_reached: Boolean(target && ready && !isDisabled && standby.length === target) || Boolean(target && isDisabled && standby.length <= effective),
      total: rows.length,
      routable_total: routableRows.length,
      unroutable_total: unroutableRows.length,
      runtime_ready_total: runtimeReadyRows.length,
      runtime_stale_total: routableRows.filter(row => row.runtime_ready !== true).length,
      active: inUse,
      leased,
      idle: standby.length,
      own: rows.length,
      warm: standby.length,
      standby: standby.length,
      routed: routedRows.length,
      idle_routed: leased,
      all_chatgpt_windows: rows.length,
      standby_semantics_revision: STANDBY_SEMANTICS_REVISION,
      standby_excludes_routed_windows: true,
      route_idle_lease_ms: 5 * 60 * 1000,
      runtime_ready_standby: true,
      runtime_preflight_checks: state.runtimeChecks,
      runtime_preflight_failures: state.runtimeFailures,
      login_ready: ready,
      worker_disabled: isDisabled,
      warming: Boolean(target && ready && !isDisabled && standby.length < target),
      excess: Math.max(0, standby.length - effective),
      reserved: state.reservations.size,
      source: state.source,
      created_total: state.created,
      reused_total: state.reused,
      closed_total: state.closed,
      persistent_window_pool: true,
      prewarmed_windows: true,
      speculative_windows: false,
      logical_route_authority: "conversation-routing-v30",
      route_window_authority: "conversation-routing-v30+persistent-pool-v132",
      window_decision_authority: "persistent-window-pool-v132",
      observed_at: new Date().toISOString(),
      last_result: state.lastResult,
    };
  }

  async function snapshot() {
    await ensureLoaded();
    const value = await routerReady();
    const [rows, ready, isDisabled] = await Promise.all([
      physicalWindows(),
      loginReady().catch(() => false),
      disabled(),
    ]);
    return snapshotFrom(rows, value, ready, isDisabled);
  }

  async function shrinkStandby(value, rows, target, reason) {
    let current = rows.slice();
    const protectedIds = await protectedWindowIds();
    let candidates = standbyRows(current, value)
      .sort((left, right) => Number(protectedIds.has(left.window_id)) - Number(protectedIds.has(right.window_id)));
    let excess = Math.max(0, candidates.length - target);
    let closed = 0;
    for (const row of candidates) {
      if (excess <= 0) break;
      if (protectedIds.has(row.window_id)) continue;
      if (await closeWindow(row.window_id)) {
        current = current.filter(item => item.window_id !== row.window_id);
        state.runtimeReadyTabs.delete(row.tab_id);
        excess -= 1;
        closed += 1;
      }
    }
    return { rows: current, closed, deferred: Math.max(0, excess), reason };
  }

  async function reconcileNow(reason = "scheduled") {
    await ensureLoaded();
    const value = await routerReady();
    const target = normalizeTarget(state.target);
    const isDisabled = await disabled();
    const ready = await loginReady().catch(() => false);
    let rows = await physicalWindows();

    let runtimeValidation = { checked: 0, failed: 0, closed: 0, skipped: true };
    if (ready && !isDisabled) {
      runtimeValidation = await validateIdleRuntime(value, rows, reason);
      rows = runtimeValidation.rows;
    }

    if (!target) {
      state.lastResult = { ok: true, reason, target: null, action: "unconfigured", runtime_validation: runtimeValidation, at: Date.now() };
      return snapshotFrom(rows, value, ready, isDisabled);
    }

    const standbyTarget = isDisabled ? Math.min(1, target) : (ready ? target : Math.min(1, target));
    const reduced = await shrinkStandby(value, rows, standbyTarget, reason);
    rows = reduced.rows;

    let opened = 0;
    let lastError = "";
    if (ready && !isDisabled) {
      while (standbyRows(rows, value).length < target) {
        try {
          const row = await createStandby(reason);
          rows.push(row);
          opened += 1;
        } catch (error) {
          lastError = String(error?.message || error);
          break;
        }
      }
    }

    const currentStandby = standbyRows(rows, value).length;
    state.lastResult = {
      ok: !lastError,
      reason,
      target,
      standby_target: standbyTarget,
      standby_before_or_after_total: currentStandby,
      total_windows: rows.length,
      routed_windows: routeByWindow(value).size,
      standby_semantics_revision: STANDBY_SEMANTICS_REVISION,
      runtime_validation: runtimeValidation,
      opened,
      closed: reduced.closed,
      deferred: reduced.deferred,
      pending_reason: isDisabled ? "worker_disabled" : (!ready ? "login_not_ready" : (reduced.deferred ? "protected_standby" : (lastError ? "warm_failed" : ""))),
      error: lastError,
      at: Date.now(),
    };
    return snapshotFrom(rows, value, ready, isDisabled);
  }

  async function reconcile(reason = "scheduled") {
    if (state.reconcilePromise) return state.reconcilePromise;
    const task = serial(() => reconcileNow(reason)).finally(() => {
      if (state.reconcilePromise === task) state.reconcilePromise = null;
    });
    state.reconcilePromise = task;
    return task;
  }

  function scheduleReconcile(reason = "scheduled", delay = 120) {
    if (state.reconcileTimer) clearTimeout(state.reconcileTimer);
    state.reconcileTimer = setTimeout(() => {
      state.reconcileTimer = null;
      reconcile(reason).catch(() => {});
    }, Math.max(0, Number(delay || 0)));
  }

  async function setTarget(requestedTarget, source = "explicit") {
    const target = normalizeTarget(requestedTarget);
    if (!target) throw new Error("Persistent standby target must be an integer between 1 and 32");
    await ensureLoaded();
    state.target = target;
    state.source = String(source || "explicit").slice(0, 40);
    await persistTarget();
    scheduleReconcile("target-updated", 0);
    const value = await routerReady();
    const [rows, ready, isDisabled] = await Promise.all([physicalWindows(), loginReady().catch(() => false), disabled()]);
    return { ok: true, target, source: state.source, snapshot: snapshotFrom(rows, value, ready, isDisabled) };
  }

  function serial(task) {
    const next = state.gate.catch(() => {}).then(task);
    state.gate = next.catch(() => {});
    return next;
  }

  async function routeLiveTab(route) {
    if (!Number.isInteger(route?.tab_id)) return null;
    try {
      const tab = await chrome.tabs.get(route.tab_id);
      const url = tab?.url || tab?.pendingUrl || "";
      return isChatGpt(url) && !isAuthSurface(url) ? tab : null;
    } catch (_) { return null; }
  }

  async function navigateSlot(row, route) {
    const desired = route?.conversation_url && isChatGpt(route.conversation_url) ? route.conversation_url : NEW_CHAT_URL;
    let tab = await chrome.tabs.get(row.tab_id);
    const current = tab?.url || tab?.pendingUrl || "";
    if (current !== desired || tab.status !== "complete") {
      tab = await chrome.tabs.update(row.tab_id, { url: desired, active: true });
    } else {
      try { tab = await chrome.tabs.update(row.tab_id, { active: true }); } catch (_) {}
    }
    return waitForReady(row.tab_id);
  }

  async function applyRoutingTarget(message) {
    const target = normalizeTarget(message?.routing?.worker_window_limit);
    if (!target) return;
    const source = String(message?.routing?.worker_window_limit_source || "server").slice(0, 40);
    await ensureLoaded();
    if (state.target === target && state.source === source) return;
    state.target = target;
    state.source = source;
    await persistTarget();
    scheduleReconcile("routing-config", 250);
  }

  async function claimSlotForRequest(message) {
    const key = routingKey(message);
    if (!key) return null;
    await ensureLoaded();
    const target = normalizeTarget(state.target);
    if (!target || await disabled()) return null;
    if (!await loginReady().catch(() => false)) return null;

    return serial(async () => {
      const value = await routerReady();
      if (!value) return null;
      const route = value.routes[key] || (value.routes[key] = freshRoute(key));
      let existing = await routeLiveTab(route);
      if (existing) {
        try {
          if (existing.status && existing.status !== "complete") existing = await waitForReady(existing.id, 12000);
          else {
            await ensureRuntimeCurrent(existing.id);
            existing = await chrome.tabs.get(existing.id);
          }
        } catch (_) {
          const staleWindowId = Number(route.window_id);
          await detachRoute(route, "persistent-pool-existing-route-runtime-stale-v152");
          if (Number.isInteger(staleWindowId)) await closeWindow(staleWindowId);
          state.runtimeReadyTabs.delete(existing.id);
          existing = null;
        }
      }
      if (existing) {
        // Once a standby is assigned to an API key the conversation router owns
        // the physical window. Its five-minute close alarm must never be cleared
        // by the standby pool. The inner router will clear/re-arm that lease when
        // the same key starts/completes another request.
        route.window_owned = true;
        route.last_active_at = Date.now();
        route.persistent_pool_revision = 132;
        state.reservations.set(key, existing.windowId);
        await persistRoutes(value);
        state.reused += 1;
        return { key, window_id: existing.windowId, tab_id: existing.id, strategy: "reuse-leased-route-v152" };
      }

      route.window_id = null;
      route.tab_id = null;
      route.window_owned = true;
      route.close_after = null;

      let physical = await physicalWindows();
      const runtimeValidation = await validateIdleRuntime(value, physical, "request-admission-v152");
      let rows = runtimeValidation.rows;
      let slot = standbyRows(rows, value)[0] || null;
      let strategy = "claim-standby-v152";

      if (!slot) {
        slot = await createStandby("request-admission");
        rows.push(slot);
        strategy = "warm-on-admission-v152";
      }
      if (!slot) {
        const error = new Error(`Persistent standby window pool exhausted (target=${target})`);
        error.code = "worker_persistent_window_pool_exhausted";
        error.retry_after_ms = 350;
        throw error;
      }

      const tab = await navigateSlot(slot, route);
      route.window_id = slot.window_id;
      route.tab_id = tab.id;
      route.window_owned = true;
      route.close_after = null;
      route.last_active_at = Date.now();
      route.persistent_pool_revision = 132;
      state.reservations.set(key, slot.window_id);
      await chrome.storage.local.set({ boundTabId: tab.id, autoBind: false, modelsUpdatedAt: 0 }).catch(() => {});
      await persistRoutes(value);
      return { key, window_id: slot.window_id, tab_id: tab.id, strategy };
    });
  }

  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver === "function" && !baseResolver.__chat2apiPersistentPoolV132) {
    const wrappedResolver = async message => {
      await applyRoutingTarget(message);
      const assignment = await claimSlotForRequest(message);
      try {
        return await baseResolver(message);
      } finally {
        if (assignment?.key) state.reservations.delete(assignment.key);
        // Consuming a standby immediately makes standby cardinality N-1. Refill
        // after the normal short admission delay; the leased route is excluded
        // from standby and remains owned by conversation_routing for five minutes.
        scheduleReconcile("post-admission-standby-refill-v152", 250);
      }
    };
    wrappedResolver.__chat2apiPersistentPoolV132 = true;
    globalThis.resolveTargetTabForRequest = wrappedResolver;
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const type = String(message?.event?.type || "");
    if (TERMINAL_TYPES.has(type)) scheduleReconcile(`terminal:${type}`, 180);
    return false;
  });

  chrome.windows.onCreated.addListener(() => scheduleReconcile("window-created", 500));
  chrome.windows.onRemoved.addListener(() => scheduleReconcile("window-removed", 250));
  chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    if (changeInfo.url || changeInfo.status) state.runtimeReadyTabs.delete(Number(tabId));
    if (changeInfo.url || changeInfo.status === "complete") scheduleReconcile("tab-updated", 700);
  });
  chrome.tabs.onRemoved.addListener(tabId => state.runtimeReadyTabs.delete(Number(tabId)));

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes[DISABLED_KEY] || changes[LOGIN_STATE_KEY] || changes[LOGIN_COMPOSER_KEY] || changes[INIT_TAB_KEY]) {
      scheduleReconcile("state-change", 200);
    }
    const legacy = changes[LEGACY_LIMIT_STORAGE_KEY]?.newValue;
    const legacyTarget = normalizeTarget(legacy?.limit);
    if (legacyTarget && legacyTarget !== state.target) {
      state.target = legacyTarget;
      state.source = String(legacy?.source || "legacy-limit-sync").slice(0, 40);
      persistTarget().then(() => scheduleReconcile("legacy-limit-sync", 0)).catch(() => {});
    }
  });

  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm?.name === REPAIR_ALARM) reconcile("repair-alarm").catch(() => {});
  });
  chrome.alarms.create(REPAIR_ALARM, { periodInMinutes: 1 }).catch?.(() => {});

  state.setTarget = setTarget;
  state.reconcile = reconcile;
  state.snapshot = snapshot;
  state.claimSlotForRequest = claimSlotForRequest;
  state.scheduleReconcile = scheduleReconcile;
  state.standbyRows = standbyRows;

  ensureLoaded().then(() => scheduleReconcile("startup", 500)).catch(() => {});
})();
