(() => {
  const KEY = "__CHAT2API_REQUEST_LIFECYCLE_V50__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const state = { version: 50, busy_rejections: 0, status_checks: 0, listener: null };
  globalThis[KEY] = state;

  const base = globalThis[REQUEST_KEY];
  if (!base?.listener) return;

  const emit = event => chrome.runtime.sendMessage({ type: "chat2api.event", event }).catch(() => {});
  const oldListener = base.listener;
  try { chrome.runtime.onMessage.removeListener(oldListener); } catch (_) {}

  const listener = (message, sender, sendResponse) => {
    if (message?.type === "chat2api.lifecycle-status.v50") {
      state.status_checks += 1;
      const active = globalThis[REQUEST_KEY]?.active;
      sendResponse({
        ok: true,
        version: 50,
        active: Boolean(active?.requestId),
        active_request_id: String(active?.requestId || ""),
        cancelled: Boolean(active?.cancelled),
      });
      return true;
    }

    if (message?.type === "chat2api.request") {
      const active = globalThis[REQUEST_KEY]?.active;
      const requestId = String(message?.requestId || message?.request_id || "");
      if (active?.requestId && String(active.requestId) !== requestId) {
        state.busy_rejections += 1;
        emit({
          type: "chat.error",
          request_id: requestId,
          error: "Target ChatGPT tab is still finalizing the previous request",
          diagnostics: {
            request_lifecycle_guard: "request-lifecycle-v50",
            request_lifecycle_busy_rejected: true,
            request_lifecycle_active_request_id: String(active.requestId),
          },
        });
        sendResponse({ ok: true, controller: "request-lifecycle-v50", rejected_busy: true });
        return false;
      }
    }

    try {
      return oldListener(message, sender, sendResponse);
    } catch (error) {
      if (message?.type === "chat2api.request") {
        const requestId = String(message?.requestId || message?.request_id || "");
        emit({
          type: "chat.error",
          request_id: requestId,
          error: String(error?.message || error || "ChatGPT request controller failed"),
          diagnostics: {
            request_lifecycle_guard: "request-lifecycle-v50",
            request_lifecycle_listener_exception: true,
          },
        });
        sendResponse({ ok: true, controller: "request-lifecycle-v50", listener_error: true });
        return false;
      }
      throw error;
    }
  };

  state.listener = listener;
  base.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();
