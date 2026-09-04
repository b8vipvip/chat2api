(() => {
  const KEY = "__CHAT2API_WINDOW_MANAGER_V88__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const WARM_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";
  const RESERVE_KEY = "__CHAT2API_RESERVE_POOL_V29__";
  const STORAGE_KEY = "chat2apiWindowManagerV88";
  const ROUTE_STORAGE_KEY = "chat2apiConversationRoutesV1";
  const WARM_STORAGE_KEY = "chat2apiConversationWarmPoolV2";
  const RESERVE_STORAGE_KEY = "chat2apiReservePoolV29";
  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";
  const SUCCESS_LEASE_MS = 5 * 60 * 1000;
  const CLOSED_LIMIT = 80;
  const REPORT_DELAY_MS = 80;

  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver !== "function") return;

  const state = {
    revision: 88,
    nextWindowNo: 1,
    active: new Map(),
    closed: [],
    loaded: false,
    reconcileInFlight: null,
    reconcileTimer: null,
    reportTimer: null,
    reportInFlight: null,
    lastReportSignature: "",
    requestAssignments: new Map(),
    successfulRequests: new Map(),
    protectedUntil: new Map(),
    fifoClaims: 0,
    newWindowFallbacks: 0,
  };
  globalThis[KEY] = state;
  globalThis.chat2apiWindowManagerV88 = state;

  function isChatGpt(value = "") {
    try {
      return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname);
    } catch (_) {
      return false;
    }
  }

  function routeKey(message) {
    const value = message?.routing?.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
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
      last_rotation_reason: "window-manager-v88-fifo-claim",
      tab_id: null,
      window_id: null,
      window_owned: true,
      inflight_request_id: null,
      last_active_at: 0,
      close_after: null,
    };
  }

  function serializableRecord(record) {
    return {
      window_no: Number(record.window_no || 0),
      window_id: Number(record.window_id),
      tab_id: Number.isInteger(record.tab_id) ? record.tab_id : null,
      opened_at_ms: Number(record.opened_at_ms || 0),
      opened_at: record.opened_at || null,
      status: String(record.status || "loading"),
      request_id: record.request_id || null,
      route_key: record.route_key || null,
      source: record.source || null,
      ready_at_ms: Number(record.ready_at_ms || 0),
      last_seen_at_ms: Number(record.last_seen_at_ms || 0),
      closed_at_ms: Number(record.closed_at_ms || 0),
      closed_at: record.closed_at || null,
      screenshot_data_url: record.screenshot_data_url || null,
      screenshot_at_ms: Number(record.screenshot_at_ms || 0),
      screenshot_at: record.screenshot_at || null,
      screenshot_error: record.screenshot_error || null,
    };
  }

  async function loadStored() {
    if (state.loaded) return;
    state.loaded = true;
    const stored = await chrome.storage.local.get({ [STORAGE_KEY]: null }).catch(() => ({}));
    const payload = stored?.[STORAGE_KEY];
    state.nextWindowNo = Math.max(1, Number(payload?.next_window_no || 1));
    state.closed = Array.isArray(payload?.closed)
      ? payload.closed.slice(0, CLOSED_LIMIT).map(row => ({ ...row }))
      : [];
  }

  async function persistState() {
    await loadStored();
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        revision: 88,
        next_window_no: state.nextWindowNo,
        active: [...state.active.values()].map(serializableRecord),
        closed: state.closed.slice(0, CLOSED_LIMIT).map(serializableRecord),
        updated_at_ms: Date.now(),
      },
    }).catch(() => {});
  }

  async function persistRoutes() {
    const router = globalThis[ROUTER_KEY];
    if (!router?.routes) return;
    await chrome.storage.local.set({ [ROUTE_STORAGE_KEY]: router.routes }).catch(() => {});
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

  function iso(ms) {
    if (!Number.isFinite(Number(ms)) || Number(ms) <= 0) return null;
    try { return new Date(Number(ms)).toISOString(); } catch (_) { return null; }
  }

  function ensureRecord(source) {
    const windowId = Number(source?.window_id);
    if (!Number.isInteger(windowId)) return null;
    let record = state.active.get(windowId) || null;
    const openedAt = Number(source?.opened_at_ms || source?.created_at_ms || record?.opened_at_ms || Date.now());
    if (!record) {
      record = {
        window_no: state.nextWindowNo++,
        window_id: windowId,
        tab_id: Number.isInteger(source?.tab_id) ? source.tab_id : null,
        opened_at_ms: openedAt,
        opened_at: iso(openedAt),
        status: source?.status || "loading",
        request_id: source?.request_id || null,
        route_key: source?.route_key || null,
        source: source?.source || "managed",
        ready_at_ms: Number(source?.ready_at_ms || 0),
        last_seen_at_ms: Date.now(),
        screenshot_data_url: null,
        screenshot_at_ms: 0,
        screenshot_at: null,
        screenshot_error: null,
      };
      state.active.set(windowId, record);
    }
    record.tab_id = Number.isInteger(source?.tab_id) ? source.tab_id : record.tab_id;
    record.opened_at_ms = Math.min(Number(record.opened_at_ms || openedAt), openedAt);
    record.opened_at = iso(record.opened_at_ms);
    record.status = source?.status || record.status || "loading";
    record.request_id = source?.request_id ?? record.request_id ?? null;
    record.route_key = source?.route_key ?? record.route_key ?? null;
    record.source = source?.source || record.source || "managed";
    record.ready_at_ms = Number(source?.ready_at_ms || record.ready_at_ms || 0);
    record.last_seen_at_ms = Date.now();
    return record;
  }

  function markClosed(record, reason = "window-removed") {
    if (!record) return;
    state.active.delete(Number(record.window_id));
    const closedAt = Date.now();
    const row = {
      ...serializableRecord(record),
      status: "closed",
      closed_at_ms: closedAt,
      closed_at: iso(closedAt),
      close_reason: reason,
    };
    state.closed = [row, ...state.closed.filter(item => Number(item.window_id) !== Number(record.window_id))].slice(0, CLOSED_LIMIT);
    state.protectedUntil.delete(Number(record.window_id));
  }

  async function liveChatGptTabsByWindow() {
    const result = new Map();
    try {
      const windows = await chrome.windows.getAll({ populate: true });
      for (const win of windows || []) {
        if (!Number.isInteger(win?.id)) continue;
        const tab = (win.tabs || []).find(item => Number.isInteger(item?.id) && isChatGpt(item.url || item.pendingUrl || ""));
        if (tab) result.set(win.id, tab);
      }
    } catch (_) {}
    return result;
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

  function managedSources() {
    const rows = [];
    const reserve = globalThis[RESERVE_KEY];
    for (const [slotKey, slot] of reserve?.reserveSlots?.entries?.() || []) {
      rows.push({
        source: "reserve",
        slot_key: slotKey,
        tab_id: slot?.tab_id,
        window_id: slot?.window_id,
        created_at_ms: Number(slot?.created_at_ms || 0),
        opened_at_ms: Number(slot?.created_at_ms || 0),
        ready_at_ms: Number(slot?.ready_at_ms || 0),
        status: slot?.ready === true ? "ready" : "loading",
        request_id: null,
        route_key: null,
      });
    }

    const warm = globalThis[WARM_KEY];
    for (const [slotKey, slot] of warm?.warmSlots?.entries?.() || []) {
      rows.push({
        source: "warm",
        slot_key: slotKey,
        tab_id: slot?.tab_id,
        window_id: slot?.window_id,
        created_at_ms: Number(slot?.created_at_ms || 0),
        opened_at_ms: Number(slot?.created_at_ms || 0),
        ready_at_ms: Number(slot?.ready_at_ms || 0),
        status: "ready",
        request_id: null,
        route_key: null,
      });
    }

    const router = globalThis[ROUTER_KEY];
    for (const [key, route] of Object.entries(router?.routes || {})) {
      if (!Number.isInteger(route?.window_id)) continue;
      rows.push({
        source: "route",
        tab_id: route.tab_id,
        window_id: route.window_id,
        opened_at_ms: Number(route.window_opened_at_ms || route.prewarm_created_at_ms || route.last_active_at || 0),
        ready_at_ms: 0,
        status: route.inflight_request_id ? "in_use" : "ready",
        request_id: route.inflight_request_id || null,
        route_key: key,
      });
    }
    return rows.sort((a, b) => {
      const left = Number(a.opened_at_ms || a.created_at_ms || Number.MAX_SAFE_INTEGER);
      const right = Number(b.opened_at_ms || b.created_at_ms || Number.MAX_SAFE_INTEGER);
      if (left !== right) return left - right;
      return Number(a.window_id || 0) - Number(b.window_id || 0);
    });
  }

  async function reconcileNow(forceReport = false) {
    if (state.reconcileInFlight) return state.reconcileInFlight;
    state.reconcileInFlight = (async () => {
      await loadStored();
      const live = await liveChatGptTabsByWindow();
      const seen = new Set();
      for (const source of managedSources()) {
        const windowId = Number(source.window_id);
        const tab = live.get(windowId);
        if (!tab) continue;
        source.tab_id = tab.id;
        seen.add(windowId);
        ensureRecord(source);
      }
      for (const [windowId, record] of [...state.active.entries()]) {
        if (!live.has(windowId)) {
          markClosed(record, "window-not-live");
          continue;
        }
        if (!seen.has(windowId)) {
          // A just-claimed pool slot may briefly disappear from pool storage before
          // the route write becomes visible. Keep it live instead of inventing a
          // closed/opened transition and losing its original FIFO number.
          record.last_seen_at_ms = Date.now();
        }
      }
      await persistState();
      scheduleReport(0, forceReport);
      return [...state.active.values()].map(serializableRecord);
    })().finally(() => { state.reconcileInFlight = null; });
    return state.reconcileInFlight;
  }

  function scheduleReconcile(delayMs = 100, forceReport = false) {
    clearTimeout(state.reconcileTimer);
    state.reconcileTimer = setTimeout(() => {
      state.reconcileTimer = null;
      reconcileNow(forceReport).catch(() => {});
    }, Math.max(0, delayMs));
  }

  function publicSnapshot() {
    return {
      revision: 88,
      policy: "oldest-ready-fifo-v88",
      fifo_claims: state.fifoClaims,
      new_window_fallbacks: state.newWindowFallbacks,
      active: [...state.active.values()]
        .map(serializableRecord)
        .sort((a, b) => Number(a.window_no || 0) - Number(b.window_no || 0)),
      closed: state.closed.slice(0, CLOSED_LIMIT).map(row => ({ ...row })),
      updated_at_ms: Date.now(),
    };
  }

  async function reportStatus(force = false) {
    if (state.reportInFlight) return state.reportInFlight;
    state.reportInFlight = (async () => {
      const snapshot = publicSnapshot();
      const signature = JSON.stringify(snapshot.active.map(row => [
        row.window_no, row.window_id, row.tab_id, row.status, row.request_id,
        row.screenshot_at_ms, row.closed_at_ms,
      ]).concat(snapshot.closed.slice(0, 20).map(row => [row.window_no, row.closed_at_ms, row.screenshot_at_ms])));
      if (!force && signature === state.lastReportSignature) return snapshot;
      state.lastReportSignature = signature;
      if (typeof trySendSocket === "function") {
        await trySendSocket({
          type: "extension.status",
          metadata: {
            window_manager_v88: snapshot,
            window_manager_revision: 88,
            window_selection_policy: "oldest-ready-fifo-v88",
          },
        }).catch(() => false);
      }
      return snapshot;
    })().finally(() => { state.reportInFlight = null; });
    return state.reportInFlight;
  }

  function scheduleReport(delayMs = REPORT_DELAY_MS, force = false) {
    clearTimeout(state.reportTimer);
    state.reportTimer = setTimeout(() => {
      state.reportTimer = null;
      reportStatus(force).catch(() => {});
    }, Math.max(0, delayMs));
  }

  async function existingLiveRoute(key) {
    const router = globalThis[ROUTER_KEY];
    const route = router?.routes?.[key];
    if (!route || !Number.isInteger(route.tab_id)) return null;
    const tab = await tabExists(route.tab_id);
    return tab ? { route, tab } : null;
  }

  async function fifoCandidates() {
    await reconcileNow(false);
    const live = await liveChatGptTabsByWindow();
    const rows = [];
    const reserve = globalThis[RESERVE_KEY];
    for (const [slotKey, slot] of reserve?.reserveSlots?.entries?.() || []) {
      if (slot?.ready !== true || !live.has(Number(slot.window_id))) continue;
      const record = state.active.get(Number(slot.window_id));
      rows.push({
        source: "reserve", slot_key: slotKey, slot, tab: live.get(Number(slot.window_id)), record,
        opened_at_ms: Number(slot.created_at_ms || record?.opened_at_ms || 0),
      });
    }
    const warm = globalThis[WARM_KEY];
    for (const [slotKey, slot] of warm?.warmSlots?.entries?.() || []) {
      if (!live.has(Number(slot.window_id))) continue;
      const record = state.active.get(Number(slot.window_id));
      rows.push({
        source: "warm", slot_key: slotKey, slot, tab: live.get(Number(slot.window_id)), record,
        opened_at_ms: Number(slot.created_at_ms || record?.opened_at_ms || 0),
      });
    }
    rows.sort((a, b) => {
      const left = Number(a.opened_at_ms || Number.MAX_SAFE_INTEGER);
      const right = Number(b.opened_at_ms || Number.MAX_SAFE_INTEGER);
      if (left !== right) return left - right;
      const leftNo = Number(a.record?.window_no || Number.MAX_SAFE_INTEGER);
      const rightNo = Number(b.record?.window_no || Number.MAX_SAFE_INTEGER);
      if (leftNo !== rightNo) return leftNo - rightNo;
      return Number(a.tab?.windowId || 0) - Number(b.tab?.windowId || 0);
    });
    return rows;
  }

  async function claimOldestReady(message, key) {
    if (!key) return null;
    const liveExisting = await existingLiveRoute(key);
    if (liveExisting) return null;

    const candidates = await fifoCandidates();
    const selected = candidates[0] || null;
    if (!selected?.tab?.id) return null;

    const router = globalThis[ROUTER_KEY];
    if (!router?.routes) return null;
    let route = router.routes[key] || freshRoute(key);
    const hadSession = Boolean(
      route.conversation_id || route.conversation_url || Number(route.turn_count || 0) ||
      Number(route.text_chars || 0) || Number(route.attachment_count || 0)
    );
    if (hadSession) route.generation = Number(route.generation || 1) + 1;
    route.conversation_id = null;
    route.conversation_url = null;
    route.turn_count = 0;
    route.text_chars = 0;
    route.attachment_count = 0;
    route.slow_load_strikes = 0;
    route.tab_id = selected.tab.id;
    route.window_id = selected.tab.windowId;
    route.window_owned = true;
    route.inflight_request_id = null;
    route.last_active_at = Date.now();
    route.close_after = null;
    route.last_open_ms = 0;
    route.last_rotation_reason = "oldest-ready-fifo-v88";
    route.prewarm_claimed_at = Date.now();
    route.prewarm_load_ms = Math.max(0, Date.now() - Number(selected.opened_at_ms || Date.now()));
    route.prewarm_ready_age_ms = Math.max(0, Date.now() - Number(selected.slot?.ready_at_ms || Date.now()));
    route.prewarm_source = `window-manager-v88:${selected.source}`;
    route.prewarm_created_at_ms = Number(selected.opened_at_ms || Date.now());
    route.window_opened_at_ms = Number(selected.opened_at_ms || Date.now());
    route.window_no = Number(selected.record?.window_no || 0) || null;
    router.routes[key] = route;

    if (selected.source === "reserve") {
      globalThis[RESERVE_KEY]?.reserveSlots?.delete?.(selected.slot_key);
      await persistReserve();
    } else {
      globalThis[WARM_KEY]?.warmSlots?.delete?.(selected.slot_key);
      if (message?.request_id) globalThis[WARM_KEY]?.claimedRequests?.add?.(message.request_id);
      await persistWarm();
    }
    await persistRoutes();

    const record = ensureRecord({
      source: "route",
      tab_id: selected.tab.id,
      window_id: selected.tab.windowId,
      opened_at_ms: route.window_opened_at_ms,
      status: "in_use",
      request_id: message?.request_id || null,
      route_key: key,
    });
    if (record) {
      record.status = "in_use";
      record.request_id = message?.request_id || null;
      record.route_key = key;
    }
    if (message?.request_id) {
      state.requestAssignments.set(String(message.request_id), {
        key,
        tab_id: selected.tab.id,
        window_id: selected.tab.windowId,
        window_no: record?.window_no || null,
        opened_at_ms: route.window_opened_at_ms,
      });
    }
    state.fifoClaims += 1;
    await persistState();
    scheduleReport(0, true);

    if (message?.request_id && typeof trySendSocket === "function") {
      const eventType = message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics";
      await trySendSocket({
        type: eventType,
        request_id: message.request_id,
        diagnostics: {
          window_manager_revision: 88,
          window_selection_policy: "oldest-ready-fifo-v88",
          window_fifo_claimed: true,
          window_fifo_source: selected.source,
          window_no: record?.window_no || null,
          window_opened_at_ms: route.window_opened_at_ms,
          routed_tab_id: selected.tab.id,
          routed_window_id: selected.tab.windowId,
        },
      }).catch(() => false);
    }
    return { tab: selected.tab, route, record, source: selected.source };
  }

  globalThis.resolveTargetTabForRequest = async function resolveWithWindowManagerV88(message) {
    const key = routeKey(message);
    let claimed = null;
    if (key) claimed = await claimOldestReady(message, key).catch(() => null);
    const tab = await baseResolver(message);
    const router = globalThis[ROUTER_KEY];
    const route = key ? router?.routes?.[key] : null;
    const openedAt = Number(route?.window_opened_at_ms || claimed?.record?.opened_at_ms || Date.now());
    const record = ensureRecord({
      source: "route",
      tab_id: tab?.id,
      window_id: tab?.windowId,
      opened_at_ms: openedAt,
      status: "in_use",
      request_id: message?.request_id || null,
      route_key: key,
    });
    if (route && record) {
      route.window_opened_at_ms = Number(route.window_opened_at_ms || record.opened_at_ms);
      route.window_no = Number(route.window_no || record.window_no);
      await persistRoutes();
    }
    if (message?.request_id && Number.isInteger(tab?.windowId)) {
      state.requestAssignments.set(String(message.request_id), {
        key,
        tab_id: tab.id,
        window_id: tab.windowId,
        window_no: record?.window_no || null,
        opened_at_ms: record?.opened_at_ms || openedAt,
      });
    }
    if (!claimed) state.newWindowFallbacks += 1;
    await persistState();
    scheduleReport(0);
    return tab;
  };

  function captureRouteSnapshot(requestId) {
    const assignment = state.requestAssignments.get(String(requestId || ""));
    if (!assignment?.key) return null;
    const route = globalThis[ROUTER_KEY]?.routes?.[assignment.key];
    if (!route) return null;
    return { assignment: { ...assignment }, route: JSON.parse(JSON.stringify(route)) };
  }

  function protectSuccessfulRequest(requestId) {
    const id = String(requestId || "");
    const assignment = state.requestAssignments.get(id);
    if (!assignment) return;
    const until = Date.now() + SUCCESS_LEASE_MS;
    state.protectedUntil.set(Number(assignment.window_id), until);
    const first = captureRouteSnapshot(id);
    state.successfulRequests.set(id, {
      ...(first || { assignment: { ...assignment }, route: null }),
      success_at_ms: Date.now(),
      protected_until_ms: until,
    });
    setTimeout(() => {
      const latest = captureRouteSnapshot(id);
      const success = state.successfulRequests.get(id);
      if (success && latest) {
        success.assignment = latest.assignment;
        success.route = latest.route;
      }
    }, 55);
    setTimeout(() => {
      state.successfulRequests.delete(id);
      state.requestAssignments.delete(id);
    }, SUCCESS_LEASE_MS + 5000);
  }

  async function repairSuccessfulRoute(requestId) {
    const id = String(requestId || "");
    const success = state.successfulRequests.get(id);
    if (!success?.assignment?.key) return false;
    const tab = await tabExists(success.assignment.tab_id);
    if (!tab) return false;
    const router = globalThis[ROUTER_KEY];
    if (!router?.routes) return false;
    const restored = success.route ? JSON.parse(JSON.stringify(success.route)) : freshRoute(success.assignment.key);
    restored.api_key_id = success.assignment.key;
    restored.tab_id = tab.id;
    restored.window_id = tab.windowId;
    restored.window_owned = true;
    restored.inflight_request_id = null;
    restored.last_active_at = Date.now();
    restored.close_after = restored.last_active_at + SUCCESS_LEASE_MS;
    restored.window_opened_at_ms = Number(success.assignment.opened_at_ms || restored.window_opened_at_ms || Date.now());
    restored.window_no = success.assignment.window_no || restored.window_no || null;
    router.routes[success.assignment.key] = restored;
    try {
      await chrome.alarms.create(`${ROUTE_ALARM_PREFIX}${tab.windowId}`, { when: restored.close_after });
    } catch (_) {}
    await persistRoutes();
    ensureRecord({
      source: "route", tab_id: tab.id, window_id: tab.windowId,
      opened_at_ms: restored.window_opened_at_ms, status: "ready", request_id: null,
      route_key: success.assignment.key,
    });
    await persistState();
    scheduleReport(0, true);
    return true;
  }

  const baseWindowRemove = chrome.windows.remove.bind(chrome.windows);
  chrome.windows.remove = async function removeWithSuccessfulLeaseV88(windowId, ...args) {
    const id = Number(windowId);
    const until = Number(state.protectedUntil.get(id) || 0);
    if (Number.isInteger(id) && until > Date.now()) {
      // Success is monotonic. Recovery/quarantine code is not allowed to reinterpret
      // a later synthetic cancellation as permission to destroy the successful
      // conversation window. User-initiated browser closes still arrive through
      // chrome.windows.onRemoved and are never blocked here.
      return undefined;
    }
    state.protectedUntil.delete(id);
    return baseWindowRemove(windowId, ...args);
  };

  async function captureWindow(windowId) {
    await reconcileNow(false);
    const id = Number(windowId);
    const record = state.active.get(id) || state.closed.find(item => Number(item.window_id) === id) || null;
    if (!record) throw new Error("Unknown Worker window");
    if (!state.active.has(id)) throw new Error("Closed Worker windows can only show their last captured screenshot");
    try {
      const dataUrl = await chrome.tabs.captureVisibleTab(id, { format: "jpeg", quality: 55 });
      record.screenshot_data_url = String(dataUrl || "");
      record.screenshot_at_ms = Date.now();
      record.screenshot_at = iso(record.screenshot_at_ms);
      record.screenshot_error = null;
      await persistState();
      await reportStatus(true);
      return { ok: true, window_id: id, screenshot_at_ms: record.screenshot_at_ms };
    } catch (error) {
      record.screenshot_error = String(error?.message || error);
      await persistState();
      await reportStatus(true);
      throw error;
    }
  }
  state.capture = captureWindow;
  state.reconcile = reconcileNow;
  state.snapshot = publicSnapshot;
  state.report = reportStatus;

  // Server-side Window Management uses the existing authenticated Worker socket.
  // Keep this as a narrow additive control message so request dispatch ownership
  // remains unchanged.
  const baseHandleServerMessage = globalThis.handleServerMessage;
  if (typeof baseHandleServerMessage === "function") {
    globalThis.handleServerMessage = async function handleServerMessageWithWindowManagerV88(message) {
      if (message?.type === "window.manager.capture") {
        try {
          const result = await captureWindow(Number(message.window_id));
          if (typeof trySendSocket === "function") {
            await trySendSocket({
              type: "window.manager.result",
              control_id: String(message.control_id || ""),
              ok: true,
              data: result,
            }).catch(() => false);
          }
        } catch (error) {
          if (typeof trySendSocket === "function") {
            await trySendSocket({
              type: "window.manager.result",
              control_id: String(message.control_id || ""),
              ok: false,
              error: String(error?.message || error),
            }).catch(() => false);
          }
        }
        return;
      }
      return baseHandleServerMessage(message);
    };
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    const requestId = String(event.request_id || "");
    if (!requestId) return false;
    const assignment = state.requestAssignments.get(requestId);

    if (event.type === "chat.started" || event.type === "image.started") {
      if (assignment) {
        const record = state.active.get(Number(assignment.window_id));
        if (record) {
          record.status = "in_use";
          record.request_id = requestId;
        }
      }
      scheduleReport(0);
      return false;
    }

    if (event.type === "chat.completed" || event.type === "image.completed") {
      protectSuccessfulRequest(requestId);
      if (assignment) {
        const record = state.active.get(Number(assignment.window_id));
        if (record) {
          record.status = "ready";
          record.request_id = null;
          record.route_key = assignment.key || record.route_key;
        }
      }
      persistState().catch(() => {});
      scheduleReport(0, true);
      scheduleReconcile(120, true);
      return false;
    }

    if (event.type === "chat.cancelled" || event.type === "chat.error" || event.type === "image.cancelled" || event.type === "image.error") {
      if (state.successfulRequests.has(requestId)) {
        // This is the exact v0.22.55 failure seen in diagnostics: network success
        // followed ~100 ms later by a synthetic cancellation. Earlier wrappers
        // may already have begun resetting the route, so restore the success state
        // after their bounded recovery delay.
        setTimeout(() => repairSuccessfulRoute(requestId).catch(() => {}), 360);
        return false;
      }
      if (assignment) {
        const record = state.active.get(Number(assignment.window_id));
        if (record) {
          record.status = "loading";
          record.request_id = null;
        }
      }
      scheduleReconcile(220, true);
      return false;
    }
    return false;
  });

  chrome.windows.onRemoved.addListener(windowId => {
    const record = state.active.get(Number(windowId));
    if (record) markClosed(record, "chrome-window-removed");
    persistState().catch(() => {});
    scheduleReport(0, true);
    scheduleReconcile(180, true);
  });
  chrome.tabs.onRemoved.addListener((_tabId, removeInfo) => {
    if (Number.isInteger(removeInfo?.windowId)) scheduleReconcile(160, true);
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes[ROUTE_STORAGE_KEY] || changes[WARM_STORAGE_KEY] || changes[RESERVE_STORAGE_KEY]) {
      scheduleReconcile(100);
    }
  });

  setTimeout(() => reconcileNow(true).catch(() => {}), 320);
})();
