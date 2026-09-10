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
    try { return await callback(); }
    finally { state.currentTab = null; }
  }

  async function resolveRoutedTab(message) {
    const resolver = globalThis.resolveTargetTabForRequest;
    const tab = typeof resolver === "function" ? await resolver(message) : await baseResolveTargetTab();
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

  async function publishRoutedDispatchFailure(message, error) {
    const requestId = String(message?.request_id || "");
    if (!requestId) throw error;
    const text = String(error?.message || error || "Worker route dispatch failed");
    const retryAfterMs = Math.max(0, Number(error?.retry_after_ms || 0));
    const rateLimited = error?.code === "chatgpt_rate_limited" || /temporarily rate limited|too many requests/i.test(text);

    // Dispatch owns transport only. It never clears route fields or closes a
    // window. Any allocation/preflight failure is handed to the single router
    // authority, which performs exactly one lifecycle transition.
    const router = globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
    const routeRetired = typeof router?.failRequest === "function"
      ? await router.failRequest(requestId, `dispatch-failure:${String(error?.code || "unknown")}`).catch(() => false)
      : false;

    const event = {
      type: terminalTypeFor(message),
      request_id: requestId,
      error: text,
      diagnostics: {
        routed_dispatch_terminal_v58: true,
        route_failure_code: String(error?.code || "route_dispatch_failed"),
        route_retired_by_authority_v30: routeRetired,
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

    // This chain serializes only brief route allocation/page hand-off operations.
    // Same-logical-API request admission is exclusively server scheduler v58.
    return enqueueDispatch(async () => {
      try {
        return isRoutedRequest ? await routedDispatch(message) : await dispatchKnownRequest(message);
      } catch (error) {
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
