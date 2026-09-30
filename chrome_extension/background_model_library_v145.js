(() => {
  const KEY = "__CHAT2API_BACKGROUND_MODEL_LIBRARY_V145__";
  if (globalThis[KEY]) return;

  const REVISION = 145;
  const CHATGPT_URLS = ["https://chatgpt.com/*", "https://www.chatgpt.com/*", "https://chat.openai.com/*"];
  const runtimeNonce = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const state = { timer: null, running: null, lastTrigger: "startup", runtimeNonce };
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

  async function settingsSnapshot() {
    return chrome.storage.local.get({
      clientId: "",
      boundTabId: null,
      socketState: "disconnected",
      socketUpdatedAt: "",
      models: [],
      currentModel: null,
      currentReasoning: null,
      modelValidationState: "pending",
      modelValidationRevision: 0,
      modelValidationTrigger: "",
      modelValidationError: "",
      modelValidationRuntimeNonce: "",
      modelValidationBoundTabId: null,
      modelValidationSocketUpdatedAt: "",
      modelValidationClientId: "",
      modelValidationCandidateCount: 0,
      modelValidationValidatedCount: 0,
      modelValidationUpdatedAt: "",
    });
  }

  function validationFresh(settings) {
    return Boolean(
      settings.modelValidationState === "validated" &&
      Number(settings.modelValidationRevision || 0) >= REVISION &&
      settings.modelValidationRuntimeNonce === runtimeNonce &&
      settings.modelValidationBoundTabId === settings.boundTabId &&
      String(settings.modelValidationSocketUpdatedAt || "") === String(settings.socketUpdatedAt || "") &&
      String(settings.modelValidationClientId || "") === String(settings.clientId || "") &&
      Array.isArray(settings.models) &&
      settings.models.length > 0 &&
      settings.models.every(item => item && item.validated === true && Number(item.validation_revision || 0) >= REVISION)
    );
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
      modelValidationRuntimeNonce: runtimeNonce,
      modelValidationBoundTabId: null,
      modelValidationSocketUpdatedAt: "",
      modelValidationClientId: "",
      modelValidationCandidateCount: 0,
      modelValidationValidatedCount: 0,
      modelValidationUpdatedAt: new Date().toISOString(),
    });
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

  async function publishStatus(forceValidation = false) {
    if (forceValidation) {
      await validateNow("forced-status").catch(() => {});
    }
    const settings = await settingsSnapshot();
    const tabs = await chatTabs();
    let bound = null;
    if (Number.isInteger(settings.boundTabId)) bound = tabs.find(tab => tab.id === settings.boundTabId) || null;
    const fresh = validationFresh(settings);
    const models = fresh ? settings.models : [];
    const currentModel = fresh ? settings.currentModel : null;
    const capabilities = [
      "text", "vision", "file-understanding", "image-generation", "model-selection",
      "reasoning-selection", "worker-model-validation-v145", "diagnostics", "estimated-token-usage",
      "extension-runtime-log",
    ];
    return trySendSocket({
      type: "extension.status",
      metadata: {
        extension_version: chrome.runtime.getManifest().version,
        tab_count: tabs.length,
        bound_tab_id: bound?.id || null,
        bound_url: bound?.url || "",
        bound_title: bound?.title || "",
        models,
        current_model: currentModel,
        current_reasoning: fresh ? (settings.currentReasoning || null) : null,
        model_validation_state: fresh ? "validated" : String(settings.modelValidationState || "pending"),
        model_validation_revision: REVISION,
        model_validation_trigger: settings.modelValidationTrigger || state.lastTrigger,
        model_validation_error: settings.modelValidationError || null,
        model_validation_candidate_count: Number(settings.modelValidationCandidateCount || 0),
        model_validation_validated_count: fresh ? models.length : 0,
        model_validation_updated_at: settings.modelValidationUpdatedAt || null,
        model_library_fresh: fresh,
        capabilities,
        ...(typeof nativeCapacityControlMetadata === "function" ? nativeCapacityControlMetadata() : {}),
      },
    });
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
      await publishStatus(false).catch(() => {});
      try {
        const tab = await targetTab();
        const response = await ensureValidator(tab.id);
        if (!response?.ok) {
          const error = new Error(response?.error || "Worker model validation failed");
          error.code = response?.code || "model_validation_failed";
          throw error;
        }
        const data = response.data || {};
        const models = (Array.isArray(data.models) ? data.models : []).filter(item =>
          item && typeof item === "object" && item.validated === true && Number(item.validation_revision || 0) >= REVISION,
        );
        if (!models.length) throw new Error("Worker model validation found no usable normal models");
        const snapshot = await settingsSnapshot();
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
          modelValidationRuntimeNonce: runtimeNonce,
          modelValidationBoundTabId: tab.id,
          modelValidationSocketUpdatedAt: snapshot.socketUpdatedAt || "",
          modelValidationClientId: snapshot.clientId || "",
          modelValidationUpdatedAt: data.validated_at || new Date().toISOString(),
        });
        await publishStatus(false);
        return { ok: true, models, current_model: data.current_model || null };
      } catch (error) {
        const message = String(error?.message || error);
        const busy = String(error?.code || "") === "model_validation_busy" || /validation (?:deferred|interrupted).*active/i.test(message);
        await chrome.storage.local.set({
          models: [],
          currentModel: null,
          modelsUpdatedAt: Date.now(),
          modelValidationState: busy ? "pending" : "failed",
          modelValidationRevision: REVISION,
          modelValidationTrigger: trigger,
          modelValidationError: message,
          modelValidationValidatedCount: 0,
          modelValidationRuntimeNonce: runtimeNonce,
          modelValidationUpdatedAt: new Date().toISOString(),
        });
        await publishStatus(false).catch(() => {});
        if (busy) {
          schedule("active-request-retry", 2500);
          return { ok: false, deferred: true, error: message };
        }
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

  // Final compatibility boundary: legacy callers may ask to "discover" models,
  // but discovery now means a fresh Worker validation, never a static fallback.
  discoverModels = async function discoverValidatedModelsV145(_tab, force = false) {
    if (force) await validateNow("legacy-discover");
    const settings = await settingsSnapshot();
    return {
      models: validationFresh(settings) ? settings.models : [],
      current_model: validationFresh(settings) ? settings.currentModel : null,
      validation_state: settings.modelValidationState,
      validation_revision: REVISION,
    };
  };
  sendExtensionStatus = async function sendValidatedExtensionStatusV145(forceModelDiscovery = false) {
    return publishStatus(Boolean(forceModelDiscovery));
  };

  state.validateNow = validateNow;
  state.publishStatus = publishStatus;
  state.validationFresh = validationFresh;

  chrome.runtime.onStartup?.addListener?.(() => schedule("browser-startup", 1000));
  chrome.runtime.onInstalled?.addListener?.(() => schedule("worker-install-or-update", 1200));
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes.boundTabId && changes.boundTabId.newValue !== changes.boundTabId.oldValue) {
      markPending("worker-bind").then(() => publishStatus(false)).catch(() => {});
      schedule("worker-bind", 500);
      return;
    }
    if (changes.socketState?.newValue === "connected" && changes.socketState?.oldValue !== "connected") {
      markPending("worker-connect").then(() => publishStatus(false)).catch(() => {});
      schedule("worker-connect", 650);
      return;
    }
    for (const key of ["workerEnabled", "workerMasterEnabled", "connectionEnabled"]) {
      if (changes[key]?.newValue === true && changes[key]?.oldValue !== true) {
        markPending("worker-enable").then(() => publishStatus(false)).catch(() => {});
        schedule("worker-enable", 650);
        return;
      }
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.models.validate.background.v145") return false;
    validateNow(message.trigger || "manual")
      .then(data => sendResponse({ ok: data?.ok !== false, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });

  // Service-worker creation is itself a fresh validation boundary. A runtime
  // nonce also prevents an old persisted catalog from becoming authoritative in
  // the tiny interval before this reset reaches chrome.storage.
  markPending("worker-restart")
    .then(() => publishStatus(false))
    .then(() => schedule("worker-restart", 1000))
    .catch(error => console.warn("chat2api model authority reset", error));
})();
