(() => {
  const KEY = "__CHAT2API_CONVERSATION_DISPATCH_V1__";
  if (globalThis[KEY]) return;

  const baseHandleServerMessage = handleServerMessage;
  const baseResolveTargetTab = resolveTargetTab;
  const state = { currentTab: null, chain: Promise.resolve() };
  globalThis[KEY] = state;

  resolveTargetTab = async function resolveCurrentConversationTarget() {
    if (state.currentTab?.id) {
      try { return await chrome.tabs.get(state.currentTab.id); }
      catch (_) { state.currentTab = null; }
    }
    return baseResolveTargetTab();
  };

  async function routedDispatch(message) {
    const resolver = globalThis.resolveTargetTabForRequest;
    if (typeof resolver !== "function") return baseHandleServerMessage(message);
    const tab = await resolver(message);
    if (!tab?.id) throw new Error("Per-key conversation router returned no usable ChatGPT tab");
    state.currentTab = tab;
    await chrome.storage.local.set({ boundTabId: tab.id, autoBind: false, modelsUpdatedAt: 0 });
    try {
      return await baseHandleServerMessage(message);
    } finally {
      state.currentTab = null;
    }
  }

  handleServerMessage = async function handleConversationAwareServerMessage(message) {
    const isRoutedRequest = ["chat.request", "image.request", "voice.request"].includes(message?.type)
      && Boolean(message?.routing?.api_key_id);
    if (!isRoutedRequest) return baseHandleServerMessage(message);

    const task = state.chain.then(() => routedDispatch(message));
    state.chain = task.catch(() => {});
    return task;
  };
})();
