(() => {
  const KEY = "__CHAT2API_WINDOW_AUTHORITY_V133__";
  if (globalThis[KEY]) return;

  const state = {
    version: 133,
    revision: 133,
    authority: "persistent-window-pool-v132",
    route_authority: "conversation-routing-v30+persistent-pool-v132",
    patched_status_reports: 0,
  };
  globalThis[KEY] = state;

  // v90 is intentionally observation-only, but its historical status payload
  // still labelled conversation-routing-v30 as the physical lifecycle owner.
  // Keep v90 as the source of physical truth while correcting only the authority
  // metadata emitted with its reports.
  if (typeof trySendSocket === "function" && !trySendSocket.__chat2apiWindowAuthorityV133) {
    const baseTrySendSocket = trySendSocket;
    const wrapped = async payload => {
      const metadata = payload?.metadata;
      const isWindowReport = payload?.type === "extension.status" && metadata && (
        metadata.window_manager_v90 || metadata.window_manager_v88 || Number(metadata.window_manager_revision || 0) === 90
      );
      if (isWindowReport) {
        state.patched_status_reports += 1;
        payload = {
          ...payload,
          metadata: {
            ...metadata,
            window_decision_authority: state.authority,
            route_window_authority: state.route_authority,
            persistent_window_pool_revision: 132,
            persistent_window_pool_policy: "persistent-prewarmed-total-window-pool-v132",
            prewarmed_windows: true,
            speculative_windows: false,
          },
        };
      }
      return baseTrySendSocket(payload);
    };
    wrapped.__chat2apiWindowAuthorityV133 = true;
    trySendSocket = wrapped;
  }
})();
