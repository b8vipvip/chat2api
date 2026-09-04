(() => {
  const KEY = "__CHAT2API_WINDOW_TRUTH_REFRESH_V89__";
  if (globalThis[KEY]) return;

  const state = {
    revision: 89,
    refreshInFlight: null,
  };
  globalThis[KEY] = state;

  async function refreshPhysicalTruth(reason = "server-refresh") {
    if (state.refreshInFlight) return state.refreshInFlight;
    state.refreshInFlight = (async () => {
      const physical = globalThis.__CHAT2API_WINDOW_TRUTH_V83__;
      const manager = globalThis.__CHAT2API_WINDOW_MANAGER_V88__;

      // Read Chrome's real window/tab graph first. v83 and v88 both use
      // chrome.windows.getAll({populate:true}); neither cached reserve slots nor
      // historical route state is allowed to establish that a window is live.
      if (typeof physical?.reconcile === "function") {
        await physical.reconcile().catch(() => null);
      }
      if (typeof manager?.reconcile === "function") {
        await manager.reconcile(true).catch(() => null);
      }
      // A reconciliation may have coalesced with another in-flight pass. Force
      // one final status report so the server receives a fresh updated_at_ms even
      // when the physical set did not otherwise change.
      if (typeof manager?.report === "function") {
        await manager.report(true).catch(() => null);
      }

      const snapshot = typeof manager?.snapshot === "function" ? manager.snapshot() : null;
      return {
        ok: Boolean(snapshot),
        revision: 89,
        reason: String(reason || "server-refresh"),
        updated_at_ms: Number(snapshot?.updated_at_ms || Date.now()),
        active_count: Array.isArray(snapshot?.active) ? snapshot.active.length : null,
      };
    })().finally(() => { state.refreshInFlight = null; });
    return state.refreshInFlight;
  }

  state.refresh = refreshPhysicalTruth;

  // Add a narrow, backwards-compatible control message after v88's capture
  // wrapper. Unknown messages continue to the previous handler unchanged.
  const baseHandleServerMessage = globalThis.handleServerMessage;
  if (typeof baseHandleServerMessage === "function") {
    globalThis.handleServerMessage = async function handleServerMessageWithTruthRefreshV89(message) {
      if (message?.type === "window.manager.refresh") {
        const result = await refreshPhysicalTruth(message?.reason || "server-refresh");
        if (typeof trySendSocket === "function") {
          await trySendSocket({
            type: "window.manager.refresh.result",
            control_id: message?.control_id || null,
            ...result,
          }).catch(() => false);
        }
        return;
      }
      return baseHandleServerMessage(message);
    };
  }

  // Repair persisted v88 state once when the service worker starts. This turns
  // windows that disappeared while the extension worker was asleep into closed
  // history before the first administrator query arrives.
  setTimeout(() => refreshPhysicalTruth("service-worker-start").catch(() => {}), 450);
})();
