(() => {
  const KEY = "__CHAT2API_EXTERNAL_WARM_BOOTSTRAP_V28__";
  if (globalThis[KEY]) return;

  const LOGIN_KEY = "__CHAT2API_LOGIN_READINESS_V27__";
  const WARM_POOL_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";
  const AFFINITY_STATE_KEY = "__CHAT2API_MODEL_AFFINITY_V23__";
  const WARM_STORAGE_KEY = "chat2apiConversationWarmPoolV2";
  const AFFINITY_STORAGE_KEY = "chat2apiModelAffinityV23";
  const TRACKING_DEFAULTS = {
    chatgptExternalWarmTabIdV28: null,
    chatgptExternalWarmWindowIdV28: null,
    chatgptExternalWarmCreatedAtV28: 0,
  };

  const state = {
    ensureInFlight: null,
    adoptInFlight: null,
    ensureTimer: null,
    poolPatched: false,
  };
  globalThis[KEY] = state;
  const createManagedWindow = (options, reason) => typeof globalThis.chat2apiCreateWindowStaggered === "function"
    ? globalThis.chat2apiCreateWindowStaggered(options, { reason })
    : chrome.windows.create(options);

  function validChatGptTab(tab) {
    if (!Number.isInteger(tab?.id)) return false;
    const url = tab.url || tab.pendingUrl || "";
    return isChatGptUrl(url);
  }

  async function eligible() {
    const stored = await chrome.storage.local.get({
      socketState: "disconnected",
      networkExternalReady: false,
      clientId: "",
      clientToken: "",
    }).catch(() => ({}));
    if (!stored.clientId || !stored.clientToken) return false;
    if (stored.socketState !== "connected" || stored.networkExternalReady !== true) return false;
    if (typeof socketReady === "function" && !socketReady()) return false;
    return true;
  }

  async function rawTracking() {
    return chrome.storage.local.get(TRACKING_DEFAULTS).catch(() => TRACKING_DEFAULTS);
  }

  async function trackedBootstrap() {
    const stored = await rawTracking();
    const tabId = stored.chatgptExternalWarmTabIdV28;
    const windowId = stored.chatgptExternalWarmWindowIdV28;
    if (!Number.isInteger(tabId) || !Number.isInteger(windowId)) return null;
    try {
      const tab = await chrome.tabs.get(tabId);
      if (!validChatGptTab(tab)) return null;
      return {
        tab,
        tab_id: tab.id,
        window_id: tab.windowId,
        created_at_ms: Number(stored.chatgptExternalWarmCreatedAtV28 || 0) || Date.now(),
      };
    } catch (_) {
      return null;
    }
  }

  async function clearTrackedBootstrap() {
    await chrome.storage.local.set(TRACKING_DEFAULTS).catch(() => {});
  }

  async function validStoredWarmTab() {
    const pool = globalThis[WARM_POOL_KEY];
    for (const warm of pool?.warmSlots?.values?.() || []) {
      if (!Number.isInteger(warm?.tab_id)) continue;
      try {
        const tab = await chrome.tabs.get(warm.tab_id);
        if (validChatGptTab(tab)) return tab;
      } catch (_) {}
    }

    const stored = await chrome.storage.local.get({ [WARM_STORAGE_KEY]: null }).catch(() => ({}));
    const slots = Array.isArray(stored?.[WARM_STORAGE_KEY]?.slots) ? stored[WARM_STORAGE_KEY].slots : [];
    for (const warm of slots) {
      if (!Number.isInteger(warm?.tab_id)) continue;
      try {
        const tab = await chrome.tabs.get(warm.tab_id);
        if (validChatGptTab(tab)) return tab;
      } catch (_) {}
    }
    return null;
  }

  async function reportLogin(force = true) {
    const login = globalThis[LOGIN_KEY];
    let snapshot = null;
    if (typeof login?.detect === "function") {
      snapshot = await login.detect(force).catch(() => null);
    }
    if (typeof sendExtensionStatus === "function" && typeof socketReady === "function" && socketReady()) {
      await sendExtensionStatus(false).catch(() => {});
    }
    return snapshot;
  }

  async function createBootstrapWindow() {
    const existing = await trackedBootstrap();
    if (existing) return existing;
    const warm = await validStoredWarmTab();
    if (warm) return { warm_exists: true, tab: warm, tab_id: warm.id, window_id: warm.windowId };

    const createdAt = Date.now();
    const created = await createManagedWindow({ url: "https://chatgpt.com/", focused: false, type: "normal" }, "external-warm-bootstrap");
    if (!Number.isInteger(created?.id)) throw new Error("Chrome did not create the external-network ChatGPT warm window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item?.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item?.id)) || null;
    }
    if (!tab?.id) throw new Error("The external-network ChatGPT warm window contains no usable tab");

    await chrome.storage.local.set({
      chatgptExternalWarmTabIdV28: tab.id,
      chatgptExternalWarmWindowIdV28: created.id,
      chatgptExternalWarmCreatedAtV28: createdAt,
      chatgptExternalWarmLastReasonV28: "socket-connected+external-network",
      chatgptExternalWarmOpenedAtV28: createdAt,
    }).catch(() => {});

    await reportLogin(true);
    return { tab, tab_id: tab.id, window_id: created.id, created_at_ms: createdAt, created: true };
  }

  async function accountType() {
    const stored = await chrome.storage.local.get({ accountType: "unknown" }).catch(() => ({}));
    const value = String(stored.accountType || "unknown").toLowerCase();
    return ["free", "paid"].includes(value) ? value : "unknown";
  }

  async function desiredDefinition() {
    const type = await accountType();
    const affinityApi = globalThis.chat2apiModelAffinityV23;
    const affinityState = globalThis[AFFINITY_STATE_KEY];
    const stored = await chrome.storage.local.get({ [AFFINITY_STORAGE_KEY]: null }).catch(() => ({}));
    let presets = Array.isArray(affinityState?.presets) && affinityState.presets.length
      ? [...affinityState.presets]
      : (Array.isArray(stored?.[AFFINITY_STORAGE_KEY]?.presets) ? stored[AFFINITY_STORAGE_KEY].presets : []);
    if (typeof affinityApi?.presetsForAccount === "function") {
      presets = affinityApi.presetsForAccount(presets, type);
    }
    const preset = presets.find(item => item?.key) || null;
    return {
      slot_key: preset ? `affinity:${preset.key}` : "generic",
      preset,
      account_type: type,
    };
  }

  async function persistWarmPool(adopted) {
    const pool = globalThis[WARM_POOL_KEY];
    const stored = await chrome.storage.local.get({ [WARM_STORAGE_KEY]: null }).catch(() => ({}));
    const merged = new Map();
    const currentStored = Array.isArray(stored?.[WARM_STORAGE_KEY]?.slots) ? stored[WARM_STORAGE_KEY].slots : [];
    for (const item of currentStored) {
      if (item?.slot_key) merged.set(String(item.slot_key), { ...item });
    }
    for (const item of pool?.warmSlots?.values?.() || []) {
      if (item?.slot_key) merged.set(String(item.slot_key), { ...item });
    }
    if (adopted?.slot_key) merged.set(String(adopted.slot_key), { ...adopted });
    await chrome.storage.local.set({
      [WARM_STORAGE_KEY]: { version: 23, slots: [...merged.values()] },
    }).catch(() => {});
  }

  async function adoptReadyBootstrap() {
    if (state.adoptInFlight) return state.adoptInFlight;
    state.adoptInFlight = (async () => {
      if (!await eligible()) return null;
      const tracked = await trackedBootstrap();
      if (!tracked) return null;

      const login = globalThis[LOGIN_KEY];
      let snapshot = typeof login?.snapshot === "function" ? await login.snapshot().catch(() => null) : null;
      if (!(snapshot?.state === "ready" && snapshot?.composer_ready === true) && typeof login?.detect === "function") {
        snapshot = await login.detect(false).catch(() => snapshot);
      }
      if (!(snapshot?.state === "ready" && snapshot?.composer_ready === true)) return null;

      const pool = globalThis[WARM_POOL_KEY];
      if (!pool?.warmSlots || typeof pool.warmSlots.set !== "function") return null;
      const definition = await desiredDefinition();
      const existing = pool.warmSlots.get(definition.slot_key);
      if (existing && Number.isInteger(existing.tab_id) && existing.tab_id !== tracked.tab_id) {
        try {
          const tab = await chrome.tabs.get(existing.tab_id);
          if (validChatGptTab(tab)) {
            try { await chrome.windows.remove(tracked.window_id); } catch (_) {}
            await clearTrackedBootstrap();
            return existing;
          }
        } catch (_) {}
      }

      const affinity = globalThis.chat2apiModelAffinityV23;
      const prepareStarted = Date.now();
      let prepared = { ok: true, verified: false, strategy: "generic-ready" };
      if (definition.preset && typeof affinity?.prepareTab === "function") {
        prepared = await affinity.prepareTab(tracked.tab_id, definition.preset, definition.account_type).catch(error => ({
          ok: false,
          error: String(error?.message || error),
        }));
        if (!prepared?.ok) {
          await chrome.storage.local.set({
            chatgptExternalWarmAdoptErrorV28: prepared?.error || "Unable to prepare model affinity on bootstrap window",
            chatgptExternalWarmAdoptErrorAtV28: Date.now(),
          }).catch(() => {});
          return null;
        }
      }

      const adopted = {
        slot_key: definition.slot_key,
        tab_id: tracked.tab_id,
        window_id: tracked.window_id,
        created_at_ms: tracked.created_at_ms,
        ready_at_ms: Date.now(),
        load_ms: Math.max(0, Date.now() - tracked.created_at_ms),
        preset_prepare_ms: Date.now() - prepareStarted,
        strategy: definition.preset ? "external-network-bootstrap-affinity" : "external-network-bootstrap-generic",
        account_type: definition.account_type,
        model_picker_ready: definition.account_type !== "free",
        preset_key: definition.preset?.key || null,
        preset_model: definition.preset?.model || null,
        preset_reasoning: definition.preset?.reasoning || null,
        preset_count: Number(definition.preset?.count || 0),
        preset_verified: Boolean(prepared?.verified),
        effective_model: prepared?.effective_model || definition.preset?.model || null,
        effective_reasoning: prepared?.effective_reasoning || definition.preset?.reasoning || null,
      };

      pool.warmSlots.set(definition.slot_key, adopted);
      await persistWarmPool(adopted);
      await clearTrackedBootstrap();
      await chrome.storage.local.set({
        chatgptExternalWarmAdoptedAtV28: Date.now(),
        chatgptExternalWarmAdoptedSlotV28: definition.slot_key,
        chatgptExternalWarmAdoptErrorV28: "",
      }).catch(() => {});
      return adopted;
    })().finally(() => { state.adoptInFlight = null; });
    return state.adoptInFlight;
  }

  function patchWarmPool() {
    const pool = globalThis[WARM_POOL_KEY];
    if (!pool || typeof pool.onAffinityChanged !== "function") return false;
    if (pool.external_warm_bootstrap_v28 === true) return true;
    const base = pool.onAffinityChanged.bind(pool);
    pool.onAffinityChanged = async (...args) => {
      await adoptReadyBootstrap().catch(() => {});
      return base(...args);
    };
    pool.external_warm_bootstrap_v28 = true;
    state.poolPatched = true;
    return true;
  }

  async function ensureNow() {
    if (state.ensureInFlight) return state.ensureInFlight;
    state.ensureInFlight = (async () => {
      if (!await eligible()) return null;
      patchWarmPool();
      const bootstrap = await createBootstrapWindow();
      await adoptReadyBootstrap().catch(() => {});
      return bootstrap;
    })().catch(async error => {
      await chrome.storage.local.set({
        chatgptExternalWarmErrorV28: String(error?.message || error),
        chatgptExternalWarmErrorAtV28: Date.now(),
      }).catch(() => {});
      return null;
    }).finally(() => { state.ensureInFlight = null; });
    return state.ensureInFlight;
  }

  function scheduleEnsure(delayMs = 0) {
    clearTimeout(state.ensureTimer);
    state.ensureTimer = setTimeout(() => {
      state.ensureTimer = null;
      ensureNow().catch(() => {});
    }, Math.max(0, Number(delayMs || 0)));
  }

  state.ensure = ensureNow;
  state.adopt = adoptReadyBootstrap;
  state.reportLogin = reportLogin;

  patchWarmPool();

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.socketState?.newValue === "connected" || changes.networkExternalReady?.newValue === true) {
      scheduleEnsure(0);
    }
    if (changes.chatgptLoginState || changes.chatgptLoginComposerReady) {
      adoptReadyBootstrap().catch(() => {});
    }
  });

  chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    if (!changeInfo.url && changeInfo.status !== "complete") return;
    trackedBootstrap().then(tracked => {
      if (!tracked || tracked.tab_id !== tabId) return;
      reportLogin(true).then(() => adoptReadyBootstrap()).catch(() => {});
    }).catch(() => {});
  });

  chrome.tabs.onRemoved.addListener(tabId => {
    rawTracking().then(async stored => {
      if (stored.chatgptExternalWarmTabIdV28 !== tabId) return;
      await clearTrackedBootstrap();
      if (await eligible()) scheduleEnsure(350);
    }).catch(() => {});
  });

  setTimeout(() => {
    patchWarmPool();
    scheduleEnsure(0);
  }, 0);
})();
