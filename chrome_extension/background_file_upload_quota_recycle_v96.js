(() => {
  const KEY = "__CHAT2API_FILE_UPLOAD_QUOTA_RECYCLE_V96__";
  if (globalThis[KEY]) return;

  const MESSAGE_TYPE = "chat2api.multimodal.quota.v36";
  const REVISION = 96;
  const RECYCLE_DELAY_MS = 900;
  const RETAIN_MS = 120000;
  const state = {
    revision: REVISION,
    scheduled: new Map(),
    recycled: 0,
    ignored: 0,
    last: null,
  };

  function dispatchState() {
    return globalThis.__CHAT2API_CONVERSATION_DISPATCH_V1__ || null;
  }

  function routerState() {
    return globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__ || null;
  }

  function workersState() {
    return globalThis.__CHAT2API_CONVERSATION_WORKERS_V25__ || null;
  }

  function integer(value) {
    return Number.isInteger(value) ? value : null;
  }

  function targetForSender(sender) {
    const tabId = integer(sender?.tab?.id);
    const windowId = integer(sender?.tab?.windowId);
    if (!Number.isInteger(tabId)) return null;

    const requestTabs = dispatchState()?.requestTabs;
    if (!(requestTabs instanceof Map)) return null;

    const candidates = [];
    for (const [requestId, target] of requestTabs.entries()) {
      if (integer(target?.tabId) !== tabId) continue;
      if (Number.isInteger(windowId) && integer(target?.windowId) !== windowId) continue;
      candidates.push({
        requestId: String(requestId || ""),
        tabId,
        windowId: integer(target?.windowId) ?? windowId,
      });
    }
    if (!candidates.length) return null;

    // Prefer the router's authoritative inflight request when an older direct
    // background terminal path left a stale requestTabs entry behind.
    const routes = routerState()?.routes;
    if (routes && typeof routes === "object") {
      for (const route of Object.values(routes)) {
        if (integer(route?.tab_id) !== tabId) continue;
        if (Number.isInteger(windowId) && integer(route?.window_id) !== windowId) continue;
        const inflight = String(route?.inflight_request_id || "");
        const exact = inflight && candidates.find(item => item.requestId === inflight);
        if (exact) return exact;
      }
    }

    return candidates.length === 1 ? candidates[0] : null;
  }

  function currentTargetMatches(item) {
    const target = dispatchState()?.requestTabs?.get?.(item.requestId) || null;
    if (!target) return false;
    return integer(target.tabId) === item.tabId
      && (!Number.isInteger(item.windowId) || integer(target.windowId) === item.windowId);
  }

  async function emitDiagnostic(item, recycled, extra = {}) {
    if (typeof trySendSocket !== "function") return false;
    return trySendSocket({
      type: "chat.diagnostics",
      request_id: item.requestId,
      diagnostics: {
        file_upload_quota_terminal_recycle_v96: true,
        file_upload_quota_terminal_recycle_revision: REVISION,
        file_upload_quota_terminal_recycle_action: "close-routed-window-no-replay",
        file_upload_quota_terminal_recycled: Boolean(recycled),
        file_upload_quota_terminal_tab_id: item.tabId,
        file_upload_quota_terminal_window_id: item.windowId,
        ...extra,
      },
    }).catch(() => false);
  }

  async function recycle(item) {
    if (!item || !currentTargetMatches(item)) {
      state.ignored += 1;
      return false;
    }

    const recovery = globalThis.__CHAT2API_BACKGROUND_REQUEST_RECOVERY_V40__;
    if (typeof recovery?.recycleRequest !== "function") {
      state.ignored += 1;
      await emitDiagnostic(item, false, { file_upload_quota_terminal_recycle_error: "request-recovery-v40-unavailable" });
      return false;
    }

    const recycled = Boolean(await recovery.recycleRequest(
      item.requestId,
      "file-upload-quota-exhausted-v96",
    ).catch(() => false));

    // recycleRequest owns the route/window lifecycle. The per-key worker layer
    // normally learns terminal state from a local chat2api.event, but this quota
    // failure is caught in background.js and sent directly to the server. Release
    // that logical reservation explicitly so the closed window cannot remain
    // reported as an in-use Worker.
    try { workersState()?.releaseRequest?.(item.requestId); } catch (_) {}

    if (recycled) state.recycled += 1;
    else state.ignored += 1;
    await emitDiagnostic(item, recycled);
    return recycled;
  }

  function schedule(sender, data = {}) {
    const item = targetForSender(sender);
    if (!item?.requestId) {
      state.ignored += 1;
      return false;
    }
    if (state.scheduled.has(item.requestId)) return true;

    const scheduled = {
      ...item,
      detectedAtMs: Number(data?.detected_at_ms || Date.now()),
      recoveryAtMs: Number(data?.recovery_at_ms || 0),
      sourceText: String(data?.source_text || "").replace(/\s+/g, " ").trim().slice(0, 320),
      scheduledAtMs: Date.now(),
    };
    state.scheduled.set(item.requestId, scheduled);
    state.last = { ...scheduled, action: "scheduled" };

    setTimeout(async () => {
      const current = state.scheduled.get(item.requestId);
      if (!current) return;
      try {
        const recycled = await recycle(current);
        state.last = { ...current, action: recycled ? "recycled" : "ignored", completedAtMs: Date.now() };
      } finally {
        setTimeout(() => state.scheduled.delete(item.requestId), RETAIN_MS);
      }
    }, RECYCLE_DELAY_MS);
    return true;
  }

  const listener = (message, sender) => {
    if (message?.type !== MESSAGE_TYPE) return false;
    schedule(sender, message.data || {});
    // The existing quota controller owns the response to this message. This
    // lifecycle observer is deliberately passive so it cannot race sendResponse.
    return false;
  };

  chrome.runtime.onMessage.addListener(listener);
  globalThis[KEY] = {
    revision: REVISION,
    state,
    listener,
    targetForSender,
    currentTargetMatches,
    schedule,
    recycle,
  };
})();
