(() => {
  const KEY = "__CHAT2API_REQUEST_INTERRUPTION_BRIDGE_V72__";
  if (globalThis[KEY]) return;

  const v6 = globalThis.__CHAT2API_REQUEST_CONTENT_V6__;
  const v5 = globalThis.__CHAT2API_REQUEST_CONTENT_V5__;
  const guard = globalThis.__CHAT2API_INTERRUPTION_GUARD_V72__;
  const baseListener = v6?.listener;
  if (!v6 || typeof baseListener !== "function" || !guard?.resolveBlockingInterruption) {
    globalThis[KEY] = { revision: 72, installed: false, reason: "dependencies-missing" };
    return;
  }

  try { chrome.runtime.onMessage.removeListener(baseListener); } catch (_) {}

  const listener = (message, sender, sendResponse) => {
    if (message?.type === "chat2api.request") {
      // Acknowledge ownership immediately, then resolve safe UI interruptions
      // before v6 is allowed to type/click. The original listener receives a no-op
      // response callback because this bridge already answered the transport call.
      sendResponse({ ok: true, controller: "request-v6", revision: 69, interruption_guard_revision: 72 });
      Promise.resolve()
        .then(() => guard.resolveBlockingInterruption({ force: true, phase: "before-request" }))
        .catch(() => null)
        .finally(() => baseListener(message, sender, () => {}));
      return false;
    }

    if (message?.type === "chat2api.request.preflight") {
      guard.resolveBlockingInterruption({ force: true, phase: "preflight" }).catch(() => {});
    }
    return baseListener(message, sender, sendResponse);
  };

  v6.listener = listener;
  if (v5) v5.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
  globalThis[KEY] = { revision: 72, installed: true, listener, baseListener };
})();
