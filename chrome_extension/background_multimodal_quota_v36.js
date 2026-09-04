(() => {
  const KEY = "__CHAT2API_MULTIMODAL_QUOTA_BACKGROUND_V36__";
  if (globalThis[KEY]) return;

  const REVISION = 91;
  const ALARM_NAME = "chat2api-mini-multimodal-quota-cooldown";
  const MINI_MODEL = "gpt-5.5-mini";
  const MAX_RESET_MS = 31 * 24 * 60 * 60 * 1000;
  const UNPARSED_RETRY_MS = 5 * 60 * 1000;
  const STORAGE_DEFAULTS = {
    miniMultimodalCooldownUntilMs: 0,
    miniMultimodalCooldownDetectedAtMs: 0,
    miniMultimodalCooldownReason: "",
    miniMultimodalCooldownSourceText: "",
    miniMultimodalQuotaLastDetectedAtMs: 0,
    miniMultimodalQuotaLastUnparsed: false,
  };

  const state = {
    loaded: false,
    cooldownUntilMs: 0,
    detectedAtMs: 0,
    reason: "",
    sourceText: "",
    lastDetectedAtMs: 0,
    lastUnparsed: false,
  };

  function normalizedAccountType(value) {
    const type = String(value || "").trim().toLowerCase();
    return ["free", "paid"].includes(type) ? type : "unknown";
  }

  function cooling(now = Date.now()) {
    return Number(state.cooldownUntilMs || 0) > now;
  }

  async function loadState() {
    if (state.loaded) return state;
    const stored = await chrome.storage.local.get(STORAGE_DEFAULTS);
    state.cooldownUntilMs = Number(stored.miniMultimodalCooldownUntilMs || 0);
    state.detectedAtMs = Number(stored.miniMultimodalCooldownDetectedAtMs || 0);
    state.reason = String(stored.miniMultimodalCooldownReason || "");
    state.sourceText = String(stored.miniMultimodalCooldownSourceText || "");
    state.lastDetectedAtMs = Number(stored.miniMultimodalQuotaLastDetectedAtMs || 0);
    state.lastUnparsed = Boolean(stored.miniMultimodalQuotaLastUnparsed);
    state.loaded = true;
    return state;
  }

  async function clearCooldown({ preserveDetection = true } = {}) {
    await loadState();
    state.cooldownUntilMs = 0;
    state.detectedAtMs = 0;
    state.reason = "";
    state.sourceText = "";
    if (!preserveDetection) {
      state.lastDetectedAtMs = 0;
      state.lastUnparsed = false;
    }
    await chrome.storage.local.set({
      miniMultimodalCooldownUntilMs: 0,
      miniMultimodalCooldownDetectedAtMs: 0,
      miniMultimodalCooldownReason: "",
      miniMultimodalCooldownSourceText: "",
      ...(preserveDetection ? {} : {
        miniMultimodalQuotaLastDetectedAtMs: 0,
        miniMultimodalQuotaLastUnparsed: false,
      }),
    });
    try { await chrome.alarms.clear(ALARM_NAME); } catch (_) {}
  }

  async function ensureFresh() {
    await loadState();
    if (state.cooldownUntilMs && state.cooldownUntilMs <= Date.now()) {
      await clearCooldown({ preserveDetection: true });
    }
    return snapshot();
  }

  function snapshot() {
    const active = cooling();
    return {
      revision: REVISION,
      cooling: active,
      available: !active,
      cooldown_until_ms: active ? Number(state.cooldownUntilMs) : 0,
      cooldown_until: active ? new Date(state.cooldownUntilMs).toISOString() : null,
      detected_at_ms: Number(state.detectedAtMs || 0),
      reason: String(state.reason || ""),
      source_text: String(state.sourceText || ""),
      last_detected_at_ms: Number(state.lastDetectedAtMs || 0),
      last_unparsed: Boolean(state.lastUnparsed),
    };
  }

  async function accountTypeNow() {
    let type = normalizedAccountType((await config()).accountType);
    if (type === "free") return type;
    try {
      if (typeof globalThis.chat2apiDetectAccountProfileV20 === "function") {
        const profile = await globalThis.chat2apiDetectAccountProfileV20(true);
        type = normalizedAccountType(profile?.account_type);
      }
    } catch (_) {}
    return type;
  }

  async function activateCooldown(data = {}) {
    await loadState();
    const now = Date.now();
    const detectedAt = Number(data.detected_at_ms || now);
    let recoveryAt = Number(data.recovery_at_ms || 0);
    const sourceText = String(data.source_text || "").replace(/\s+/g, " ").trim().slice(0, 1200);
    state.lastDetectedAtMs = detectedAt;

    const accountType = await accountTypeNow();
    if (accountType !== "free") {
      state.lastUnparsed = !recoveryAt;
      await chrome.storage.local.set({
        miniMultimodalQuotaLastDetectedAtMs: state.lastDetectedAtMs,
        miniMultimodalQuotaLastUnparsed: state.lastUnparsed,
      });
      return { activated: false, reason: "account-not-free", account_type: accountType };
    }

    const parsedDelta = recoveryAt - now;
    const parsed = Number.isFinite(recoveryAt) && parsedDelta > 1000 && parsedDelta <= MAX_RESET_MS;
    if (!parsed) recoveryAt = now + UNPARSED_RETRY_MS;

    state.cooldownUntilMs = Math.round(recoveryAt);
    state.detectedAtMs = detectedAt;
    state.reason = parsed ? "chatgpt-page-file-upload-quota" : "chatgpt-page-file-upload-quota-unparsed";
    state.sourceText = sourceText;
    state.lastUnparsed = !parsed;
    await chrome.storage.local.set({
      miniMultimodalCooldownUntilMs: state.cooldownUntilMs,
      miniMultimodalCooldownDetectedAtMs: state.detectedAtMs,
      miniMultimodalCooldownReason: state.reason,
      miniMultimodalCooldownSourceText: state.sourceText,
      miniMultimodalQuotaLastDetectedAtMs: state.lastDetectedAtMs,
      miniMultimodalQuotaLastUnparsed: state.lastUnparsed,
    });
    try { chrome.alarms.create(ALARM_NAME, { when: state.cooldownUntilMs }); } catch (_) {}
    return { activated: true, account_type: accountType, parsed_recovery: parsed, ...snapshot() };
  }

  function adaptModel(model, status) {
    const item = { ...(model || {}) };
    const capabilities = new Set(Array.isArray(item.capabilities) ? item.capabilities : []);
    if (String(item.id || "").trim().toLowerCase() === MINI_MODEL) capabilities.add("text");
    if (status.cooling) {
      capabilities.delete("vision");
      capabilities.delete("file-understanding");
    } else if (String(item.id || "").trim().toLowerCase() === MINI_MODEL) {
      capabilities.add("vision");
      capabilities.add("file-understanding");
    }
    item.capabilities = [...capabilities];
    item.multimodal_available = !status.cooling;
    item.multimodal_cooldown_until = status.cooldown_until;
    item.multimodal_cooldown_until_ms = status.cooldown_until_ms;
    item.multimodal_cooldown_reason = status.cooling ? status.reason : null;
    item.file_upload_available = !status.cooling;
    item.file_upload_cooldown_until = status.cooldown_until;
    return item;
  }

  async function adaptStatusMetadata(metadata = {}) {
    const status = await ensureFresh();
    const base = { ...(metadata || {}) };
    const accountType = normalizedAccountType(base.account_type);
    if (accountType !== "free") return base;

    const models = Array.isArray(base.models) ? base.models.map(item => adaptModel(item, status)) : [];
    if (!models.some(item => String(item?.id || "").toLowerCase() === MINI_MODEL)) {
      models.push(adaptModel({
        id: MINI_MODEL,
        label: "GPT-5.5 Mini · Free 默认",
        family: MINI_MODEL,
        reasoning: null,
        reasoning_efforts: [],
        capabilities: ["text"],
        selected: true,
        free_default: true,
      }, status));
    }
    base.models = models;

    const capabilities = new Set(Array.isArray(base.capabilities) ? base.capabilities : []);
    capabilities.add("text");
    capabilities.add("mini-multimodal-quota-aware");
    capabilities.add("file-upload-quota-aware-v91");
    if (status.cooling) {
      capabilities.delete("vision");
      capabilities.delete("file-understanding");
    } else {
      capabilities.add("vision");
      capabilities.add("file-understanding");
    }
    base.capabilities = [...capabilities];
    base.mini_multimodal_available = !status.cooling;
    base.mini_multimodal_cooldown_until = status.cooldown_until;
    base.mini_multimodal_cooldown_until_ms = status.cooldown_until_ms;
    base.mini_multimodal_cooldown_detected_at_ms = status.detected_at_ms;
    base.mini_multimodal_cooldown_reason = status.cooling ? status.reason : null;
    base.mini_multimodal_quota_last_detected_at_ms = status.last_detected_at_ms;
    base.mini_multimodal_quota_last_unparsed = status.last_unparsed;
    base.file_upload_available = !status.cooling;
    base.file_upload_quota_cooling = status.cooling;
    base.file_upload_cooldown_until = status.cooldown_until;
    base.file_upload_cooldown_until_ms = status.cooldown_until_ms;
    base.file_upload_quota_detected_at_ms = status.detected_at_ms;
    base.file_upload_quota_reason = status.cooling ? status.reason : null;
    base.file_upload_quota_revision = REVISION;
    return base;
  }

  const baseEnsureContent = ensureContent;
  ensureContent = async function ensureContentWithQuotaDetector(tabId) {
    await baseEnsureContent(tabId);
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.multimodal.quota.ping.v36" });
      if (response?.ok) return;
    } catch (_) {}
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["content_multimodal_quota_v36.js"] });
      await sleep(80);
    } catch (_) {}
  };

  if (typeof trySendSocket === "function") {
    const baseTrySendSocket = trySendSocket;
    trySendSocket = async payload => {
      if (payload?.type === "extension.status") {
        payload = { ...payload, metadata: await adaptStatusMetadata(payload.metadata || {}) };
      }
      return baseTrySendSocket(payload);
    };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.multimodal.quota.v36") return false;
    activateCooldown(message.data || {})
      .then(async result => {
        try { await sendExtensionStatus(false); } catch (_) {}
        sendResponse({ ok: true, data: result });
      })
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });

  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm?.name !== ALARM_NAME) return;
    ensureFresh()
      .then(() => sendExtensionStatus(false))
      .catch(() => {});
  });

  loadState()
    .then(async () => {
      const status = await ensureFresh();
      if (status.cooling) {
        try { chrome.alarms.create(ALARM_NAME, { when: status.cooldown_until_ms }); } catch (_) {}
      }
    })
    .catch(() => {});

  globalThis.chat2apiMiniMultimodalQuotaV36 = {
    revision: REVISION,
    state,
    snapshot,
    ensureFresh,
    activateCooldown,
    adaptStatusMetadata,
  };
  globalThis[KEY] = true;
})();
