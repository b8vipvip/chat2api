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

  async function createAutomationWindow() {
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
      if (text.includes("Multiple ChatGPT tabs") || text.includes("No ChatGPT tab")) return createAutomationWindow();
      throw error;
    }
  };
})();
