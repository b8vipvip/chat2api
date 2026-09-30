(() => {
  const KEY = "__CHAT2API_BACKGROUND_MODEL_LIBRARY_V145__";
  if (globalThis[KEY]) return;

  const REVISION = 145;
  const CHATGPT_URLS = ["https://chatgpt.com/*", "https://www.chatgpt.com/*", "https://chat.openai.com/*"];
  const state = { timer: null, running: null, lastTrigger: "startup" };
  globalThis[KEY] = state;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  function isChatGptUrl(value = "") {
    try { return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname); }
    catch (_) { return false; }
  }

  async function targetTab() {
    const settings = await chrome.storage.local.get({ boundTabId: null });
    if (Number.isInteger(settings.boundTabId)) {
      try {
        const tab = await chrome.tabs.get(settings.boundTabId);
        if (tab?.id && isChatGptUrl(tab.url || tab.pendingUrl || "")) return tab;
      } catch (_) {}
    }
    const tabs = await chrome.tabs.query({ url: CHATGPT_URLS });
    const candidates = tabs.filter(tab => Number.isInteger(tab.id) && isChatGptUrl(tab.url || tab.pendingUrl || ""));
    if (!candidates.length) throw new Error("No ChatGPT tab is available for Worker model validation");
    return candidates[0];
  }

  async function publishStatus() {
    try {
      if (typeof sendExtensionStatus === "function") {
        await sendExtensionStatus(false);
        return;
      }
    } catch (_) {}
  }

  async function markPending(trigger) {
    await chrome.storage.local.set({
      models: [],
      currentModel: null,
      modelsUpdatedAt: 0,
      modelValidationState: "pending",
      modelValidationRevision: REVISION,
      modelValidationTrigger: trigger,
      modelValidationError: "",
      modelValidationUpdatedAt: new Date().toISOString(),
    });
    await publishStatus();
  }

  async function ensureValidator(tabId) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.models.validate.v145" });
      if (response) return response;
    } catch (_) {}
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content_model_library_v145.js"] });
    await delay(120);
    return chrome.tabs.sendMessage(tabId, { type: "chat2api.models.validate.v145" });
  }

  async function validateNow(trigger = "manual") {
    if (state.running) return state.running;
    state.lastTrigger = trigger;
    state.running = (async () => {
      await markPending(trigger);
      await chrome.storage.local.set({
        modelValidationState: "validating",
        modelValidationStartedAt: new Date().toISOString(),
      });
      try {
        const tab = await targetTab();
        const response = await ensureValidator(tab.id);
        if (!response?.ok) throw new Error(response?.error || "Worker model validation failed");
        const data = response.data || {};
        const models = (Array.isArray(data.models) ? data.models : []).filter(item =>
          item && typeof item === "object" && item.validated === true && Number(item.validation_revision || 0) >= REVISION,
        );
        await chrome.storage.local.set({
          models,
          currentModel: data.current_model || null,
          modelsUpdatedAt: Date.now(),
          modelValidationState: "validated",
          modelValidationRevision: REVISION,
          modelValidationTrigger: trigger,
          modelValidationError: "",
          modelValidationCandidateCount: Number(data.candidate_count || models.length),
          modelValidationValidatedCount: models.length,
          modelValidationUpdatedAt: data.validated_at || new Date().toISOString(),
        });
        await publishStatus();
        return { ok: true, models, current_model: data.current_model || null };
      } catch (error) {
        const message = String(error?.message || error);
        await chrome.storage.local.set({
          models: [],
          currentModel: null,
          modelsUpdatedAt: Date.now(),
          modelValidationState: "failed",
          modelValidationRevision: REVISION,
          modelValidationTrigger: trigger,
          modelValidationError: message,
          modelValidationValidatedCount: 0,
          modelValidationUpdatedAt: new Date().toISOString(),
        });
        await publishStatus();
        throw error;
      }
    })().finally(() => { state.running = null; });
    return state.running;
  }

  function schedule(trigger, delayMs = 700) {
    state.lastTrigger = trigger;
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      validateNow(trigger).catch(error => console.warn("chat2api Worker model validation", error));
    }, Math.max(0, delayMs));
  }

  chrome.runtime.onStartup?.addListener?.(() => schedule("browser-startup", 1000));
  chrome.runtime.onInstalled?.addListener?.(() => schedule("worker-install-or-update", 1200));
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes.boundTabId && changes.boundTabId.newValue !== changes.boundTabId.oldValue) {
      schedule("worker-bind", 500);
      return;
    }
    if (changes.socketState?.newValue === "connected" && changes.socketState?.oldValue !== "connected") {
      schedule("worker-connect", 650);
      return;
    }
    for (const key of ["workerEnabled", "workerMasterEnabled", "connectionEnabled"]) {
      if (changes[key]?.newValue === true && changes[key]?.oldValue !== true) {
        schedule("worker-enable", 650);
        return;
      }
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.models.validate.background.v145") return false;
    validateNow(message.trigger || "manual")
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });

  // Service-worker creation is itself a restart boundary. Clear any persisted
  // normal-model authority immediately, then repopulate it only after validation.
  markPending("worker-restart")
    .then(() => schedule("worker-restart", 1000))
    .catch(error => console.warn("chat2api model authority reset", error));
})();
