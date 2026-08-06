(() => {
  const LAUNCH_KEY = "chat2api_launch";
  const bindingTasks = new Map();

  function launchTokenFromUrl(value = "") {
    try {
      const url = new URL(value);
      return url.searchParams.get(LAUNCH_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  async function fetchBootstrapWithRetry(timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 2500);
      try {
        const response = await fetch(LOCAL_BOOTSTRAP_URL, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.ok) return response.json();
        lastError = new Error(`Local bootstrap returned HTTP ${response.status}`);
      } catch (error) {
        lastError = error;
      } finally {
        clearTimeout(timer);
      }
      await sleep(500);
    }
    throw lastError || new Error("Local desktop bootstrap is unavailable");
  }

  async function waitForReadyTab(tabId, timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const tab = await chrome.tabs.get(tabId);
      const value = tab.url || tab.pendingUrl || "";
      if (tab.status === "complete" && isChatGptUrl(value)) return tab;
      await sleep(200);
    }
    throw new Error("Timed out waiting for the launched ChatGPT page");
  }

  async function ensureBootstrapConfiguration(data) {
    if (!data?.server_url || !data?.pairing_code) {
      throw new Error("Local bootstrap payload is incomplete");
    }
    const settings = await config();
    const serverUrl = String(data.server_url).trim().replace(/\/$/, "");
    const currentServer = String(settings.serverUrl || "").trim().replace(/\/$/, "");
    if (!settings.clientId || !settings.clientToken || serverUrl !== currentServer) {
      await pair({
        serverUrl,
        pairingCode: data.pairing_code,
        extensionName: data.extension_name || "chat2api Existing Chrome",
        force: true,
        autoBind: false,
      });
      return;
    }
    await chrome.storage.local.set({
      serverUrl,
      pairingCode: data.pairing_code,
      extensionName: data.extension_name || settings.extensionName || "Chrome",
      autoBind: false,
    });
    if (!socketReady()) await connectSocket();
  }

  async function removeMarker(tabId) {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: key => {
        const url = new URL(location.href);
        if (!url.searchParams.has(key)) return;
        url.searchParams.delete(key);
        history.replaceState(history.state, "", url.toString());
      },
      args: [LAUNCH_KEY],
    });
  }

  async function bindMarkedTab(tab) {
    const candidateUrl = tab?.pendingUrl || tab?.url || "";
    const token = launchTokenFromUrl(candidateUrl);
    if (!tab?.id || !token) return false;
    if (bindingTasks.has(tab.id)) return bindingTasks.get(tab.id);

    const task = (async () => {
      const data = await fetchBootstrapWithRetry();
      if (!data.launch_token || data.launch_token !== token) {
        throw new Error("The launched ChatGPT tab does not match the current desktop wake command");
      }
      await ensureBootstrapConfiguration(data);
      const ready = await waitForReadyTab(tab.id);
      await ensureContent(ready.id);
      const settings = await config();
      await chrome.storage.local.set({
        boundTabId: ready.id,
        autoBind: false,
        models: Array.isArray(settings.models) ? settings.models : [],
        currentModel: settings.currentModel || "chatgpt-web",
        modelsUpdatedAt: Date.now(),
        lastLaunchToken: token,
        lastLaunchAt: new Date().toISOString(),
        lastLaunchBindingError: "",
      });
      await removeMarker(ready.id);
      await sendExtensionStatus(false);
      return true;
    })().catch(async error => {
      await chrome.storage.local.set({
        lastLaunchBindingError: String(error?.message || error),
      });
      return false;
    }).finally(() => bindingTasks.delete(tab.id));

    bindingTasks.set(tab.id, task);
    return task;
  }

  async function scanMarkedTabs() {
    const tabs = await chatTabs();
    for (const tab of tabs) {
      const value = tab.pendingUrl || tab.url || "";
      if (launchTokenFromUrl(value)) await bindMarkedTab(tab);
    }
  }

  chrome.tabs.onCreated.addListener(tab => {
    if (launchTokenFromUrl(tab.pendingUrl || tab.url || "")) bindMarkedTab(tab).catch(() => {});
  });
  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    const value = changeInfo.url || tab.pendingUrl || tab.url || "";
    if (launchTokenFromUrl(value)) bindMarkedTab({ ...tab, id: tabId }).catch(() => {});
  });

  setInterval(() => scanMarkedTabs().catch(() => {}), 1000);
  setTimeout(() => scanMarkedTabs().catch(() => {}), 100);
})();
