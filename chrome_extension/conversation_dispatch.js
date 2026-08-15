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

  async function routedDispatch(message) {
    const resolver = globalThis.resolveTargetTabForRequest;
    if (typeof resolver !== "function") return baseHandleServerMessage(message);
    const tab = await resolver(message);
    if (!tab?.id) throw new Error("Per-key conversation router returned no usable ChatGPT tab");
    if (message?.request_id) {
      state.requestTabs.set(String(message.request_id), { tabId: tab.id, windowId: tab.windowId });
    }
    await chrome.storage.local.set({ boundTabId: tab.id, autoBind: false, modelsUpdatedAt: 0 });
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
    const task = state.chain.then(() => isRoutedRequest ? routedDispatch(message) : dispatchKnownRequest(message));
    state.chain = task.catch(() => {});
    return task;
  };

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(event.type)) return false;
    const requestId = String(event.request_id || "");
    if (requestId) state.requestTabs.delete(requestId);
    return false;
  });
})();
