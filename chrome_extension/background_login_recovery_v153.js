(() => {
  "use strict";
  const KEY = "__CHAT2API_LOGIN_RECOVERY_V153__";
  if (globalThis[KEY]) return;
  const state = {inFlight:false};
  globalThis[KEY] = state;

  const previous = handleServerMessage;
  handleServerMessage = async message => {
    if (message?.type !== "worker.login.open.v153") return previous(message);
    if (state.inFlight) return;
    state.inFlight = true;
    try {
      // Reuse the existing per-Worker login probe. Never receive, cache, or
      // autofill third-party account passwords or authenticator secrets.
      await globalThis.__CHAT2API_LOGIN_READINESS_V27__?.openLoginWindow?.();
    } finally {
      state.inFlight = false;
    }
  };
})();
