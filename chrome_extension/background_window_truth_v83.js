(() => {
  const KEY = "__CHAT2API_WINDOW_TRUTH_V83__";
  if (globalThis[KEY]) return;

  const RESERVE_KEY = "__CHAT2API_RESERVE_POOL_V29__";
  const SUPERVISOR_KEY = "__CHAT2API_TAB_SUPERVISOR_V32__";
  const WARM_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";
  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const INIT_TAB_KEY = "chat2apiInitializationTabIdV32";
  const INIT_WINDOW_KEY = "chat2apiInitializationWindowIdV32";
  const EXTERNAL_WARM_WINDOW_KEY = "chatgptExternalWarmWindowIdV28";
  const REVISION = 83;

  const state = {
    timer: null,
    inFlight: null,
    lastSignature: "",
    lastSnapshot: null,
  };
  globalThis[KEY] = state;

  function isChatGpt(value = "") {
    if (typeof isChatGptUrl === "function") return isChatGptUrl(value);
    try {
      return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname);
    } catch (_) {
      return false;
    }
  }

  async function liveChatGptWindowIds() {
    const windows = await chrome.windows.getAll({ populate: true }).catch(() => []);
    const ids = new Set();
    for (const win of windows || []) {
      if (!Number.isInteger(win?.id)) continue;
      if ((win.tabs || []).some(tab => isChatGpt(tab?.url || tab?.pendingUrl || ""))) ids.add(win.id);
    }
    return ids;
  }

  async function liveWindow(windowId) {
    if (!Number.isInteger(windowId)) return null;
    try { return await chrome.windows.get(windowId, { populate: true }); }
    catch (_) { return null; }
  }

  function candidateWorkerWindows(initWindowId) {
    const preferred = [];
    const seen = new Set();
    const add = (windowId, priority) => {
      if (!Number.isInteger(windowId) || windowId === initWindowId || seen.has(windowId)) return;
      seen.add(windowId);
      preferred.push({ window_id: windowId, priority });
    };

    const reserve = globalThis[RESERVE_KEY];
    for (const slot of reserve?.reserveSlots?.values?.() || []) add(slot?.window_id, 10);

    const warm = globalThis[WARM_KEY];
    for (const slot of warm?.warmSlots?.values?.() || []) add(slot?.window_id, 20);

    const router = globalThis[ROUTER_KEY];
    for (const route of Object.values(router?.routes || {})) {
      add(route?.window_id, route?.inflight_request_id ? 50 : 30);
    }

    return preferred.sort((a, b) => a.priority - b.priority).map(row => row.window_id);
  }

  async function compactInitializationWindow() {
    const stored = await chrome.storage.local.get({
      [INIT_TAB_KEY]: null,
      [INIT_WINDOW_KEY]: null,
      [EXTERNAL_WARM_WINDOW_KEY]: null,
    }).catch(() => ({}));
    const initTabId = stored[INIT_TAB_KEY];
    if (!Number.isInteger(initTabId)) return { compacted: false, reason: "no-init-tab" };

    let initTab = null;
    try { initTab = await chrome.tabs.get(initTabId); } catch (_) {}
    if (!initTab || !isChatGpt(initTab.url || initTab.pendingUrl || "")) {
      return { compacted: false, reason: "init-tab-not-live" };
    }

    const initWindowId = initTab.windowId;
    let candidates = candidateWorkerWindows(initWindowId);
    if (Number.isInteger(stored[EXTERNAL_WARM_WINDOW_KEY]) && stored[EXTERNAL_WARM_WINDOW_KEY] !== initWindowId) {
      candidates.push(stored[EXTERNAL_WARM_WINDOW_KEY]);
    }
    candidates = [...new Set(candidates)];

    let targetWindowId = null;
    for (const windowId of candidates) {
      const win = await liveWindow(windowId);
      if (!win) continue;
      if ((win.tabs || []).some(tab => isChatGpt(tab?.url || tab?.pendingUrl || ""))) {
        targetWindowId = windowId;
        break;
      }
    }
    if (!Number.isInteger(targetWindowId)) return { compacted: false, reason: "no-worker-window" };
    if (targetWindowId === initWindowId) return { compacted: false, reason: "already-shared" };

    try {
      const moved = await chrome.tabs.move(initTabId, { windowId: targetWindowId, index: -1 });
      const movedTab = Array.isArray(moved) ? moved[0] : moved;
      if (!movedTab || movedTab.windowId !== targetWindowId) throw new Error("Chrome did not move initialization tab into Worker window");
      await chrome.tabs.update(initTabId, { active: false }).catch(() => {});
      await chrome.storage.local.set({
        [INIT_WINDOW_KEY]: targetWindowId,
        chat2apiInitializationCompactionVersionV83: REVISION,
        chat2apiInitializationCompactedAtV83: Date.now(),
        chat2apiInitializationCompactedFromWindowIdV83: initWindowId,
        chat2apiInitializationCompactedToWindowIdV83: targetWindowId,
        chat2apiInitializationCompactionErrorV83: "",
      }).catch(() => {});
      return { compacted: true, from_window_id: initWindowId, to_window_id: targetWindowId };
    } catch (error) {
      await chrome.storage.local.set({
        chat2apiInitializationCompactionVersionV83: REVISION,
        chat2apiInitializationCompactionErrorV83: String(error?.message || error),
        chat2apiInitializationCompactionErrorAtV83: Date.now(),
      }).catch(() => {});
      return { compacted: false, reason: "move-failed", error: String(error?.message || error) };
    }
  }

  async function physicalSnapshot() {
    const reserve = globalThis[RESERVE_KEY];
    const managed = typeof reserve?.snapshot === "function" ? await reserve.snapshot().catch(() => null) : null;
    const live = await liveChatGptWindowIds();
    const supervisor = globalThis[SUPERVISOR_KEY];
    const supervised = typeof supervisor?.snapshot === "function" ? supervisor.snapshot() : null;
    return {
      revision: REVISION,
      total: Number(managed?.total || 0),
      active: Number(managed?.active || 0),
      idle: Number(managed?.idle || 0),
      target: Number(managed?.target || reserve?.target || 0),
      own: Number(managed?.own || 0),
      warm: Number(managed?.warm || 0),
      routed: Number(managed?.routed || 0),
      all_chatgpt_windows: live.size,
      initialization_window_shared: Number.isInteger(supervised?.initialization_tab_id)
        ? Number(supervised?.chatgpt_tabs_seen || 0) > live.size
        : null,
      observed_at_ms: Date.now(),
    };
  }

  async function reportPhysical(force = false) {
    const snapshot = await physicalSnapshot();
    const signature = JSON.stringify([
      snapshot.total,
      snapshot.active,
      snapshot.target,
      snapshot.own,
      snapshot.warm,
      snapshot.routed,
      snapshot.all_chatgpt_windows,
    ]);
    state.lastSnapshot = snapshot;
    if (!force && signature === state.lastSignature) return snapshot;
    state.lastSignature = signature;
    if (typeof trySendSocket === "function") {
      await trySendSocket({
        type: "extension.status",
        metadata: {
          reserve_window_total: snapshot.total,
          reserve_window_active: snapshot.active,
          reserve_window_idle: snapshot.idle,
          reserve_window_target: snapshot.target,
          reserve_window_own_spare: snapshot.own,
          reserve_window_warm_spare: snapshot.warm,
          reserve_window_routed: snapshot.routed,
          reserve_window_all_chatgpt_windows: snapshot.all_chatgpt_windows,
          reserve_window_physical_telemetry_version: REVISION,
          reserve_window_physical_updated_at: snapshot.observed_at_ms,
        },
      }).catch(() => false);
    }
    return snapshot;
  }

  async function reconcileTruth() {
    if (state.inFlight) return state.inFlight;
    state.inFlight = (async () => {
      await compactInitializationWindow();
      await reportPhysical(true);
      return state.lastSnapshot;
    })().finally(() => { state.inFlight = null; });
    return state.inFlight;
  }

  function schedule(delayMs = 250) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      state.timer = null;
      reconcileTruth().catch(() => {});
    }, Math.max(0, Number(delayMs || 0)));
  }

  const reserve = globalThis[RESERVE_KEY];
  if (reserve && typeof reserve.report === "function" && !reserve.report.__chat2apiWindowTruthV83) {
    const baseReport = reserve.report.bind(reserve);
    const wrapped = async force => {
      const result = await baseReport(force);
      await compactInitializationWindow().catch(() => null);
      await reportPhysical(Boolean(force)).catch(() => null);
      return result;
    };
    wrapped.__chat2apiWindowTruthV83 = true;
    reserve.report = wrapped;
  }

  state.snapshot = physicalSnapshot;
  state.report = reportPhysical;
  state.compactInitializationWindow = compactInitializationWindow;
  state.reconcile = reconcileTruth;

  chrome.tabs.onCreated.addListener(() => schedule(450));
  chrome.tabs.onUpdated.addListener((_tabId, changeInfo) => {
    if (changeInfo.url || changeInfo.status === "complete") schedule(450);
  });
  chrome.tabs.onRemoved.addListener(() => schedule(350));
  chrome.windows.onRemoved.addListener(() => schedule(350));
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (
      changes[INIT_TAB_KEY]
      || changes[INIT_WINDOW_KEY]
      || changes.chat2apiReservePoolV29
      || changes.chat2apiConversationWarmPoolV2
      || changes.chat2apiConversationRoutesV1
      || changes[EXTERNAL_WARM_WINDOW_KEY]
      || changes.socketState
    ) schedule(300);
  });

  setTimeout(() => schedule(0), 650);
})();
