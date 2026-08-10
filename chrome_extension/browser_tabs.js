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

  async function createAutomationTab() {
    const tab = await chrome.tabs.create({ url: "https://chatgpt.com/", active: true });
    if (!tab.id) throw new Error("Chrome did not create a ChatGPT tab");
    const ready = await waitForController(tab.id);
    await chrome.storage.local.set({ boundTabId: ready.id, autoBind: false, modelsUpdatedAt: 0, automationTabCreatedAt: new Date().toISOString() });
    await sendExtensionStatus(false);
    return ready;
  }

  resolveTargetTab = async function resolveBrowserTarget() {
    try { return await baseResolveTargetTab(); }
    catch (error) {
      const text = String(error?.message || error);
      if (text.includes("Multiple ChatGPT tabs") || text.includes("No ChatGPT tab")) return createAutomationTab();
      throw error;
    }
  };
})();
