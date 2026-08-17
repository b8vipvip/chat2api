(() => {
  const KEY = "__CHAT2API_LOGIN_READINESS_V27__";
  if (globalThis[KEY]) return;

  const CACHE_KEY = "chatgptLoginReadinessV27";
  const CACHE_MS = 15000;
  const PROBE_URL = "https://chatgpt.com/";
  const state = {
    inFlight: null,
    bootValidated: false,
    takingProbe: false,
  };
  globalThis[KEY] = state;

  function normalizeState(value) {
    const stateValue = String(value || "unknown");
    return ["checking", "ready", "login_required", "unknown"].includes(stateValue) ? stateValue : "unknown";
  }

  function isAuthLikeUrl(value = "") {
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase();
      const path = url.pathname.toLowerCase();
      if (isChatGptUrl(value) && /(^|\/)(auth|login|signin|sign-in|signup|sign-up)(\/|$)/.test(path)) return true;
      if (host.endsWith(".openai.com") && /(auth|login|signin|signup)/.test(`${host}${path}`)) return true;
      return false;
    } catch (_) {
      return false;
    }
  }

  function snapshotFrom(value) {
    return {
      state: normalizeState(value?.state),
      confidence: String(value?.confidence || "low"),
      strategy: String(value?.strategy || "unknown"),
      detector: "login-v27",
      composer_ready: value?.composer_ready === true,
      document_ready: value?.document_ready === true,
      checked_at_ms: Number(value?.checked_at_ms || 0),
      tab_id: Number.isInteger(value?.tab_id) ? value.tab_id : null,
      window_id: Number.isInteger(value?.window_id) ? value.window_id : null,
    };
  }

  async function cachedSnapshot() {
    const stored = await chrome.storage.local.get({ [CACHE_KEY]: null }).catch(() => ({}));
    return stored?.[CACHE_KEY] ? snapshotFrom(stored[CACHE_KEY]) : null;
  }

  function fresh(snapshot) {
    const age = Date.now() - Number(snapshot?.checked_at_ms || 0);
    return Boolean(snapshot?.checked_at_ms && age >= 0 && age < CACHE_MS);
  }

  async function persist(snapshot) {
    const value = snapshotFrom({ ...snapshot, checked_at_ms: snapshot?.checked_at_ms || Date.now() });
    state.bootValidated = value.state !== "checking";
    await chrome.storage.local.set({
      [CACHE_KEY]: value,
      chatgptLoginState: value.state,
      chatgptLoginConfidence: value.confidence,
      chatgptLoginStrategy: value.strategy,
      chatgptLoginComposerReady: value.composer_ready,
      chatgptLoginCheckedAt: value.checked_at_ms,
      chatgptLoginTabId: value.tab_id,
      chatgptLoginWindowId: value.window_id,
    }).catch(() => {});
    return value;
  }

  async function trackedProbe() {
    const stored = await chrome.storage.local.get({
      chatgptLoginProbeTabId: null,
      chatgptLoginProbeWindowId: null,
      chatgptLoginProbeAdoptable: false,
    }).catch(() => ({}));
    if (!Number.isInteger(stored.chatgptLoginProbeTabId)) return null;
    try {
      const tab = await chrome.tabs.get(stored.chatgptLoginProbeTabId);
      const url = tab?.url || tab?.pendingUrl || "";
      if (!isChatGptUrl(url) && !isAuthLikeUrl(url)) return null;
      return {
        tab,
        tab_id: tab.id,
        window_id: tab.windowId,
        adoptable: stored.chatgptLoginProbeAdoptable === true,
      };
    } catch (_) {
      return null;
    }
  }

  async function clearTrackedProbe() {
    await chrome.storage.local.set({
      chatgptLoginProbeTabId: null,
      chatgptLoginProbeWindowId: null,
      chatgptLoginProbeAdoptable: false,
    }).catch(() => {});
  }

  async function queryDetector(tabId) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.login.detect.v27" });
      if (response?.ok) return response.data || null;
    } catch (_) {}
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["content_login_v27.js"] });
      await sleep(80);
      const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.login.detect.v27" });
      return response?.ok ? (response.data || null) : null;
    } catch (_) {
      return null;
    }
  }

  async function candidateTabs() {
    const rows = [];
    const seen = new Set();
    const add = tab => {
      if (!Number.isInteger(tab?.id) || seen.has(tab.id)) return;
      const url = tab.url || tab.pendingUrl || "";
      if (!isChatGptUrl(url) && !isAuthLikeUrl(url)) return;
      seen.add(tab.id);
      rows.push(tab);
    };

    const probe = await trackedProbe();
    if (probe?.tab) add(probe.tab);

    const settings = await config().catch(() => ({}));
    if (Number.isInteger(settings.boundTabId)) {
      try { add(await chrome.tabs.get(settings.boundTabId)); } catch (_) {}
    }

    try {
      const active = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      for (const tab of active || []) add(tab);
    } catch (_) {}

    try {
      for (const tab of await chatTabs()) add(tab);
    } catch (_) {}
    return rows;
  }

  async function runDetection() {
    const candidates = await candidateTabs();
    if (!candidates.length) {
      return persist({
        state: "unknown",
        confidence: "low",
        strategy: "no-chatgpt-tab",
        composer_ready: false,
        document_ready: false,
        checked_at_ms: Date.now(),
        tab_id: null,
        window_id: null,
      });
    }

    let loginRequired = null;
    let checking = null;
    let unknown = null;
    for (const tab of candidates) {
      const url = tab.url || tab.pendingUrl || "";
      if (isAuthLikeUrl(url)) {
        loginRequired ||= {
          state: "login_required",
          confidence: "high",
          strategy: "auth-redirect-url",
          composer_ready: false,
          document_ready: tab.status === "complete",
          tab_id: tab.id,
          window_id: tab.windowId,
        };
        continue;
      }
      if (!isChatGptUrl(url)) continue;
      const detected = await queryDetector(tab.id);
      if (!detected) {
        unknown ||= {
          state: "unknown",
          confidence: "low",
          strategy: "detector-no-result",
          composer_ready: false,
          document_ready: tab.status === "complete",
          tab_id: tab.id,
          window_id: tab.windowId,
        };
        continue;
      }
      const result = {
        ...detected,
        state: normalizeState(detected.state),
        tab_id: tab.id,
        window_id: tab.windowId,
      };
      if (result.state === "ready" && result.composer_ready === true) return persist(result);
      if (result.state === "login_required") loginRequired ||= result;
      else if (result.state === "checking") checking ||= result;
      else unknown ||= result;
    }

    return persist(loginRequired || checking || unknown || {
      state: "unknown",
      confidence: "low",
      strategy: "no-passive-login-evidence",
      composer_ready: false,
      document_ready: false,
      tab_id: candidates[0]?.id || null,
      window_id: candidates[0]?.windowId || null,
    });
  }

  async function detect(force = false) {
    const cached = await cachedSnapshot();
    if (!force && state.bootValidated && cached && fresh(cached)) return cached;
    if (state.inFlight) return state.inFlight;
    state.inFlight = runDetection().finally(() => { state.inFlight = null; });
    return state.inFlight;
  }

  async function ensureProbeWindow({ focused = false, userVisible = false } = {}) {
    let probe = await trackedProbe();
    if (probe?.tab) {
      if (userVisible) {
        await chrome.storage.local.set({ chatgptLoginProbeAdoptable: false }).catch(() => {});
        try { await chrome.windows.update(probe.window_id, { focused: true }); } catch (_) {}
        try { await chrome.tabs.update(probe.tab_id, { active: true }); } catch (_) {}
      }
      return { ...probe, existing: true };
    }

    const created = await chrome.windows.create({ url: PROBE_URL, focused: Boolean(focused), type: "normal" });
    if (!Number.isInteger(created?.id)) throw new Error("Chrome did not create the ChatGPT login window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item?.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item?.id)) || null;
    }
    if (!tab?.id) throw new Error("The ChatGPT login window contains no usable tab");

    await chrome.storage.local.set({
      chatgptLoginProbeTabId: tab.id,
      chatgptLoginProbeWindowId: created.id,
      chatgptLoginProbeAdoptable: !userVisible && !focused,
    }).catch(() => {});
    state.bootValidated = false;
    await persist({
      state: "checking",
      confidence: "low",
      strategy: userVisible ? "manual-login-window-created" : "startup-readiness-window-created",
      composer_ready: false,
      document_ready: false,
      checked_at_ms: Date.now(),
      tab_id: tab.id,
      window_id: created.id,
    });
    return { tab, tab_id: tab.id, window_id: created.id, adoptable: !userVisible && !focused, existing: false };
  }

  async function readyForPrewarm() {
    let snapshot = await detect(!state.bootValidated);
    if (snapshot.state === "unknown" && snapshot.strategy === "no-chatgpt-tab") {
      await ensureProbeWindow({ focused: false, userVisible: false });
      return false;
    }
    return snapshot.state === "ready" && snapshot.composer_ready === true;
  }

  async function takeReadyProbe() {
    if (state.takingProbe) return null;
    state.takingProbe = true;
    try {
      const probe = await trackedProbe();
      if (!probe?.tab || !probe.adoptable) return null;
      const detected = await queryDetector(probe.tab_id);
      if (normalizeState(detected?.state) !== "ready" || detected?.composer_ready !== true) return null;
      await clearTrackedProbe();
      return probe.tab;
    } finally {
      state.takingProbe = false;
    }
  }

  async function openLoginWindow() {
    const cached = await cachedSnapshot();
    if (cached?.state === "login_required" && Number.isInteger(cached.window_id) && Number.isInteger(cached.tab_id)) {
      try {
        const tab = await chrome.tabs.get(cached.tab_id);
        const url = tab?.url || tab?.pendingUrl || "";
        if (isChatGptUrl(url) || isAuthLikeUrl(url)) {
          const probe = await trackedProbe();
          if (probe?.tab_id === tab.id) await chrome.storage.local.set({ chatgptLoginProbeAdoptable: false }).catch(() => {});
          await chrome.windows.update(tab.windowId, { focused: true });
          await chrome.tabs.update(tab.id, { active: true }).catch(() => {});
          return { tab_id: tab.id, window_id: tab.windowId, existing: true };
        }
      } catch (_) {}
    }
    const probe = await ensureProbeWindow({ focused: true, userVisible: true });
    return { tab_id: probe.tab_id, window_id: probe.window_id, existing: probe.existing };
  }

  function metadata(snapshot) {
    return {
      chatgpt_login_state: normalizeState(snapshot?.state),
      chatgpt_login_detector_version: "v27",
      chatgpt_login_confidence: String(snapshot?.confidence || "low").slice(0, 20),
      chatgpt_login_strategy: String(snapshot?.strategy || "unknown").slice(0, 120),
      chatgpt_login_composer_ready: snapshot?.composer_ready === true,
      chatgpt_login_checked_at_ms: Number(snapshot?.checked_at_ms || 0) || null,
    };
  }

  state.detect = detect;
  state.readyForPrewarm = readyForPrewarm;
  state.takeReadyProbe = takeReadyProbe;
  state.openLoginWindow = openLoginWindow;
  state.snapshot = cachedSnapshot;

  if (typeof trySendSocket === "function") {
    const baseTrySendSocket = trySendSocket;
    trySendSocket = async payload => {
      if (payload?.type === "extension.status") {
        const snapshot = await detect(false);
        payload = { ...payload, metadata: { ...(payload.metadata || {}), ...metadata(snapshot) } };
      }
      return baseTrySendSocket(payload);
    };
  }

  if (typeof sendSocket === "function") {
    const baseSendSocket = sendSocket;
    sendSocket = async payload => {
      if (payload?.type === "extension.hello") {
        const snapshot = await detect(false);
        payload = { ...payload, metadata: { ...(payload.metadata || {}), ...metadata(snapshot) } };
      }
      return baseSendSocket(payload);
    };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "popup.login.refresh") {
      detect(true)
        .then(async data => {
          if (typeof sendExtensionStatus === "function" && socketReady()) await sendExtensionStatus(false).catch(() => {});
          sendResponse({ ok: true, data });
        })
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    if (message?.type === "popup.login.open") {
      openLoginWindow()
        .then(data => sendResponse({ ok: true, data }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    return false;
  });

  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    const url = changeInfo.url || tab?.url || tab?.pendingUrl || "";
    if (!isChatGptUrl(url) && !isAuthLikeUrl(url)) return;
    if (changeInfo.url || changeInfo.status === "complete") {
      detect(true).then(async snapshot => {
        if (typeof sendExtensionStatus === "function" && socketReady()) await sendExtensionStatus(false).catch(() => {});
        return snapshot;
      }).catch(() => {});
    }
  });

  chrome.tabs.onRemoved.addListener(tabId => {
    chrome.storage.local.get({ chatgptLoginProbeTabId: null, chatgptLoginTabId: null }).then(async stored => {
      if (stored.chatgptLoginProbeTabId === tabId) await clearTrackedProbe();
      if (stored.chatgptLoginTabId === tabId || stored.chatgptLoginProbeTabId === tabId) {
        state.bootValidated = false;
        detect(true).catch(() => {});
      }
    }).catch(() => {});
  });

  chrome.windows.onRemoved.addListener(windowId => {
    chrome.storage.local.get({ chatgptLoginProbeWindowId: null, chatgptLoginWindowId: null }).then(async stored => {
      if (stored.chatgptLoginProbeWindowId === windowId) await clearTrackedProbe();
      if (stored.chatgptLoginWindowId === windowId || stored.chatgptLoginProbeWindowId === windowId) {
        state.bootValidated = false;
        detect(true).catch(() => {});
      }
    }).catch(() => {});
  });
})();
