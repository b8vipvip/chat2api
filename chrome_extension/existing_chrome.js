(() => {
  const LAUNCH_KEY = "chat2api_launch";
  const launchTasks = new Map();
  const baseResolveTargetTab = resolveTargetTab;

  function launchTokenFromUrl(value = "") {
    try {
      const url = new URL(value);
      const queryToken = url.searchParams.get(LAUNCH_KEY);
      if (queryToken) return queryToken;
      const hash = url.hash.startsWith("#") ? url.hash.slice(1) : url.hash;
      return new URLSearchParams(hash).get(LAUNCH_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  async function fetchBootstrapPayload() {
    const response = await fetch(LOCAL_BOOTSTRAP_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`Local bootstrap returned HTTP ${response.status}`);
    return response.json();
  }

  async function ensureBootstrapConfiguration(data) {
    if (!data?.server_url || !data?.pairing_code) {
      throw new Error("Local bootstrap payload is incomplete");
    }
    const settings = await config();
    const cleanServer = String(data.server_url).trim().replace(/\/$/, "");
    const savedServer = String(settings.serverUrl || "").trim().replace(/\/$/, "");
    const hasIdentity = Boolean(settings.clientId && settings.clientToken);
    if (!hasIdentity || cleanServer !== savedServer) {
      await pair({
        serverUrl: cleanServer,
        pairingCode: data.pairing_code,
        extensionName: data.extension_name || "chat2api Existing Chrome",
        force: true,
        autoBind: false,
      });
      return;
    }
    await chrome.storage.local.set({
      serverUrl: cleanServer,
      pairingCode: data.pairing_code,
      extensionName: data.extension_name || settings.extensionName || "Chrome",
      autoBind: false,
    });
    if (!socketReady()) await connectSocket();
  }

  async function removeLaunchMarker(tabId) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        func: key => {
          const url = new URL(location.href);
          let changed = false;
          if (url.searchParams.has(key)) {
            url.searchParams.delete(key);
            changed = true;
          }
          const hash = url.hash.startsWith("#") ? url.hash.slice(1) : url.hash;
          const hashParams = new URLSearchParams(hash);
          if (hashParams.has(key)) {
            hashParams.delete(key);
            url.hash = hashParams.toString() ? `#${hashParams}` : "";
            changed = true;
          }
          if (changed) history.replaceState(history.state, "", url.toString());
        },
        args: [LAUNCH_KEY],
      });
    } catch (_) {}
  }

  async function bindLaunchTab(tab) {
    const candidateUrl = tab?.pendingUrl || tab?.url || "";
    if (!tab?.id || !isChatGptUrl(candidateUrl)) return false;
    const token = launchTokenFromUrl(candidateUrl);
    if (!token) return false;
    if (launchTasks.has(tab.id)) return launchTasks.get(tab.id);

    const task = (async () => {
      const data = await fetchBootstrapPayload();
      if (!data.launch_token || data.launch_token !== token) {
        throw new Error("The ChatGPT launch token does not match the local desktop command");
      }
      await ensureBootstrapConfiguration(data);
      await ensureContent(tab.id);
      await chrome.storage.local.set({
        boundTabId: tab.id,
        autoBind: false,
        modelsUpdatedAt: 0,
        lastLaunchToken: token,
        lastLaunchAt: new Date().toISOString(),
      });
      await removeLaunchMarker(tab.id);
      await sendExtensionStatus(true);
      return true;
    })().catch(error => {
      console.warn("chat2api launch-tab binding failed", error);
      return false;
    }).finally(() => {
      launchTasks.delete(tab.id);
    });

    launchTasks.set(tab.id, task);
    return task;
  }

  async function waitForTabReady(tabId, timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const tab = await chrome.tabs.get(tabId);
      if (tab.status === "complete" && isChatGptUrl(tab.url || "")) return tab;
      await sleep(150);
    }
    throw new Error("Timed out waiting for the new ChatGPT tab");
  }

  async function createAutomationTab() {
    const tab = await chrome.tabs.create({ url: "https://chatgpt.com/", active: true });
    if (!tab.id) throw new Error("Chrome did not create a ChatGPT tab");
    const ready = await waitForTabReady(tab.id);
    await ensureContent(ready.id);
    await chrome.storage.local.set({
      boundTabId: ready.id,
      autoBind: false,
      modelsUpdatedAt: 0,
      automationTabCreatedAt: new Date().toISOString(),
    });
    await sendExtensionStatus(true);
    return ready;
  }

  resolveTargetTab = async function resolveExistingChromeTarget() {
    try {
      return await baseResolveTargetTab();
    } catch (error) {
      const message = String(error?.message || error);
      if (
        message.includes("Multiple ChatGPT tabs") ||
        message.includes("No ChatGPT tab")
      ) {
        return createAutomationTab();
      }
      throw error;
    }
  };

  chrome.tabs.onCreated.addListener(tab => {
    bindLaunchTab(tab).catch(() => {});
  });

  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (
      launchTokenFromUrl(changeInfo.url || "") ||
      launchTokenFromUrl(tab.pendingUrl || tab.url || "")
    ) {
      bindLaunchTab({ ...tab, id: tabId }).catch(() => {});
    }
  });

  async function scanLaunchTabs() {
    const tabs = await chatTabs();
    for (const tab of tabs) {
      if (launchTokenFromUrl(tab.pendingUrl || tab.url || "")) {
        await bindLaunchTab(tab);
      }
    }
  }

  chrome.runtime.onStartup.addListener(() => scanLaunchTabs().catch(() => {}));
  setTimeout(() => scanLaunchTabs().catch(() => {}), 400);
})();
