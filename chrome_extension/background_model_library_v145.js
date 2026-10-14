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
      modelObservedChoices: [],
    });
  }

  function validationFresh(settings) {
    return Boolean(
      settings.modelValidationState === "validated" &&
      Number(settings.modelValidationRevision || 0) >= REVISION &&
      settings.modelValidationRuntimeNonce === runtimeNonce &&
      // Linux/Windows Worker automation often has no manually bound tab.
      // The validator selects a real ChatGPT tab and saves its ID.  Comparing
      // that ID to null invalidated every otherwise successful discovery.
      (settings.boundTabId == null ||
        settings.modelValidationBoundTabId === settings.boundTabId) &&
      Number.isInteger(settings.modelValidationBoundTabId) &&
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
      modelObservedChoices: [],
    });
  }

  async function ensureValidator(tabId) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.models.validate.v145" });
      if (response) return response;
    } catch (_) {}
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content_model_library_v145.js"] });
    await delay(120);
    try { return await chrome.tabs.sendMessage(tabId, { type: "chat2api.models.validate.v145" }); }
    catch (error) {
      // ChatGPT may reload its content scripts while the picker is inspected.
      // Re-query the current tab and retry once after the new document settles.
      await delay(900);
      const live = await targetTab();
      await chrome.scripting.executeScript({ target: { tabId: live.id }, files: ["content_model_library_v145.js"] }).catch(() => {});
      return chrome.tabs.sendMessage(live.id, { type: "chat2api.models.validate.v145" });
    }
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
        model_observed_choices: Array.isArray(settings.modelObservedChoices) ? settings.modelObservedChoices : [],
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
        const observed = (Array.isArray(data.observed_models) ? data.observed_models : [])
          .filter(item => item && typeof item.id === "string")
          .map(item => ({ id: item.id, label: String(item.label || item.id), source: "chatgpt-model-picker", validated: false, observed_at: item.observed_at || null }));
        await chrome.storage.local.set({ modelObservedChoices: observed });
        if (!models.length) throw new Error(observed.length
          ? "ChatGPT picker choices observed, but no concrete model is selected and verified"
          : "Worker model validation found no usable normal models");
        const snapshot = await settingsSnapshot();
        await chrome.storage.local.set({
          models,
          modelObservedChoices: observed,
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
          // Manual tasks never retry without a new administrator action.
          return { ok: false, deferred: true, error: message };
        }
        // Reattempt after page navigation or a transient Chrome message-port
        // closure instead of leaving the Worker catalog failed indefinitely.
        // Manual tasks never retry without a new administrator action.
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
    // Explicit discovery tasks use model.task.v147; status reads are side-effect free.
    const settings = await settingsSnapshot();
    return {
      models: validationFresh(settings) ? settings.models : [],
      current_model: validationFresh(settings) ? settings.currentModel : null,
      validation_state: settings.modelValidationState,
      validation_revision: REVISION,
    };
  };
  sendExtensionStatus = async function sendValidatedExtensionStatusV145(forceModelDiscovery = false) {
    return publishStatus(false);
  };

  state.validateNow = validateNow;
  state.publishStatus = publishStatus;
  state.validationFresh = validationFresh;

  // v147: only administrators/popup can initiate discovery or verification.
  // Bind/connect/startup only republishes existing evidence, no scheduled probes.
  chrome.runtime.onStartup?.addListener?.(() => publishStatus(false).catch(() => {}));
  chrome.runtime.onInstalled?.addListener?.(() => publishStatus(false).catch(() => {}));

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.models.validate.background.v145") return false;
    validateNow(message.trigger || "manual")
      .then(data => sendResponse({ ok: data?.ok !== false, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });

  // Never auto-discover or auto-validate after an extension restart.
  // Old proof is not fresh until explicitly revalidated; observed models
  // remain available for manual testing, accurately labeled unverified.
  const previousServerMessage = handleServerMessage;
  let activeTask = null;
  handleServerMessage = async function modelTaskCommandV147(message) {
    if (message?.type !== "model.task.v147") return previousServerMessage(message);
    const taskId = String(message.task_id || "");
    if (!/^mtask_[0-9a-f]{32}$/.test(taskId)) return;
    if (activeTask) {
      await trySendSocket({type:"extension.status",metadata:{model_task_report_v147:{
        task_id:taskId,state:"failed",progress:100,error:"Worker model task is already running",
        logs:["Rejected concurrent model task"],models:[],
      }}});
      return;
    }
    activeTask = taskId;
    const logs = [];
    const kind = message.action === "validate" ? "validate" : "discover";
    const log = detail => {
      const line = new Date().toISOString()+" "+String(detail).slice(0,300);
      logs.push(line);
      if(logs.length > 65) logs.shift();
    };
    const report = async (stateName, progress, error = "", models = []) => {
      await trySendSocket({type:"extension.status",metadata:{model_task_report_v147:{
        task_id:taskId,action:kind,state:stateName,progress,error:String(error).slice(0,600),
        models,logs:[...logs],
      }}});
    };
    try {
      log("Worker accepted server-authorized task "+kind);
      await report("running", 10);
      const tab = await targetTab();
      log("Resolved ChatGPT tab "+tab.id+" url="+(tab.url||"").split("?")[0].slice(0,120));
      await report("running", 30);
      if (kind === "discover") {
        const result = await ensureValidator(tab.id);
        if (!result?.ok) throw new Error(result?.error || "ChatGPT picker discovery failed");
        const observed = (Array.isArray(result.data?.observed_models) ? result.data.observed_models : [])
          .filter(row=>row && typeof row.id==="string")
          .map(row=>({id:row.id,label:String(row.label||row.id),source:"chatgpt-picker-manual-v147",validated:false,observed_at:new Date().toISOString()}));
        log("Picker discovery completed: observed="+observed.length);
        await chrome.storage.local.set({
          modelObservedChoices:observed,models:[],currentModel:null,
          modelValidationState:"discovered",modelValidationRevision:REVISION,
          modelValidationError:"",modelValidationTrigger:"admin-manual-discovery",
          modelValidationCandidateCount:observed.length,
          modelValidationValidatedCount:0,modelValidationUpdatedAt:new Date().toISOString(),
          modelValidationRuntimeNonce:runtimeNonce,modelValidationBoundTabId:tab.id,
        });
        await publishStatus(false);
        await report("completed",100,"",observed.map(row=>row.id));
      } else {
        const requested = String(message.model||"").trim().toLowerCase();
        if(!/^[a-z0-9._:-]{1,128}$/.test(requested))throw new Error("Invalid model identifier");
        log("Requested explicit model validation: "+requested);
        const response = await chrome.tabs.sendMessage(tab.id,{type:"chat2api.model.select.v145",model:requested})
          .catch(async()=>{
            await chrome.scripting.executeScript({target:{tabId:tab.id},files:["content_model_library_v145.js"]});
            return chrome.tabs.sendMessage(tab.id,{type:"chat2api.model.select.v145",model:requested});
          });
        if(!response?.ok || response?.data?.validated!==true)
          throw new Error(response?.error||"Worker could not confirm requested model in ChatGPT picker");
        const settings=await settingsSnapshot();
        const observed=Array.isArray(settings.modelObservedChoices)?settings.modelObservedChoices:[];
        const old=Array.isArray(settings.models)?settings.models:[];
        const verified=[
          ...old.filter(row=>row?.validated===true&&Number(row.validation_revision||0)>=REVISION&&row.id!==requested),
          {id:requested,label:observed.find(row=>row.id===requested)?.label||requested,selected:true,
           validated:true,validation_revision:REVISION,validated_at:new Date().toISOString(),
           validation_method:"admin-explicit-picker-selection-v147",capabilities:["text"]},
        ];
        await chrome.storage.local.set({
          models:verified,currentModel:requested,modelValidationState:"validated",
          modelValidationRevision:REVISION,modelValidationError:"",
          modelValidationTrigger:"admin-manual-verify",
          modelValidationCandidateCount:observed.length,modelValidationValidatedCount:verified.length,
          modelValidationRuntimeNonce:runtimeNonce,modelValidationBoundTabId:tab.id,
          modelValidationSocketUpdatedAt:settings.socketUpdatedAt||"",
          modelValidationClientId:settings.clientId||"",
          modelValidationUpdatedAt:new Date().toISOString(),
        });
        log("Explicit model picker selection confirmed: "+requested);
        await publishStatus(false);
        await report("completed",100,"",[requested]);
      }
    } catch (error) {
      const failure = String(error?.message||error);
      log("FAILED: "+failure);
      await chrome.storage.local.set({
        modelValidationState:kind==="discover"?"failed":(await settingsSnapshot()).modelValidationState,
        modelValidationError:failure,
        modelValidationTrigger:"admin-manual-"+kind,
        modelValidationUpdatedAt:new Date().toISOString(),
        ...(kind==="discover"?{modelObservedChoices:[],models:[],currentModel:null,
          modelValidationCandidateCount:0,modelValidationValidatedCount:0}:{}),
      });
      await publishStatus(false).catch(()=>{});
      await report("failed",100,failure);
    } finally {
      activeTask = null;
    }
  };
  publishStatus(false).catch(error=>console.warn("Worker model status",error));
})();
