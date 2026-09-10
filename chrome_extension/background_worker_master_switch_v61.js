(() => {
  const KEY = "__CHAT2API_WORKER_MASTER_SWITCH_V61__";
  if (globalThis[KEY]) return;

  const CONTROL_KEY = "__CHAT2API_CAPACITY_CONTROL_V35__";
  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const DISPATCH_KEY = "__CHAT2API_CONVERSATION_DISPATCH_V1__";
  const OBSERVER_KEY = "__CHAT2API_WINDOW_OBSERVER_V90__";
  const DISABLED_STORAGE_KEY = "chat2apiWorkerMasterDisabledV61";
  const AWAIT_DISCONNECT_KEY = "chat2apiWorkerMasterAwaitDisconnectV62";
  const state = { version: 61, revision: 90, installed: false, lastResult: null };
  globalThis[KEY] = state;

  function activeRequestLease() {
    const requestTabs = globalThis[DISPATCH_KEY]?.requestTabs;
    if (!(requestTabs instanceof Map) || requestTabs.size <= 0) return { count: 0, ids: [] };
    return { count: requestTabs.size, ids: [...requestTabs.keys()].map(String).slice(0, 20) };
  }

  async function markDisabling() {
    await chrome.storage.local.set({
      [DISABLED_STORAGE_KEY]: true,
      [AWAIT_DISCONNECT_KEY]: true,
      socketState: "disconnecting",
      chat2apiWorkerMasterSwitchV61: {
        enabled: false,
        phase: "retiring-routes",
        revision: 90,
        route_window_authority: "conversation-routing-v30",
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
        revision: 90,
        error: String(error || ""),
        observed_at_ms: Date.now(),
      },
    }).catch(() => {});
  }

  async function routeSnapshot() {
    const observer = globalThis[OBSERVER_KEY];
    if (typeof observer?.report === "function") await observer.report(true).catch(() => {});
    return typeof observer?.snapshot === "function" ? observer.snapshot() : {};
  }

  async function retireRoutesForDisable(keepCount = 1) {
    const router = globalThis[ROUTER_KEY];
    if (!router?.routes || typeof router?.retireRoute !== "function") {
      throw new Error("Conversation route authority v30 is not ready");
    }
    const entries = Object.entries(router.routes)
      .filter(([, route]) => Number.isInteger(route?.window_id))
      .sort((a, b) => Number(b[1]?.last_active_at || 0) - Number(a[1]?.last_active_at || 0));
    const keep = Math.max(1, Math.floor(Number(keepCount || 1)));
    const kept = entries.slice(0, keep);
    const retire = entries.slice(keep);

    await markDisabling();
    const retiredKeys = [];
    for (const [key, route] of retire) {
      const ok = await router.retireRoute(key, route, "", "worker-disable-v61-r90").catch(() => false);
      if (ok) retiredKeys.push(key);
    }
    const snapshot = await routeSnapshot();
    await chrome.storage.local.set({
      [DISABLED_STORAGE_KEY]: true,
      [AWAIT_DISCONNECT_KEY]: true,
      chat2apiWorkerMasterSwitchV61: {
        enabled: false,
        phase: "routes-retired",
        revision: 90,
        kept_route_keys: kept.map(([key]) => key),
        retired_route_keys: retiredKeys,
        route_window_authority: "conversation-routing-v30",
        observed_at_ms: Date.now(),
      },
    }).catch(() => {});
    return {
      keep_windows: keep,
      kept_route_keys: kept.map(([key]) => key),
      retired_route_keys: retiredKeys,
      managed_windows_before: entries.length,
      managed_windows_after: kept.length,
      window_snapshot: snapshot,
      master_enabled: false,
      route_window_authority: "conversation-routing-v30",
    };
  }

  async function emitResult(message, ok, data = {}, error = "") {
    const result = {
      version: 61,
      revision: 90,
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
        extension_control_transport: "worker-master-switch-v61-r90",
        extension_control_result: result,
        worker_master_switch_version: 61,
        worker_master_switch_revision: 90,
        active_request_disable_lease_revision: 79,
        worker_master_enabled: !(result.action === "worker.disable" && result.ok),
        window_decision_authority: "conversation-routing-v30",
      },
    });
    if (!sent) throw new Error("Failed to send Worker master-switch confirmation");
    return result;
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
      const data = await retireRoutesForDisable(keep);
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
      const text = String(error?.message || error);
      await restoreAfterFailure(text);
      return emitResult(message, false, {}, text);
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
      chrome.storage.local.get({ [DISABLED_STORAGE_KEY]: false }).then(stored => {
        if (!stored[DISABLED_STORAGE_KEY]) return;
        return chrome.storage.local.set({
          [AWAIT_DISCONNECT_KEY]: false,
          chat2apiWorkerMasterSwitchV61: { enabled: false, phase: "disabled", revision: 90, disconnected_at_ms: Date.now() },
        });
      }).catch(() => {});
      return;
    }
    if (nextState === "connected") {
      chrome.storage.local.get({ [DISABLED_STORAGE_KEY]: false, [AWAIT_DISCONNECT_KEY]: false }).then(stored => {
        if (stored[DISABLED_STORAGE_KEY] && stored[AWAIT_DISCONNECT_KEY] && previousState !== "disconnected") return;
        return chrome.storage.local.set({
          [DISABLED_STORAGE_KEY]: false,
          [AWAIT_DISCONNECT_KEY]: false,
          chat2apiWorkerMasterSwitchV61: { enabled: true, phase: "connected", revision: 90, resumed_at_ms: Date.now() },
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
