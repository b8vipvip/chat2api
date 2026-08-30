(() => {
  const baseResolveTargetTab = resolveTargetTab;

  async function waitForController(tabId, timeoutMs = 30000) {
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const tab = await chrome.tabs.get(tabId);
        if (!isChatGptUrl(tab.url || tab.pendingUrl || "")) { await sleep(200); continue; }
        await ensureContent(tabId);
        return tab;
      } catch (error) { lastError = error; }
      await sleep(250);
    }
    throw lastError || new Error("Timed out waiting for the ChatGPT page controller");
  }

  async function beforeWindowCreate(purpose) {
    const guard = globalThis.__CHAT2API_RATE_LIMIT_GUARD_V52__;
    if (typeof guard?.beforeWindowCreate === "function") await guard.beforeWindowCreate(purpose);
  }

  async function chooseExistingTab() {
    const tabs = await chatTabs().catch(() => []);
    if (!tabs.length) return null;
    const sorted = [...tabs].sort((left, right) => {
      const activeDiff = Number(Boolean(right?.active)) - Number(Boolean(left?.active));
      if (activeDiff) return activeDiff;
      return Number(right?.lastAccessed || 0) - Number(left?.lastAccessed || 0);
    });
    const chosen = sorted[0] || null;
    if (!Number.isInteger(chosen?.id)) return null;
    await chrome.storage.local.set({
      boundTabId: chosen.id,
      autoBind: false,
      modelsUpdatedAt: 0,
      automationTabAdoptedAt: new Date().toISOString(),
      automationWindowId: chosen.windowId,
      automationWindowStrategy: "adopt-existing-on-multiple-tabs-v52",
    }).catch(() => {});
    if (typeof sendExtensionStatus === "function") await sendExtensionStatus(false).catch(() => {});
    return chosen;
  }

  async function createAutomationWindow() {
    await beforeWindowCreate("automation-fallback");
    const created = await chrome.windows.create({
      url: "https://chatgpt.com/",
      focused: false,
      type: "normal",
    });
    if (!created?.id) throw new Error("Chrome did not create a ChatGPT automation window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item.id)) || null;
    }
    if (!tab?.id) throw new Error("The ChatGPT automation window contains no usable tab");
    const ready = await waitForController(tab.id);
    await chrome.storage.local.set({
      boundTabId: ready.id,
      autoBind: false,
      modelsUpdatedAt: 0,
      automationTabCreatedAt: new Date().toISOString(),
      automationWindowId: created.id,
      automationWindowStrategy: "single-tab-window",
    });
    await sendExtensionStatus(false);
    return ready;
  }

  resolveTargetTab = async function resolveBrowserTarget() {
    try { return await baseResolveTargetTab(); }
    catch (error) {
      const text = String(error?.message || error);
      if (text.includes("Multiple ChatGPT tabs")) {
        // Never respond to an already-multiple state by creating one more window.
        // The previous behavior was a positive-feedback loop: N tabs -> N+1 on
        // every request. Adopt one existing Worker tab instead and bind it.
        const existing = await chooseExistingTab();
        if (existing) return existing;
        throw error;
      }
      if (text.includes("No ChatGPT tab")) return createAutomationWindow();
      throw error;
    }
  };
})();
