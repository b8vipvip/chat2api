(() => {
  const KEY = "__CHAT2API_WINDOW_REFRESH_V129__";
  if (globalThis[KEY]) return;

  const state = {
    revision: 129,
    policy: "physical-window-refresh-bridge-v129",
    refresh_count: 0,
    last_refresh_at_ms: 0,
    last_error: null,
  };
  globalThis[KEY] = state;

  const baseHandleServerMessage = globalThis.handleServerMessage;
  if (typeof baseHandleServerMessage !== "function") return;

  async function sendResult(message, payload) {
    if (typeof globalThis.trySendSocket !== "function") return false;
    return globalThis.trySendSocket({
      type: "window.manager.refresh.result",
      request_id: String(message?.request_id || ""),
      control_id: String(message?.control_id || ""),
      ...payload,
    }).catch(() => false);
  }

  globalThis.handleServerMessage = async function handleWindowManagerRefreshV129(message) {
    if (message?.type !== "window.manager.refresh") {
      return baseHandleServerMessage(message);
    }

    const observer = globalThis.__CHAT2API_WINDOW_OBSERVER_V90__;
    try {
      if (!observer || typeof observer.report !== "function") {
        throw new Error("window-observer-v90-unavailable");
      }

      // A normal observer report may already be in flight when the server asks
      // for a physical verification. Wait for it to settle, then force a new
      // report so window_manager_v88.updated_at_ms is guaranteed to advance.
      if (observer.reportInFlight && typeof observer.reportInFlight.then === "function") {
        await observer.reportInFlight.catch(() => {});
      }
      const snapshot = await observer.report(true);
      state.refresh_count += 1;
      state.last_refresh_at_ms = Date.now();
      state.last_error = null;

      await sendResult(message, {
        ok: true,
        data: {
          revision: 129,
          observer_revision: Number(snapshot?.revision || observer.revision || 90),
          active_count: Array.isArray(snapshot?.active) ? snapshot.active.length : 0,
          updated_at_ms: Number(snapshot?.updated_at_ms || state.last_refresh_at_ms),
        },
      });
    } catch (error) {
      state.last_error = String(error?.message || error || "window-refresh-failed");
      await sendResult(message, { ok: false, error: state.last_error });
    }
    return undefined;
  };
})();
