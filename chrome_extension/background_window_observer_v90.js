(() => {
  const KEY = "__CHAT2API_WINDOW_OBSERVER_V90__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const STORAGE_KEY = "chat2apiWindowObserverV90";
  const CLOSED_LIMIT = 80;
  const REPORT_DELAY_MS = 80;

  const state = {
    revision: 90,
    policy: "observe-only-single-route-authority-v90",
    nextWindowNo: 1,
    active: new Map(),
    closed: [],
    assignments: new Map(),
    loaded: false,
    reportTimer: null,
    reportInFlight: null,
    lastSignature: "",
  };
  globalThis[KEY] = state;
  globalThis.chat2apiWindowObserverV90 = state;
  globalThis.__CHAT2API_WINDOW_MANAGER_V88__ = state;
  globalThis.chat2apiWindowManagerV88 = state;

  function iso(ms) {
    if (!Number.isFinite(Number(ms)) || Number(ms) <= 0) return null;
    try { return new Date(Number(ms)).toISOString(); } catch (_) { return null; }
  }

  function isChatGpt(value = "") {
    try {
      return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname);
    } catch (_) { return false; }
  }

  function serializable(record) {
    return {
      window_no: Number(record?.window_no || 0),
      window_id: Number(record?.window_id),
      tab_id: Number.isInteger(record?.tab_id) ? record.tab_id : null,
      opened_at_ms: Number(record?.opened_at_ms || 0),
      opened_at: record?.opened_at || iso(record?.opened_at_ms),
      status: String(record?.status || "ready"),
      request_id: record?.request_id || null,
      route_key: record?.route_key || null,
      source: record?.source || "physical-observer-v90",
      ready_at_ms: Number(record?.ready_at_ms || 0),
      last_seen_at_ms: Number(record?.last_seen_at_ms || 0),
      closed_at_ms: Number(record?.closed_at_ms || 0),
      closed_at: record?.closed_at || null,
      screenshot_data_url: record?.screenshot_data_url || null,
      screenshot_at_ms: Number(record?.screenshot_at_ms || 0),
      screenshot_at: record?.screenshot_at || null,
      screenshot_error: record?.screenshot_error || null,
      close_reason: record?.close_reason || null,
    };
  }

  async function load() {
    if (state.loaded) return;
    state.loaded = true;
    const stored = await chrome.storage.local.get({ [STORAGE_KEY]: null }).catch(() => ({}));
    const value = stored?.[STORAGE_KEY];
    state.nextWindowNo = Math.max(1, Number(value?.next_window_no || 1));
    state.closed = Array.isArray(value?.closed) ? value.closed.slice(0, CLOSED_LIMIT) : [];
  }

  async function persist() {
    await load();
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        revision: 90,
        next_window_no: state.nextWindowNo,
        active: [...state.active.values()].map(serializable),
        closed: state.closed.slice(0, CLOSED_LIMIT).map(serializable),
        updated_at_ms: Date.now(),
      },
    }).catch(() => {});
  }

  function recordFor(windowId, tabId, source = {}) {
    if (!Number.isInteger(windowId)) return null;
    let record = state.active.get(windowId) || null;
    if (!record) {
      const opened = Number(source.opened_at_ms || Date.now());
      record = {
        window_no: state.nextWindowNo++,
        window_id: windowId,
        tab_id: Number.isInteger(tabId) ? tabId : null,
        opened_at_ms: opened,
        opened_at: iso(opened),
        status: source.status || "ready",
        request_id: source.request_id || null,
        route_key: source.route_key || null,
        source: source.source || "physical-observer-v90",
        ready_at_ms: Number(source.ready_at_ms || 0),
        last_seen_at_ms: Date.now(),
        screenshot_data_url: null,
        screenshot_at_ms: 0,
        screenshot_at: null,
        screenshot_error: null,
      };
      state.active.set(windowId, record);
    }
    if (Number.isInteger(tabId)) record.tab_id = tabId;
    if (source.source) record.source = source.source;
    record.status = source.status || record.status;
    record.request_id = source.request_id ?? record.request_id;
    record.route_key = source.route_key ?? record.route_key;
    record.last_seen_at_ms = Date.now();
    return record;
  }

  function markClosed(windowId, reason = "chrome-window-removed") {
    const record = state.active.get(Number(windowId));
    if (!record) return;
    state.active.delete(Number(windowId));
    const closedAt = Date.now();
    state.closed = [{
      ...serializable(record),
      status: "closed",
      closed_at_ms: closedAt,
      closed_at: iso(closedAt),
      close_reason: reason,
    }, ...state.closed.filter(row => Number(row.window_id) !== Number(windowId))].slice(0, CLOSED_LIMIT);
  }

  async function liveRoutes() {
    await load();
    const routes = globalThis[ROUTER_KEY]?.routes || {};
    const routeByWindow = new Map();
    for (const [key, route] of Object.entries(routes)) {
      if (Number.isInteger(route?.window_id)) routeByWindow.set(route.window_id, {key, route});
    }

    // The launcher creates one real ChatGPT browser window before any API request
    // has a route. v90 used to inspect only router-owned windows, so that initial
    // window was invisible until the first routed request. Observe every physical
    // ChatGPT window instead, then enrich it with route state when a route exists.
    // This remains strictly observation-only: no create/close/reroute operation is
    // performed here and conversation_routing.js stays the sole lifecycle owner.
    const physical = await chrome.windows.getAll({ populate: true }).catch(() => []);
    const seen = new Set();
    for (const win of physical) {
      const windowId = Number(win?.id);
      if (!Number.isInteger(windowId)) continue;
      const tab = (win.tabs || []).find(item => Number.isInteger(item?.id) && isChatGpt(item.url || item.pendingUrl || ""));
      if (!tab) continue;
      seen.add(windowId);
      const routed = routeByWindow.get(windowId);
      const route = routed?.route || null;
      recordFor(windowId, tab.id, {
        source: routed ? "route-observer-v90" : "physical-observer-v90",
        route_key: routed?.key || null,
        request_id: route?.inflight_request_id || null,
        status: route?.inflight_request_id ? "in_use" : "ready",
        opened_at_ms: Number(route?.window_opened_at_ms || route?.last_active_at || state.active.get(windowId)?.opened_at_ms || Date.now()),
      });
    }

    for (const windowId of [...state.active.keys()]) {
      if (!seen.has(windowId)) markClosed(windowId, "window-not-live-or-not-chatgpt");
    }
    await persist();
    return [...state.active.values()].map(serializable);
  }

  function snapshot() {
    return {
      revision: 90,
      policy: state.policy,
      decision_authority: false,
      speculative_windows: false,
      fifo_claims: 0,
      new_window_fallbacks: 0,
      active: [...state.active.values()].map(serializable).sort((a, b) => a.window_no - b.window_no),
      closed: state.closed.slice(0, CLOSED_LIMIT).map(serializable),
      updated_at_ms: Date.now(),
    };
  }

  async function report(force = false) {
    if (state.reportInFlight) return state.reportInFlight;
    state.reportInFlight = (async () => {
      await liveRoutes();
      const value = snapshot();
      const signature = JSON.stringify(value.active.map(row => [row.window_no, row.window_id, row.tab_id, row.status, row.request_id, row.route_key, row.source]));
      if (!force && signature === state.lastSignature) return value;
      state.lastSignature = signature;
      if (typeof trySendSocket === "function") {
        await trySendSocket({
          type: "extension.status",
          metadata: {
            window_manager_v88: value,
            window_manager_v90: value,
            window_manager_revision: 90,
            window_selection_policy: state.policy,
            window_decision_authority: "conversation-routing-v30",
          },
        }).catch(() => false);
      }
      return value;
    })().finally(() => { state.reportInFlight = null; });
    return state.reportInFlight;
  }

  function scheduleReport(delay = REPORT_DELAY_MS, force = false) {
    clearTimeout(state.reportTimer);
    state.reportTimer = setTimeout(() => {
      state.reportTimer = null;
      report(force).catch(() => {});
    }, Math.max(0, delay));
  }

  async function capture(windowId) {
    await liveRoutes();
    const record = state.active.get(Number(windowId));
    if (!record) throw new Error("Unknown active Worker route window");
    try {
      const dataUrl = await chrome.tabs.captureVisibleTab(Number(windowId), { format: "jpeg", quality: 55 });
      record.screenshot_data_url = String(dataUrl || "");
      record.screenshot_at_ms = Date.now();
      record.screenshot_at = iso(record.screenshot_at_ms);
      record.screenshot_error = null;
      await persist();
      scheduleReport(0, true);
      return { ok: true, window_id: Number(windowId), screenshot_at_ms: record.screenshot_at_ms };
    } catch (error) {
      record.screenshot_error = String(error?.message || error);
      await persist();
      throw error;
    }
  }

  state.reconcile = liveRoutes;
  state.snapshot = snapshot;
  state.report = report;
  state.capture = capture;

  const baseHandleServerMessage = globalThis.handleServerMessage;
  if (typeof baseHandleServerMessage === "function") {
    globalThis.handleServerMessage = async function handleWindowObserverControl(message) {
      if (message?.type !== "window.manager.capture") return baseHandleServerMessage(message);
      try {
        const data = await capture(Number(message.window_id));
        await trySendSocket?.({ type: "window.manager.result", control_id: String(message.control_id || ""), ok: true, data });
      } catch (error) {
        await trySendSocket?.({ type: "window.manager.result", control_id: String(message.control_id || ""), ok: false, error: String(error?.message || error) });
      }
      return undefined;
    };
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    const requestId = String(event.request_id || "");
    if (!requestId) return false;
    const routes = globalThis[ROUTER_KEY]?.routes || {};
    for (const [key, route] of Object.entries(routes)) {
      if (route?.inflight_request_id !== requestId && !state.assignments.has(requestId)) continue;
      const record = recordFor(route.window_id, route.tab_id, {
        source: "route-observer-v90",
        route_key: key,
        request_id: ["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(event.type) ? null : requestId,
        status: ["chat.completed", "image.completed"].includes(event.type) ? "ready" : ["chat.error", "chat.cancelled", "image.error", "image.cancelled"].includes(event.type) ? "ready" : "in_use",
      });
      if (record) state.assignments.set(requestId, { window_id: record.window_id, tab_id: record.tab_id, route_key: key });
      break;
    }
    if (["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(event.type)) {
      setTimeout(() => state.assignments.delete(requestId), 5000);
    }
    scheduleReport(0);
    return false;
  });

  chrome.windows.onRemoved.addListener(windowId => {
    markClosed(Number(windowId));
    persist().catch(() => {});
    scheduleReport(0, true);
  });
  chrome.windows.onCreated.addListener(() => scheduleReport(120, true));
  chrome.tabs.onUpdated.addListener((_tabId, changeInfo) => {
    if (changeInfo.url || changeInfo.status === "complete") scheduleReport(120, true);
  });

  load().then(() => report(true)).catch(() => {});
})();
