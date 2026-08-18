(() => {
  const KEY = "__CHAT2API_LINUX_WORKER_BINDING_V30__";
  if (globalThis[KEY]) return;

  const PENDING_KEY = "linuxWorkerBindingPendingV30";
  const HASH_TICKET = "chat2api-worker-bind";
  const HASH_SERVER = "chat2api-server";
  const RETRY_MS = 5000;
  const state = { inFlight: null, retryTimer: null };
  globalThis[KEY] = state;

  function normalizeServer(value) {
    try {
      const url = new URL(String(value || ""));
      if (!/^https?:$/.test(url.protocol)) return "";
      url.pathname = url.pathname.replace(/\/$/, "");
      url.search = "";
      url.hash = "";
      return url.toString().replace(/\/$/, "");
    } catch (_) {
      return "";
    }
  }

  function bindingFromUrl(value, tabId = null) {
    try {
      const url = new URL(String(value || ""));
      if (!isChatGptUrl(url.toString())) return null;
      const params = new URLSearchParams(url.hash.replace(/^#/, ""));
      const ticket = String(params.get(HASH_TICKET) || "").trim();
      const serverUrl = normalizeServer(params.get(HASH_SERVER));
      if (!ticket.startsWith("wbind_") || ticket.length > 160 || !serverUrl) return null;
      return { ticket, serverUrl, tabId: Number.isInteger(tabId) ? tabId : null, capturedAt: Date.now() };
    } catch (_) {
      return null;
    }
  }

  async function ensureDeviceIdV30() {
    const stored = await chrome.storage.local.get({ deviceId: "" });
    let value = String(stored.deviceId || "").trim();
    if (!value) {
      value = self.crypto?.randomUUID?.() || `device-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      await chrome.storage.local.set({ deviceId: value });
    }
    return value;
  }

  async function hideBindingFragment(tabId) {
    if (!Number.isInteger(tabId)) return;
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          try {
            if (location.hash.includes("chat2api-worker-bind=")) {
              history.replaceState(history.state, "", `${location.pathname}${location.search}`);
            }
          } catch (_) {}
        },
      });
      return;
    } catch (_) {}
    try {
      const tab = await chrome.tabs.get(tabId);
      const url = new URL(tab.url || tab.pendingUrl || "");
      if (!url.hash.includes("chat2api-worker-bind=")) return;
      url.hash = "";
      await chrome.tabs.update(tabId, { url: url.toString() });
    } catch (_) {}
  }

  async function savePending(binding) {
    await chrome.storage.session.set({ [PENDING_KEY]: binding });
  }

  async function loadPending() {
    const stored = await chrome.storage.session.get({ [PENDING_KEY]: null }).catch(() => ({}));
    const value = stored?.[PENDING_KEY];
    if (!value || typeof value !== "object") return null;
    const ticket = String(value.ticket || "").trim();
    const serverUrl = normalizeServer(value.serverUrl);
    if (!ticket.startsWith("wbind_") || !serverUrl) return null;
    return { ticket, serverUrl, tabId: Number.isInteger(value.tabId) ? value.tabId : null, capturedAt: Number(value.capturedAt || 0) };
  }

  async function clearPending() {
    clearTimeout(state.retryTimer);
    state.retryTimer = null;
    await chrome.storage.session.remove(PENDING_KEY).catch(() => {});
  }

  function scheduleRetry() {
    clearTimeout(state.retryTimer);
    state.retryTimer = setTimeout(() => claimPending().catch(() => {}), RETRY_MS);
  }

  async function claim(binding) {
    const settings = await config();
    const deviceId = await ensureDeviceIdV30();
    const savedServer = normalizeServer(settings.serverUrl);
    const reuseExisting = savedServer === binding.serverUrl && Boolean(settings.clientId && settings.clientToken);
    const body = {
      ticket: binding.ticket,
      device_id: deviceId,
      client_id: reuseExisting ? settings.clientId : "",
      client_token: reuseExisting ? settings.clientToken : "",
      name: String(settings.extensionName || "Linux Worker Chrome").slice(0, 120),
      browser_name: "Chrome",
      version: chrome.runtime.getManifest().version,
      metadata: {
        runtime_id: chrome.runtime.id,
        extension_version: chrome.runtime.getManifest().version,
        device_id: deviceId,
      },
    };

    const response = await fetch(`${binding.serverUrl}/api/extensions/worker-bind`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      const error = new Error(String(payload.detail || `worker binding HTTP ${response.status}`));
      error.status = response.status;
      throw error;
    }
    if (!payload.bound || !payload.client_id) throw new Error("Worker binding response is incomplete");

    const update = {
      serverUrl: binding.serverUrl,
      pairingCode: "",
      extensionName: String(settings.extensionName || "Linux Worker Chrome"),
      clientId: String(payload.client_id),
      autoBind: true,
      linuxWorkerId: String(payload.worker_id || ""),
      linuxWorkerBindingVersion: 30,
      linuxWorkerBoundAt: new Date().toISOString(),
    };
    if (payload.token) update.clientToken = String(payload.token);
    if (!payload.token && !reuseExisting) throw new Error("New Worker binding did not return an extension token");
    await chrome.storage.local.set(update);
    await clearPending();
    if (socket) {
      try { socket.close(4000, "Linux Worker binding completed"); } catch (_) {}
    }
    await connectSocket();
    return payload;
  }

  async function claimPending() {
    if (state.inFlight) return state.inFlight;
    state.inFlight = (async () => {
      const pending = await loadPending();
      if (!pending) return null;
      try {
        return await claim(pending);
      } catch (error) {
        const status = Number(error?.status || 0);
        if (status >= 400 && status < 500 && status !== 429) await clearPending();
        else scheduleRetry();
        return null;
      }
    })().finally(() => { state.inFlight = null; });
    return state.inFlight;
  }

  async function captureFromTab(tab) {
    if (!Number.isInteger(tab?.id)) return false;
    const binding = bindingFromUrl(tab.url || tab.pendingUrl || "", tab.id);
    if (!binding) return false;
    await savePending(binding);
    await hideBindingFragment(tab.id);
    await claimPending();
    return true;
  }

  async function scanTabs() {
    const tabs = await chrome.tabs.query({ url: CHATGPT_URLS }).catch(() => []);
    for (const tab of tabs || []) {
      if (await captureFromTab(tab)) return true;
    }
    return false;
  }

  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    const value = changeInfo.url || tab?.url || tab?.pendingUrl || "";
    if (!String(value).includes(`${HASH_TICKET}=`)) return;
    captureFromTab({ ...tab, id: tabId, url: value }).catch(() => {});
  });

  state.claimPending = claimPending;
  state.scanTabs = scanTabs;
  loadPending().then(pending => pending ? claimPending() : scanTabs()).catch(() => {});
})();
