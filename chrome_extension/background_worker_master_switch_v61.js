(() => {
  const KEY = "__CHAT2API_WORKER_MASTER_SWITCH_V61__";
  if (globalThis[KEY]) return;

  const CONTROL_KEY = "__CHAT2API_CAPACITY_CONTROL_V35__";
  const RESERVE_KEY = "__CHAT2API_RESERVE_POOL_V29__";
  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const DISPATCH_KEY = "__CHAT2API_CONVERSATION_DISPATCH_V1__";
  const WARM_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";
  const DISABLED_STORAGE_KEY = "chat2apiWorkerMasterDisabledV61";
  const AWAIT_DISCONNECT_KEY = "chat2apiWorkerMasterAwaitDisconnectV62";
  const state = {
    version: 61,
    revision: 62,
    installed: false,
    lastResult: null,
  };
  globalThis[KEY] = state;

  function isChatGpt(value = "") {
    try {
      return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname);
    } catch (_) {
      return false;
    }
  }

  async function liveWindowIds() {
    const result = new Set();
    try {
      const windows = await chrome.windows.getAll({ populate: true });
      for (const win of windows || []) {
        if (!Number.isInteger(win?.id)) continue;
        if ((win.tabs || []).some(tab => isChatGpt(tab?.url || tab?.pendingUrl || ""))) result.add(win.id);
      }
    } catch (_) {}
    return result;
  }

  async function managedWindowIds() {
    const ids = new Set();
    const reserve = globalThis[RESERVE_KEY];
    for (const slot of reserve?.reserveSlots?.values?.() || []) {
      if (Number.isInteger(slot?.window_id)) ids.add(slot.window_id);
    }

    const warm = globalThis[WARM_KEY];
    for (const slot of warm?.warmSlots?.values?.() || []) {
      if (Number.isInteger(slot?.window_id)) ids.add(slot.window_id);
    }
    for (const slot of warm?.openingSlots?.values?.() || []) {
      if (Number.isInteger(slot?.window_id)) ids.add(slot.window_id);
    }

    const router = globalThis[ROUTER_KEY];
    for (const route of Object.values(router?.routes || {})) {
      if (route?.window_owned !== false && Number.isInteger(route?.window_id)) ids.add(route.window_id);
    }
    if (router?.activeRequests instanceof Map) {
      for (const request of router.activeRequests.values()) {
        if (Number.isInteger(request?.window_id)) ids.add(request.window_id);
      }
    }

    const stored = await chrome.storage.local.get({
      chatgptExternalWarmWindowIdV28: null,
      boundTabId: null,
    }).catch(() => ({}));
    if (Number.isInteger(stored.chatgptExternalWarmWindowIdV28)) ids.add(stored.chatgptExternalWarmWindowIdV28);
    if (Number.isInteger(stored.boundTabId)) {
      try {
        const tab = await chrome.tabs.get(stored.boundTabId);
        if (isChatGpt(tab?.url || tab?.pendingUrl || "") && Number.isInteger(tab?.windowId)) ids.add(tab.windowId);
      } catch (_) {}
    }
    return ids;
  }

  async function chooseKeepWindow(ids, live) {
    const stored = await chrome.storage.local.get({
      chatgptExternalWarmWindowIdV28: null,
      boundTabId: null,
    }).catch(() => ({}));
    const bootstrap = Number(stored.chatgptExternalWarmWindowIdV28);
    if (Number.isInteger(bootstrap) && ids.has(bootstrap) && live.has(bootstrap)) return bootstrap;
    const boundTab = Number(stored.boundTabId);
    if (Number.isInteger(boundTab)) {
      try {
        const tab = await chrome.tabs.get(boundTab);
        if (Number.isInteger(tab?.windowId) && ids.has(tab.windowId) && live.has(tab.windowId)) return tab.windowId;
      } catch (_) {}
    }
    for (const id of ids) if (live.has(id)) return id;
    return null;
  }

  async function markDisabling() {
    await chrome.storage.local.set({
      [DISABLED_STORAGE_KEY]: true,
      [AWAIT_DISCONNECT_KEY]: true,
      socketState: "disconnecting",
      chat2apiWorkerMasterSwitchV61: {
        enabled: false,
        phase: "collapsing-windows",
        revision: 62,
        observed_at_ms: Date.now(),
      },
    }).catch(() => {});
  }

  async function restoreAfterFailure(error = "") {
    const connected = typeof socketReady === "function" && socketReady();
    await chrome.storage.local.set({
      [DISABLED_STORAGE_KEY]: false,
      [AWAIT_DISCONNECT_KEY]: false,
      socketState: connected ? "connected" : "disconnected",
      chat2apiWorkerMasterSwitchV61: {
        enabled: connected,
        phase: "disable-failed",
        revision: 62,
        error: String(error || ""),
        observed_at_ms: Date.now(),
      },
    }).catch(() => {});
  }

  async function collapseManagedWindows(keepCount = 1) {
    const ids = await managedWindowIds();
    const live = await liveWindowIds();
    const managedLive = [...ids].filter(id => live.has(id));
    if (keepCount <= 0) keepCount = 1;

    const keep = await chooseKeepWindow(ids, live);
    const keepIds = new Set();
    if (Number.isInteger(keep)) keepIds.add(keep);
    for (const id of managedLive) {
      if (keepIds.size >= keepCount) break;
      keepIds.add(id);
    }

    // Stop Reserve Pool eligibility before removing any windows. Otherwise its
    // short reconcile timer can recreate a spare during the disable handshake.
    await markDisabling();

    const closed = [];
    for (const id of managedLive) {
      if (keepIds.has(id)) continue;
      try {
        await chrome.windows.remove(id);
        closed.push(id);
      } catch (_) {}
    }

    await chrome.storage.local.set({
      [DISABLED_STORAGE_KEY]: true,
      [AWAIT_DISCONNECT_KEY]: true,
      chat2apiWorkerMasterSwitchV61: {
        enabled: false,
        phase: "collapsed",
        revision: 62,
        keep_windows: keepCount,
        kept_window_ids: [...keepIds],
        closed_window_ids: closed,
        observed_at_ms: Date.now(),
      },
    }).catch(() => {});

    await new Promise(resolve => setTimeout(resolve, 80));
    let snapshot = null;
    try {
      const ctl = globalThis[CONTROL_KEY];
      if (typeof ctl?.snapshot === "function") snapshot = await ctl.snapshot();
    } catch (_) {}
    return {
      keep_windows: keepCount,
      kept_window_ids: [...keepIds],
      closed_window_ids: closed,
      managed_windows_before: managedLive.length,
      managed_windows_after: keepIds.size,
      window_snapshot: snapshot || {},
      master_enabled: false,
    };
  }

  async function emitResult(message, ok, data = {}, error = "") {
    const result = {
      version: 61,
      revision: 62,
      control_id: String(message?.control_id || ""),
      action: String(message?.action || ""),
      ok: Boolean(ok),
      data: data && typeof data === "object" ? data : {},
      error: String(error || ""),
      observed_at: new Date().toISOString(),
    };
    state.lastResult = result;
    if (typeof trySendSocket !== "function") throw new Error("Extension WebSocket sender is unavailable");
    const sent = await trySendSocket({
      type: "extension.control.result",
      control_id: result.control_id,
      action: result.action,
      ok: result.ok,
      data: result.data,
      error: result.error,
      metadata: {
        extension_control_version: 36,
        extension_control_ready: true,
        extension_control_transport: "worker-master-switch-v61-r62",
        extension_control_result: result,
        worker_master_switch_version: 61,
        worker_master_switch_revision: 62,
        active_request_disable_lease_revision: 79,
        worker_master_enabled: !(result.action === "worker.disable" && result.ok),
      },
    });
    if (!sent) throw new Error("Failed to send Worker master-switch confirmation");
    return result;
  }

  function activeRequestLease() {
    const dispatch = globalThis[DISPATCH_KEY];
    const requestTabs = dispatch?.requestTabs;
    if (!(requestTabs instanceof Map) || requestTabs.size <= 0) return { count: 0, ids: [] };
    return { count: requestTabs.size, ids: [...requestTabs.keys()].map(String).slice(0, 20) };
  }

  async function handleDisable(message) {
    try {
      const lease = activeRequestLease();
      if (lease.count > 0) {
        return emitResult(message, false, {
          blocked: true,
          retryable: true,
          active_request_count: lease.count,
          active_request_ids: lease.ids,
          lease_revision: 79,
        }, "Worker has active requests; disable is blocked until terminal completion");
      }
      const keep = Math.max(1, Math.floor(Number(message?.payload?.keep_windows || 1)));
      const data = await collapseManagedWindows(keep);
      const result = await emitResult(message, true, data);
      setTimeout(() => {
        try {
          if (typeof socket !== "undefined" && socket && socket.readyState <= 1) {
            socket.close(4003, "Worker disabled by administrator");
          }
        } catch (_) {}
      }, 180);
      return result;
    } catch (error) {
      const messageText = String(error?.message || error);
      await restoreAfterFailure(messageText);
      return emitResult(message, false, {}, messageText);
    }
  }

  function install() {
    const ctl = globalThis[CONTROL_KEY];
    if (!ctl || typeof ctl.handle !== "function" || ctl.handle.__chat2apiWorkerMasterSwitchV61) return false;
    const baseHandle = ctl.handle;
    const wrapped = async message => {
      if (String(message?.action || "") === "worker.disable") return handleDisable(message);
      return baseHandle(message);
    };
    wrapped.__chat2apiWorkerMasterSwitchV61 = true;
    ctl.handle = wrapped;
    state.installed = true;
    return true;
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local" || !changes.socketState) return;
    const nextState = String(changes.socketState.newValue || "");
    const previousState = String(changes.socketState.oldValue || "");

    if (nextState === "disconnected") {
      chrome.storage.local.get({
        [DISABLED_STORAGE_KEY]: false,
        [AWAIT_DISCONNECT_KEY]: false,
      }).then(stored => {
        if (!stored[DISABLED_STORAGE_KEY]) return;
        return chrome.storage.local.set({
          [DISABLED_STORAGE_KEY]: true,
          [AWAIT_DISCONNECT_KEY]: false,
          chat2apiWorkerMasterSwitchV61: {
            enabled: false,
            phase: "disabled",
            revision: 62,
            disconnected_at_ms: Date.now(),
          },
        });
      }).catch(() => {});
      return;
    }

    if (nextState === "connected") {
      chrome.storage.local.get({
        [DISABLED_STORAGE_KEY]: false,
        [AWAIT_DISCONNECT_KEY]: false,
      }).then(stored => {
        // During worker.disable the existing socket may briefly publish another
        // connected heartbeat before its close frame wins the race. Never let
        // that transient state erase the administrator's disabled flag. A real
        // re-enable has to pass through a full disconnected state first.
        if (stored[DISABLED_STORAGE_KEY] && stored[AWAIT_DISCONNECT_KEY] && previousState !== "disconnected") {
          return;
        }
        return chrome.storage.local.set({
          [DISABLED_STORAGE_KEY]: false,
          [AWAIT_DISCONNECT_KEY]: false,
          chat2apiWorkerMasterSwitchV61: {
            enabled: true,
            phase: "connected",
            revision: 62,
            resumed_at_ms: Date.now(),
          },
        });
      }).catch(() => {});
    }
  });

  if (!install()) {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (install() || attempts >= 30) clearInterval(timer);
    }, 100);
  }
})();
