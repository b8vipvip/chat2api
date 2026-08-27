(() => {
  const KEY = "__CHAT2API_BACKGROUND_REQUEST_HYGIENE_V42__";
  if (globalThis[KEY]) return;

  const state = {
    version: 42,
    queries: 0,
    managedQueries: 0,
  };
  globalThis[KEY] = state;

  function routeOwnsTab(tabId) {
    const router = globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
    for (const route of Object.values(router?.routes || {})) {
      if (route?.tab_id === tabId && route?.window_owned !== false) return "route";
    }
    return "";
  }

  function warmOwnsTab(tabId) {
    const warm = globalThis.__CHAT2API_CONVERSATION_WARM_POOL_V2__;
    for (const slot of warm?.warmSlots?.values?.() || []) {
      if (slot?.tab_id === tabId) return "warm";
    }
    return "";
  }

  function reserveOwnsTab(tabId) {
    const reserve = globalThis.__CHAT2API_RESERVE_POOL_V29__;
    for (const slot of reserve?.reserveSlots?.values?.() || []) {
      if (slot?.tab_id === tabId) return "reserve";
    }
    return "";
  }

  function dispatchOwnsTab(tabId) {
    const dispatch = globalThis.__CHAT2API_CONVERSATION_DISPATCH_V1__;
    for (const target of dispatch?.requestTabs?.values?.() || []) {
      if (target?.id === tabId || target?.tabId === tabId) return "dispatch";
    }
    return "";
  }

  async function externalWarmOwnsTab(tabId) {
    const stored = await chrome.storage.local.get({ chatgptExternalWarmTabIdV28: null }).catch(() => ({}));
    return stored.chatgptExternalWarmTabIdV28 === tabId ? "external-warm" : "";
  }

  async function managedTab(tabId) {
    if (!Number.isInteger(tabId)) return { managed: false, source: "no-tab" };
    const source = routeOwnsTab(tabId) || warmOwnsTab(tabId) || reserveOwnsTab(tabId) || dispatchOwnsTab(tabId) || await externalWarmOwnsTab(tabId);
    return { managed: Boolean(source), source: source || "manual-or-unowned" };
  }

  state.managedTab = managedTab;

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== "chat2api.automation-tab.query") return false;
    state.queries += 1;
    managedTab(sender?.tab?.id).then(result => {
      if (result.managed) state.managedQueries += 1;
      sendResponse({ ok: true, ...result, version: 42 });
    }).catch(error => sendResponse({ ok: false, managed: false, source: "query-error", error: String(error?.message || error), version: 42 }));
    return true;
  });
})();
