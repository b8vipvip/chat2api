(() => {
  const KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const LOGIN_KEY = "__CHAT2API_LOGIN_READINESS_V27__";
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
    reconcilePromise: null,
    reconcileTimer: null,
    lastResult: null,
    created: 0,
    reused: 0,
    reassigned: 0,
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
      window_owned: false,
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
    if (typeof readiness?.readyForPrewarm === "function") {
      try { return await readiness.readyForPrewarm(); } catch (_) {}
    }
    const stored = await chrome.storage.local.get({
      chatgptLoginState: "unknown",
      chatgptLoginComposerReady: false,
    }).catch(() => ({}));
    return stored.chatgptLoginState === "ready" && stored.chatgptLoginComposerReady === true;
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

  async function clearRouteAlarm(windowId) {
    if (!Number.isInteger(Number(windowId))) return;
    try { await chrome.alarms.clear(`${ROUTE_ALARM_PREFIX}${Number(windowId)}`); } catch (_) {}
  }

  async function markPooledRoute(route) {
    if (!route) return false;
    const windowId = Number(route.window_id);
    if (Number.isInteger(windowId)) await clearRouteAlarm(windowId);
    const changed = route.window_owned !== false || route.close_after != null || Number(route.persistent_pool_revision || 0) !== 132;
    route.window_owned = false;
    route.close_after = null;
    route.persistent_pool_revision = 132;
    return changed;
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

  async function detachRoute(route, reason = "persistent-pool-reassign") {
    if (!route) return;
    const windowId = Number(route.window_id);
    const tabId = Number(route.tab_id);
    await captureRouteUrl(route, tabId);
    if (Number.isInteger(windowId)) await clearRouteAlarm(windowId);
    route.window_id = null;
    route.tab_id = null;
    route.window_owned = false;
    route.close_after = null;
    route.persistent_pool_revision = 132;
    route.last_pool_detach_reason = reason;
    route.last_pool_detach_at = Date.now();
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
        if (typeof ensureContent === "function") await ensureContent(tabId);
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
    return { window_id: created.id, tab_id: tab.id, url: tab.url || NEW_CHAT_URL, routable: true, status: tab.status || "complete" };
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

  function snapshotFrom(rows, value, ready, isDisabled) {
    const target = normalizeTarget(state.target) || 0;
    const effective = target ? (isDisabled ? Math.min(1, target) : (ready ? target : Math.min(1, target))) : 0;
    const assigned = routeByWindow(value);
    const busy = busyWindowIds(value);
    const routedRows = rows.filter(row => assigned.has(row.window_id));
    const standbyRows = rows.filter(row => !assigned.has(row.window_id));
    const inUse = rows.filter(row => busy.has(row.window_id)).length;
    return {
      version: 132,
      revision: 132,
      policy: state.policy,
      target,
      configured_target: target,
      effective_target: effective,
      target_reached: Boolean(target && ready && !isDisabled && rows.length === target) || Boolean(target && isDisabled && rows.length <= effective),
      total: rows.length,
      active: inUse,
      idle: Math.max(0, rows.length - inUse),
      own: rows.length,
      warm: standbyRows.length,
      standby: standbyRows.length,
      routed: routedRows.length,
      idle_routed: routedRows.filter(row => !busy.has(row.window_id)).length,
      all_chatgpt_windows: rows.length,
      login_ready: ready,
      worker_disabled: isDisabled,
      warming: Boolean(target && ready && !isDisabled && rows.length < target),
      excess: Math.max(0, rows.length - effective),
      reserved: state.reservations.size,
      source: state.source,
      created_total: state.created,
      reused_total: state.reused,
      reassigned_total: state.reassigned,
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

  async function shrink(value, rows, target, reason) {
    let current = rows.slice();
    const busy = busyWindowIds(value);
    const assigned = routeByWindow(value);
    const protectedIds = await protectedWindowIds();
    let excess = Math.max(0, current.length - target);
    let routesChanged = false;
    let closed = 0;
    if (!excess) return { rows: current, closed, deferred: 0, routesChanged };

    const standby = current
      .filter(row => !assigned.has(row.window_id) && !busy.has(row.window_id))
      .sort((left, right) => Number(protectedIds.has(left.window_id)) - Number(protectedIds.has(right.window_id)));
    for (const row of standby) {
      if (excess <= 0) break;
      if (await closeWindow(row.window_id)) {
        current = current.filter(item => item.window_id !== row.window_id);
        excess -= 1;
        closed += 1;
      }
    }

    if (excess > 0) {
      const latestAssigned = routeByWindow(value);
      const idleRoutes = [...latestAssigned.values()]
        .filter(entry => {
          const windowId = Number(entry.route?.window_id);
          return Number.isInteger(windowId) && !busy.has(windowId) && !entry.route?.inflight_request_id;
        })
        .sort((left, right) => Number(left.route?.last_active_at || 0) - Number(right.route?.last_active_at || 0));
      for (const entry of idleRoutes) {
        if (excess <= 0) break;
        const windowId = Number(entry.route.window_id);
        await detachRoute(entry.route, `${reason}:shrink`);
        routesChanged = true;
        if (await closeWindow(windowId)) {
          current = current.filter(item => item.window_id !== windowId);
          excess -= 1;
          closed += 1;
        }
      }
    }

    if (routesChanged) await persistRoutes(value);
    return { rows: current, closed, deferred: Math.max(0, excess), routesChanged };
  }

  async function reconcileNow(reason = "scheduled") {
    await ensureLoaded();
    const value = await routerReady();
    const target = normalizeTarget(state.target);
    const isDisabled = await disabled();
    const ready = await loginReady().catch(() => false);
    let rows = await physicalWindows();
    let routesChanged = false;

    if (value) {
      const liveIds = new Set(rows.map(row => row.window_id));
      for (const [, route] of routeEntries(value)) {
        if (!liveIds.has(Number(route?.window_id))) continue;
        if (await markPooledRoute(route)) routesChanged = true;
      }
      if (routesChanged) await persistRoutes(value);
    }

    if (!target) {
      state.lastResult = { ok: true, reason, target: null, action: "unconfigured", at: Date.now() };
      return snapshotFrom(rows, value, ready, isDisabled);
    }

    const effectiveTarget = isDisabled ? Math.min(1, target) : (ready ? target : Math.min(1, target));
    let closed = 0;
    let deferred = 0;
    if (isDisabled || ready) {
      const reduced = await shrink(value, rows, effectiveTarget, reason);
      rows = reduced.rows;
      closed += reduced.closed;
      deferred += reduced.deferred;
    }

    let opened = 0;
    let lastError = "";
    if (ready && !isDisabled) {
      while (rows.length < effectiveTarget) {
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

    state.lastResult = {
      ok: !lastError,
      reason,
      target,
      effective_target: effectiveTarget,
      before_or_after_total: rows.length,
      opened,
      closed,
      deferred,
      pending_reason: isDisabled ? "worker_disabled" : (!ready ? "login_not_ready" : (deferred ? "busy_windows_protected" : (lastError ? "warm_failed" : ""))),
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
    if (!target) throw new Error("Persistent window target must be an integer between 1 and 32");
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
      const existing = await routeLiveTab(route);
      if (existing) {
        await markPooledRoute(route);
        route.last_active_at = Date.now();
        state.reservations.set(key, existing.windowId);
        await persistRoutes(value);
        state.reused += 1;
        return { key, window_id: existing.windowId, tab_id: existing.id, strategy: "reuse-persistent-route" };
      }

      if (Number.isInteger(route.window_id)) await clearRouteAlarm(route.window_id);
      route.window_id = null;
      route.tab_id = null;
      route.window_owned = false;
      route.close_after = null;

      let rows = (await physicalWindows()).filter(row => row.routable);
      let assigned = routeByWindow(value);
      const busy = busyWindowIds(value);
      let slot = rows.find(row => !assigned.has(row.window_id) && !busy.has(row.window_id)) || null;
      let strategy = "claim-standby";

      if (!slot) {
        const victims = [...assigned.values()]
          .filter(entry => {
            const windowId = Number(entry.route?.window_id);
            return entry.key !== key && Number.isInteger(windowId) && !busy.has(windowId) && !entry.route?.inflight_request_id;
          })
          .sort((left, right) => Number(left.route?.last_active_at || 0) - Number(right.route?.last_active_at || 0));
        const victim = victims[0];
        if (victim) {
          const windowId = Number(victim.route.window_id);
          const tabId = Number(victim.route.tab_id);
          slot = rows.find(row => row.window_id === windowId && row.tab_id === tabId) || null;
          if (slot) {
            await detachRoute(victim.route, "persistent-pool-lru-reassign");
            strategy = "reassign-idle-persistent-route";
            state.reassigned += 1;
          }
        }
      }

      if (!slot && rows.length < target) {
        slot = await createStandby("request-admission");
        rows.push(slot);
        strategy = "warm-on-admission";
      }

      if (!slot) {
        const error = new Error(`Persistent Worker window pool exhausted (${rows.length}/${target})`);
        error.code = "worker_persistent_window_pool_exhausted";
        error.retry_after_ms = 350;
        throw error;
      }

      const tab = await navigateSlot(slot, route);
      route.window_id = slot.window_id;
      route.tab_id = tab.id;
      route.window_owned = false;
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
        scheduleReconcile("post-admission", 250);
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
  chrome.tabs.onUpdated.addListener((_tabId, changeInfo) => {
    if (changeInfo.url || changeInfo.status === "complete") scheduleReconcile("tab-updated", 700);
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes[DISABLED_KEY] || changes.chatgptLoginState || changes.chatgptLoginComposerReady || changes[INIT_TAB_KEY]) {
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

  ensureLoaded().then(() => scheduleReconcile("startup", 500)).catch(() => {});
})();
