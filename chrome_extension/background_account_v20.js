(() => {
  const CACHE_KEY = "chatgptAccountProfileV20";
  const CACHE_MS = 45000;
  const MINI_MODEL = {
    id: "gpt-5.5-mini",
    label: "GPT-5.5 Mini · Free 默认",
    family: "gpt-5.5-mini",
    reasoning: null,
    capabilities: ["text"],
    reasoning_efforts: [],
    selected: true,
    free_default: true,
  };

  const normalizeType = value => ["free", "paid"].includes(String(value || "").toLowerCase())
    ? String(value).toLowerCase()
    : "unknown";

  async function cachedProfile() {
    const stored = await chrome.storage.local.get({ [CACHE_KEY]: null });
    const value = stored[CACHE_KEY];
    if (!value || typeof value !== "object") return null;
    return { ...value, account_type: normalizeType(value.account_type) };
  }

  async function accountTab() {
    const settings = await config();
    if (Number.isInteger(settings.boundTabId)) {
      try {
        const tab = await chrome.tabs.get(settings.boundTabId);
        if (tab?.id && isChatGptUrl(tab.url || tab.pendingUrl || "")) return tab;
      } catch (_) {}
    }
    try {
      const active = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      const tab = active.find(item => item?.id && isChatGptUrl(item.url || item.pendingUrl || ""));
      if (tab) return tab;
    } catch (_) {}
    const tabs = await chatTabs();
    if (tabs.length === 1) return tabs[0];
    return null;
  }

  async function queryDetector(tabId) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.account.detect.v20" });
      if (response?.ok) return response.data || null;
    } catch (_) {}
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["content_account_v20.js"] });
      await sleep(120);
      const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.account.detect.v20" });
      return response?.ok ? (response.data || null) : null;
    } catch (_) {
      return null;
    }
  }

  async function detectAccountProfileV20(force = false) {
    const cached = await cachedProfile();
    if (!force && cached?.checked_at_ms && Date.now() - Number(cached.checked_at_ms) < CACHE_MS) return cached;

    const tab = await accountTab();
    if (!tab?.id) {
      return cached || {
        account_type: "unknown",
        confidence: "low",
        strategy: "no-chatgpt-tab",
        detector: "account-v20",
        checked_at_ms: Date.now(),
      };
    }

    let latest = null;
    let freeHits = 0;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const result = await queryDetector(tab.id);
      if (result) {
        latest = result;
        const type = normalizeType(result.account_type);
        if (type === "paid" || (type === "free" && result.confidence === "high")) break;
        if (type === "free") {
          freeHits += 1;
          if (freeHits >= 2) break;
        } else {
          freeHits = 0;
        }
      }
      if (attempt < 3) await sleep(320);
    }

    const profile = {
      account_type: normalizeType(latest?.account_type),
      confidence: String(latest?.confidence || "low"),
      strategy: String(latest?.strategy || "detector-no-result"),
      detector: "account-v20",
      model_control_present: Boolean(latest?.model_control_present),
      composer_ready: Boolean(latest?.composer_ready),
      checked_at_ms: Date.now(),
      tab_id: tab.id,
    };
    await chrome.storage.local.set({
      [CACHE_KEY]: profile,
      accountType: profile.account_type,
      accountDetectionStrategy: profile.strategy,
      accountDetectionConfidence: profile.confidence,
    });
    return profile;
  }

  function accountMetadata(profile) {
    return {
      account_type: normalizeType(profile?.account_type),
      account_detection_version: "v20",
      account_detection_strategy: String(profile?.strategy || "unknown").slice(0, 120),
      account_detection_confidence: String(profile?.confidence || "low").slice(0, 20),
      account_model_control_present: Boolean(profile?.model_control_present),
      account_checked_at_ms: Number(profile?.checked_at_ms || Date.now()),
    };
  }

  function adaptStatusMetadata(metadata, profile) {
    const type = normalizeType(profile?.account_type);
    const base = { ...(metadata || {}), ...accountMetadata(profile) };
    const caps = Array.isArray(base.capabilities) ? [...base.capabilities] : [];
    if (!caps.includes("account-plan-detection")) caps.push("account-plan-detection");
    if (type === "free") {
      base.models = [{ ...MINI_MODEL }];
      base.current_model = MINI_MODEL.id;
      base.current_reasoning = null;
      base.capabilities = caps.filter(item => !["model-selection", "reasoning-selection", "passive-model-state"].includes(item));
      if (!base.capabilities.includes("free-account-default-model")) base.capabilities.push("free-account-default-model");
      return base;
    }
    base.capabilities = caps;
    return base;
  }

  globalThis.chat2apiDetectAccountProfileV20 = detectAccountProfileV20;

  const baseFetch = self.fetch.bind(self);
  self.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url || "";
    if (url.includes("/api/extensions/register") && String(init.method || "GET").toUpperCase() === "POST") {
      try {
        const profile = await detectAccountProfileV20(true);
        const body = JSON.parse(String(init.body || "{}"));
        body.metadata = { ...(body.metadata || {}), ...accountMetadata(profile) };
        init = { ...init, body: JSON.stringify(body) };
      } catch (_) {}
    }
    return baseFetch(input, init);
  };

  if (typeof trySendSocket === "function") {
    const baseTrySendSocket = trySendSocket;
    trySendSocket = async payload => {
      if (payload?.type === "extension.status") {
        const profile = await detectAccountProfileV20(false);
        payload = { ...payload, metadata: adaptStatusMetadata(payload.metadata, profile) };
      }
      return baseTrySendSocket(payload);
    };
  }

  if (typeof sendSocket === "function") {
    const baseSendSocket = sendSocket;
    sendSocket = async payload => {
      if (payload?.type === "extension.hello") {
        const profile = await detectAccountProfileV20(true);
        payload = { ...payload, metadata: { ...(payload.metadata || {}), ...accountMetadata(profile) } };
      }
      return baseSendSocket(payload);
    };
  }

  detectAccountProfileV20(false).catch(() => {});
})();
