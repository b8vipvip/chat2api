(() => {
  const KEY = "__CHAT2API_WORKER_PRESENTATION_V66__";
  if (globalThis[KEY]) return;

  // v0.22.102: Worker table presentation has a single structural owner:
  // admin_extension_columns.js. Historical v66 remains as a compatibility
  // facade for code that may call refresh(), but it never mutates table rows,
  // wraps show(), starts timers, or performs a second /extensions fetch.
  const state = {
    version: 66,
    retired_renderer: true,
    structural_owner: "admin_extension_columns-v152",
    delegated_to: "admin_extension_columns-v152",
  };

  function liveWindowTruth(payload) {
    const authoritative = Number(payload?.truth_revision || 0) >= 89;
    const result = new Map();
    for (const worker of Array.isArray(payload?.workers) ? payload.workers : []) {
      const clientId = String(worker?.client_id || "");
      if (!clientId) continue;
      const raw = worker?.standby_window_count;
      const countKnown = raw !== null && raw !== undefined
        && raw !== "" && Number.isFinite(Number(raw)) && Number(raw) >= 0;
      const status = String(worker?.truth_status || (authoritative ? "unverified" : "legacy"));
      const liveVerified = authoritative && worker?.live_verified === true
        && worker?.chatgpt_routing_ready === true && worker?.online === true
        && status === "verified" && countKnown;
      result.set(clientId, {
        authoritative,
        liveVerified,
        status,
        standby: liveVerified ? Math.max(0, Number(raw)) : null,
      });
    }
    return {authoritative, byClient:result};
  }

  async function refresh() {
    const owner = globalThis.__CHAT2API_CANONICAL_WORKER_LIST_V59__;
    if (typeof owner?.reload === "function") return owner.reload(true);
    const reload = globalThis.chat2apiReloadCanonicalWorkerListV59;
    if (typeof reload === "function") return reload();
    return null;
  }

  state.refresh = refresh;
  state.liveWindowTruth = liveWindowTruth;
  globalThis[KEY] = Object.freeze(state);
  document.documentElement.dataset.chat2apiWorkerPresentationRevision = "66-retired-by-v152";
})();