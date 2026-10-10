(() => {
  "use strict";
  const KEY = "__CHAT2API_LOGIN_RECOVERY_V153__";
  if (globalThis[KEY]) return;
  const state = { inFlight: false };
  globalThis[KEY] = state;

  async function report(attemptId, status) {
    if (!/^[A-Za-z0-9_-]{12,64}$/.test(String(attemptId || ""))) return;
    // Only status and a correlation ID cross the Worker transport.
    // Credentials, TOTP seeds, and OTP codes are not reported.
    await trySendSocket({
      type: "extension.status",
      metadata: {
        worker_login_attempt_id: attemptId,
        worker_login_recovery_state: status,
      },
    });
  }

  const previous = handleServerMessage;
  handleServerMessage = async message => {
    if (message?.type === "worker.lifecycle.initialize.v156") {
      // A service-worker reload reconnects the authenticated Chrome Bridge;
      // it does not clear device pairing, browser history or login cookies.
      chrome.runtime.reload();
      return;
    }
    if (message?.type === "worker.lifecycle.update_check.v156") {
      try {
        chrome.runtime.requestUpdateCheck(() => {
          // Unpacked/developer extensions require a manual extension update.
          void chrome.runtime.lastError;
        });
      } catch (_) { /* Unsupported deployment; leave the Worker running. */ }
      return;
    }
    if (message?.type !== "worker.login.open.v153") return previous(message);
    if (state.inFlight) return;
    const attemptId = String(message.attempt_id || "");
    state.inFlight = true;
    try {
      await report(attemptId, "opening");
      const login = globalThis.__CHAT2API_LOGIN_READINESS_V27__;
      if (typeof login?.openLoginWindow !== "function") throw new Error("Login surface unavailable");
      await login.openLoginWindow();
      // The server independently confirms success from fresh Composer telemetry.
      // A window opening is *not* proof of account authentication.
      await report(attemptId, "manual_required");
    } catch (_) {
      await report(attemptId, "failed");
    } finally {
      state.inFlight = false;
    }
  };
})();