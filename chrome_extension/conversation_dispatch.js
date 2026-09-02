(() => {
  const KEY = "__CHAT2API_CONVERSATION_DISPATCH_V1__";
  if (globalThis[KEY]) return;

  const baseHandleServerMessage = handleServerMessage;
  const baseResolveTargetTab = resolveTargetTab;
  const state = { currentTab: null, chain: Promise.resolve(), requestTabs: new Map() };
  globalThis[KEY] = state;

  resolveTargetTab = async function resolveCurrentConversationTarget() {
    if (state.currentTab?.id) {
      try { return await chrome.tabs.get(state.currentTab.id); }
      catch (_) { state.currentTab = null; }
    }
    return baseResolveTargetTab();
  };

  async function withCurrentTab(tab, callback) {
    state.currentTab = tab;
    try {
      return await callback();
    } finally {
      state.currentTab = null;
    }
  }

  async function resolveRoutedTab(message) {
    const resolver = globalThis.resolveTargetTabForRequest;
    const tab = typeof resolver === "function"
      ? await resolver(message)
      : await baseResolveTargetTab();
    if (!tab?.id) throw new Error("Per-key conversation router returned no usable ChatGPT tab");
    if (message?.request_id) {
      state.requestTabs.set(String(message.request_id), { tabId: tab.id, windowId: tab.windowId });
    }
    await chrome.storage.local.set({ boundTabId: tab.id, autoBind: false, modelsUpdatedAt: 0 });
    return tab;
  }

  function enqueueDispatch(taskFactory) {
    const task = state.chain.then(taskFactory);
    state.chain = task.catch(() => {});
    return task;
  }

  function terminalTypeFor(message) {
    if (message?.type === "image.request") return "image.error";
    if (message?.type === "voice.request" || message?.type === "voice.live.start") return "voice.error";
    return "chat.error";
  }

  function releaseConversationReservation(requestId) {
    const id = String(requestId || "");
    if (!id) return false;
    const router = globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
    const routes = router?.routes;
    const activeRequests = router?.activeRequests;
    let released = false;

    if (routes instanceof Map) {
      for (const route of routes.values()) {
        if (String(route?.inflight_request_id || "") !== id) continue;
        route.inflight_request_id = null;
        route.last_used_at = Date.now();
        released = true;
      }
    }
    if (activeRequests instanceof Map && activeRequests.has(id)) {
      activeRequests.delete(id);
      released = true;
    }
    return released;
  }

  async function publishRoutedDispatchFailure(message, error) {
    const requestId = String(message?.request_id || "");
    if (!requestId) throw error;
    const text = String(error?.message || error || "Worker route dispatch failed");
    const retryAfterMs = Math.max(0, Number(error?.retry_after_ms || 0));
    const rateLimited = error?.code === "chatgpt_rate_limited" || /temporarily rate limited|too many requests/i.test(text);
    // resolveTargetTabForRequest reserves a per-key route before background.js
    // performs attachment preparation. If preparation fails before the request is
    // handed to the content request controller, no content terminal event exists to
    // release that reservation. Release it here before reporting the terminal error
    // so one failed visual upload cannot permanently consume a conversation Worker.
    const routeReservationReleased = releaseConversationReservation(requestId);
    const event = {
      type: terminalTypeFor(message),
      request_id: requestId,
      error: text,
      diagnostics: {
        routed_dispatch_terminal_v58: true,
        routed_dispatch_reservation_release_v68: true,
        route_reservation_released: routeReservationReleased,
        route_failure_code: String(error?.code || "route_dispatch_failed"),
        rate_limit_terminal: rateLimited,
        retry_after_ms: retryAfterMs,
      },
    };
    if (retryAfterMs > 0) event.retry_after_ms = retryAfterMs;
    const sent = await trySendSocket(event);
    state.requestTabs.delete(requestId);
    if (!sent) throw error;
    return null;
  }

  // GPT Live is loaded outside this handler so it can stream many audio/control
  // frames without walking the generic dispatch chain. Its start phase still must
  // use exactly the same serialized worker allocation as chat/image/voice requests.
  globalThis.chat2apiResolveRoutedWorkerTabV24 = function resolveExternalRoutedWorkerTab(message) {
    return enqueueDispatch(() => resolveRoutedTab(message));
  };

  async function routedDispatch(message) {
    const tab = await resolveRoutedTab(message);
    return withCurrentTab(tab, () => baseHandleServerMessage(message));
  }

  async function dispatchKnownRequest(message) {
    const target = state.requestTabs.get(String(message?.request_id || ""));
    if (!target?.tabId) return baseHandleServerMessage(message);
    let tab = null;
    try { tab = await chrome.tabs.get(target.tabId); } catch (_) {}
    if (!tab?.id) return baseHandleServerMessage(message);
    return withCurrentTab(tab, () => baseHandleServerMessage(message));
  }

  handleServerMessage = async function handleConversationAwareServerMessage(message) {
    const isRoutedRequest = ["chat.request", "image.request", "voice.request", "voice.live.start"].includes(message?.type)
      && Boolean(message?.routing?.api_key_id);
    const isTargetedControl = ["chat.cancel", "image.cancel", "voice.cancel"].includes(message?.type)
      && state.requestTabs.has(String(message?.request_id || ""));

    if (!isRoutedRequest && !isTargetedControl) return baseHandleServerMessage(message);

    // Serialize only route allocation / page dispatch. Content scripts return as soon
    // as work starts, so generations continue independently in their own worker tabs.
    return enqueueDispatch(async () => {
      try {
        return isRoutedRequest ? await routedDispatch(message) : await dispatchKnownRequest(message);
      } catch (error) {
        // resolveRoutedTab() runs outside background.js's per-request try/catch. A
        // rate-limit guard or window-allocation failure here used to escape only to
        // console.error, leaving the server request active until its watchdog. The
        // v68 boundary additionally releases any route reservation already claimed
        // before an attachment/preflight dispatch failure.
        if (isRoutedRequest) return publishRoutedDispatchFailure(message, error);
        throw error;
      }
    });
  };

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled", "voice.error", "voice.cancelled"].includes(event.type)) return false;
    const requestId = String(event.request_id || "");
    if (requestId) state.requestTabs.delete(requestId);
    return false;
  });
})();
