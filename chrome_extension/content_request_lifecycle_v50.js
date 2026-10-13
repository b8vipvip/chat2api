(() => {
  const KEY = "__CHAT2API_REQUEST_LIFECYCLE_V50__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V6__";
  const legacy = globalThis.__CHAT2API_REQUEST_CONTENT_V5__;
  const base = globalThis[REQUEST_KEY];
  const oldListener = base?.listener;
  const state = { version: 50, role: "read-only-lifecycle-status", status_checks: 0, listener: null };
  globalThis[KEY] = state;
  if (typeof oldListener !== "function") return;

  try { chrome.runtime.onMessage.removeListener(oldListener); } catch (_) {}
  const listener = (message, sender, sendResponse) => {
    if (message?.type === "chat2api.lifecycle-status.v50") {
      const active = globalThis[REQUEST_KEY]?.active;
      state.status_checks += 1;
      sendResponse({
        ok: true,
        version: 50,
        active: Boolean(active?.requestId),
        active_request_id: String(active?.requestId || ""),
        cancelled: Boolean(active?.cancelled),
      });
      return false;
    }
    // Admission, response, cancellation, errors and final transitions belong
    // exclusively to content_request_v6. This compatibility wrapper must
    // never emit a terminal event or acknowledge a request itself.
    return oldListener(message, sender, sendResponse);
  };
  state.listener = listener;
  base.listener = listener;
  if (legacy) legacy.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();
