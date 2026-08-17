(() => {
  const baseHandleServerMessageV13 = handleServerMessage;
  const TEXT_MODELS = ["gpt-5.6-sol", "gpt-5.5"];
  const MINI_MODEL = "gpt-5.5-mini";
  const STATIC_MODELS = TEXT_MODELS.map(id => ({
    id,
    label: id === "gpt-5.6-sol" ? "GPT-5.6 Sol" : "GPT-5.5",
    family: id,
    reasoning: null,
    capabilities: ["text", "vision", "file-understanding"],
    reasoning_efforts: ["low", "medium", "high"],
  }));

  function reasoningLevel(options = {}) {
    const direct = String(options.reasoning_level || "").trim().toLowerCase();
    if (["instant", "medium", "high"].includes(direct)) return direct;
    const effort = String(options.reasoning_effort || "").trim().toLowerCase();
    if (["low", "minimal", "none", "fast", "instant"].includes(effort)) return "instant";
    if (effort === "medium") return "medium";
    if (["high", "xhigh"].includes(effort)) return "high";
    return "";
  }

  function normalizeModel(value) {
    const model = String(value || "gpt-5.6-sol").trim().toLowerCase();
    if (model === MINI_MODEL) return model;
    if (!TEXT_MODELS.includes(model)) throw new Error(`Unsupported text model: ${model}. Use gpt-5.6-sol, gpt-5.5 or ${MINI_MODEL}.`);
    return model;
  }

  async function accountProfileForRouting(force = false) {
    try {
      if (typeof globalThis.chat2apiDetectAccountProfileV20 === "function") {
        const profile = await globalThis.chat2apiDetectAccountProfileV20(force);
        if (profile) return profile;
      }
    } catch (_) {}
    const settings = await config();
    return {
      account_type: String(settings.accountType || "unknown").toLowerCase(),
      confidence: String(settings.accountDetectionConfidence || "low"),
      strategy: String(settings.accountDetectionStrategy || "storage-fallback"),
      detector: "account-v20-storage",
    };
  }

  async function sendWithScript(tabId, message, files) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, message);
      if (response) return response;
    } catch (_) {}
    await chrome.scripting.executeScript({ target: { tabId }, files });
    await sleep(160);
    return chrome.tabs.sendMessage(tabId, message);
  }

  async function ensureModelControllers(tabId) {
    await ensureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content_model_v5.js", "content_model_v7.js", "content_reasoning_v7.js", "content_runtime_log.js"],
      });
    } catch (_) {}
    await sleep(80);
  }

  const probeState = (tabId, model, reasoning) => sendWithScript(
    tabId,
    { type: "chat2api.model.probe.v7", model, reasoning_level: reasoning || "" },
    ["content_model_v7.js"],
  );
  const commitState = (tabId, model, reasoning) => sendWithScript(
    tabId,
    { type: "chat2api.model.commit.v7", model, reasoning_level: reasoning || "" },
    ["content_model_v7.js"],
  );
  const setAutomation = (tabId, active) => sendWithScript(
    tabId,
    { type: "chat2api.model.automation.v7", active },
    ["content_model_v7.js"],
  );
  const prepareFamily = (tabId, model) => sendWithScript(
    tabId,
    { type: "chat2api.model.prepare.v5", model },
    ["content_model_v5.js"],
  );
  const prepareReasoning = (tabId, level) => sendWithScript(
    tabId,
    { type: "chat2api.reasoning.prepare.v7", reasoning_level: level },
    ["content_reasoning_v7.js"],
  );
  const passiveDiscover = tabId => sendWithScript(
    tabId,
    { type: "chat2api.models.discover.v7" },
    ["content_model_v7.js"],
  );

  function routingError(message, options = {}) {
    const error = new Error(String(message || "ChatGPT routing failed"));
    error.code = String(options.code || "chat_routing_failed");
    error.stage = String(options.stage || "chat-routing");
    error.diagnostics = options.diagnostics && typeof options.diagnostics === "object"
      ? options.diagnostics
      : {};
    return error;
  }

  function reasoningRoutingError(response, model, reasoning) {
    const controllerDiagnostics = response?.diagnostics && typeof response.diagnostics === "object"
      ? response.diagnostics
      : {};
    const code = String(response?.code || "reasoning_selection_failed");
    const message = String(response?.error || `Unable to select requested reasoning level: ${reasoning}`);
    return routingError(message, {
      code,
      stage: "reasoning-selection",
      diagnostics: {
        requested_model: model,
        requested_reasoning: reasoning || null,
        reasoning_error_code: code,
        reasoning_error: message,
        reasoning_controller: response?.controller || null,
        reasoning_controller_diagnostics: controllerDiagnostics,
        reasoning_verification: controllerDiagnostics.verification || null,
        page_driver_version: controllerDiagnostics.page_driver_version || null,
      },
    });
  }

  async function sendCanonicalStatus(modelData = null) {
    const settings = await config();
    const tabs = await chatTabs();
    let bound = null;
    if (Number.isInteger(settings.boundTabId)) bound = tabs.find(tab => tab.id === settings.boundTabId) || null;
    const data = modelData || {};
    const currentModel = TEXT_MODELS.includes(data.current_model) ? data.current_model : (TEXT_MODELS.includes(settings.currentModel) ? settings.currentModel : null);
    const currentReasoning = data.current_reasoning || settings.currentReasoning || null;
    return trySendSocket({
      type: "extension.status",
      metadata: {
        extension_version: chrome.runtime.getManifest().version,
        tab_count: tabs.length,
        bound_tab_id: bound?.id || null,
        bound_url: bound?.url || "",
        bound_title: bound?.title || "",
        models: STATIC_MODELS.map(item => ({ ...item, selected: item.id === currentModel })),
        current_model: currentModel,
        current_reasoning: currentReasoning,
        capabilities: [
          "text", "vision", "file-understanding", "image-generation", "model-selection",
          "reasoning-selection", "passive-model-state", "diagnostics", "estimated-token-usage", "extension-runtime-log",
        ],
      },
    });
  }

  discoverModels = async function discoverCanonicalModels(tab, _force = false) {
    const settings = await config();
    if (!tab?.id) {
      return {
        models: STATIC_MODELS,
        current_model: TEXT_MODELS.includes(settings.currentModel) ? settings.currentModel : null,
        current_reasoning: settings.currentReasoning || null,
        selection_strategy: "static-no-ui",
      };
    }
    try {
      await ensureModelControllers(tab.id);
      const response = await passiveDiscover(tab.id);
      if (!response?.ok) throw new Error(response?.error || "Passive model state detection failed");
      const data = response.data || {};
      const currentModel = TEXT_MODELS.includes(data.current_model) ? data.current_model : (TEXT_MODELS.includes(settings.currentModel) ? settings.currentModel : null);
      const currentReasoning = data.current_reasoning || settings.currentReasoning || null;
      await chrome.storage.local.set({
        models: STATIC_MODELS,
        currentModel: currentModel,
        currentReasoning,
        modelsUpdatedAt: Date.now(),
        modelDiscoveryError: "",
        modelDiscoveryStrategy: "passive-no-ui-v7",
      });
      return { ...data, models: STATIC_MODELS, current_model: currentModel, current_reasoning: currentReasoning };
    } catch (error) {
      await chrome.storage.local.set({ modelDiscoveryError: String(error?.message || error) });
      return {
        models: STATIC_MODELS,
        current_model: TEXT_MODELS.includes(settings.currentModel) ? settings.currentModel : null,
        current_reasoning: settings.currentReasoning || null,
        selection_strategy: "static-fallback-no-ui",
      };
    }
  };

  sendExtensionStatus = async function sendExtensionStatusV13(forceModelDiscovery = false) {
    const settings = await config();
    let modelData = null;
    let tab = null;
    if (Number.isInteger(settings.boundTabId)) {
      try { tab = await chrome.tabs.get(settings.boundTabId); } catch (_) {}
    }
    if (forceModelDiscovery && tab?.id) modelData = await discoverModels(tab, true);
    return sendCanonicalStatus(modelData);
  };

  async function persistPrepared(data, model, reasoning) {
    await chrome.storage.local.set({
      models: STATIC_MODELS,
      currentModel: model,
      currentReasoning: reasoning || data?.actual_reasoning || null,
      modelsUpdatedAt: Date.now(),
      lastRequestedModel: model,
      lastRequestedReasoning: reasoning || null,
      lastModelSelectionError: "",
      modelRouterVersion: "0.5.0",
      modelSelectionStrategy: data?.selection_strategy || "",
      lastModelDiagnostics: data || {},
    });
    await sendCanonicalStatus({ current_model: model, current_reasoning: reasoning || data?.actual_reasoning || null });
  }

  async function waitForPassiveFamily(tabId, model, reasoning, timeoutMs = 2200) {
    const deadline = Date.now() + timeoutMs;
    let last = null;
    while (Date.now() < deadline) {
      const response = await probeState(tabId, model, reasoning);
      last = response?.data || null;
      if (response?.ok && last?.family_match && last?.family_trusted) return { ok: true, data: last };
      await sleep(140);
    }
    return { ok: false, data: last };
  }

  async function prepareRequestedState(tab, requestedModel, requestedReasoning) {
    const totalStarted = Date.now();
    const model = normalizeModel(requestedModel);
    if (model === MINI_MODEL) throw new Error("gpt-5.5-mini must use the Free-account routing path");
    const reasoning = requestedReasoning || "";
    await ensureModelControllers(tab.id);

    const probeStarted = Date.now();
    const probe = await probeState(tab.id, model, reasoning);
    const before = probe?.data || {};
    const stateDetectMs = Date.now() - probeStarted;

    if (probe?.ok && before.zero_op) {
      const diagnostics = {
        ...before,
        requested_model: model,
        requested_reasoning: reasoning || null,
        zero_op: true,
        model_switched: false,
        reasoning_switched: false,
        used_click: false,
        selection_strategy: "passive-state-match-zero-op",
        state_detect_ms: before.state_detect_ms ?? stateDetectMs,
        model_selection_ms: 0,
        model_prepare_ms: Date.now() - totalStarted,
      };
      await persistPrepared(diagnostics, model, reasoning);
      return { prepared: false, diagnostics };
    }

    const selectionStarted = Date.now();
    let familyResponse = null;
    let familyRecovery = null;
    let reasoningResponse = null;
    let modelSwitched = false;
    let reasoningSwitched = false;
    let familyUsedClick = false;

    await setAutomation(tab.id, true);
    try {
      if (!(before.family_match && before.family_trusted)) {
        familyResponse = await prepareFamily(tab.id, model);
        modelSwitched = true;
        familyUsedClick = true;
        if (!familyResponse?.ok) {
          familyRecovery = await waitForPassiveFamily(tab.id, model, reasoning);
          if (!familyRecovery.ok) {
            throw new Error(familyResponse?.error || `Unable to select requested ChatGPT model: ${model}`);
          }
        }
      }

      const postFamily = familyRecovery?.data || (await probeState(tab.id, model, reasoning))?.data || before;
      if (reasoning && !(postFamily.reasoning_match && postFamily.reasoning_trusted)) {
        reasoningResponse = await prepareReasoning(tab.id, reasoning);
        if (!reasoningResponse?.ok) throw reasoningRoutingError(reasoningResponse, model, reasoning);
        reasoningSwitched = Boolean(reasoningResponse.data?.reasoning_switched ?? true);
      }

      await commitState(tab.id, model, reasoning);
    } finally {
      await setAutomation(tab.id, false).catch(() => {});
    }

    const afterResponse = await probeState(tab.id, model, reasoning);
    const after = afterResponse?.data || {};
    if (!after.family_match) {
      throw routingError(`ChatGPT model verification failed after selection: requested ${model}, actual ${after.actual_family || "unknown"}`, {
        code: "model_verification_failed",
        stage: "post-selection-verification",
        diagnostics: { ...before, ...after, requested_model: model, requested_reasoning: reasoning || null },
      });
    }
    if (reasoning && !after.reasoning_match) {
      throw routingError(`ChatGPT reasoning verification failed after selection: requested ${reasoning}, actual ${after.actual_reasoning || "unknown"}`, {
        code: "reasoning_verification_failed",
        stage: "post-selection-verification",
        diagnostics: {
          ...before,
          ...after,
          requested_model: model,
          requested_reasoning: reasoning || null,
          reasoning_controller: reasoningResponse?.controller || null,
          reasoning_controller_diagnostics: reasoningResponse?.diagnostics || null,
          reasoning_verification: reasoningResponse?.data?.verification || reasoningResponse?.diagnostics?.verification || null,
          page_driver_version: reasoningResponse?.data?.page_driver_version || reasoningResponse?.diagnostics?.page_driver_version || null,
        },
      });
    }

    const reasoningStrategy = reasoningResponse?.data?.selection_strategy || null;
    const reasoningUsedClick = Boolean(reasoningResponse?.data?.used_click);
    const diagnostics = {
      ...before,
      ...after,
      requested_model: model,
      requested_reasoning: reasoning || null,
      zero_op: false,
      model_switched: modelSwitched,
      reasoning_switched: reasoningSwitched,
      family_used_click: familyUsedClick,
      reasoning_used_click: reasoningUsedClick,
      used_click: familyUsedClick || reasoningUsedClick,
      family_verification_recovered: Boolean(familyRecovery?.ok),
      family_original_error: familyRecovery?.ok ? (familyResponse?.error || null) : null,
      state_detect_ms: before.state_detect_ms ?? stateDetectMs,
      model_selection_ms: Date.now() - selectionStarted,
      model_prepare_ms: Date.now() - totalStarted,
      selection_strategy: modelSwitched
        ? `family-ui-fallback${familyRecovery?.ok ? "+passive-recovery" : ""}${reasoningStrategy ? `+${reasoningStrategy}` : ""}`
        : (reasoningStrategy || "reasoning-only"),
      reasoning_selection_strategy: reasoningStrategy,
      reasoning_controller: reasoningResponse?.controller || null,
      reasoning_page_driver_version: reasoningResponse?.data?.page_driver_version || null,
      reasoning_verification: reasoningResponse?.data?.verification || null,
      reasoning_verification_warning: reasoningResponse?.data?.verification_warning || null,
    };
    await persistPrepared(diagnostics, model, reasoning);
    return { prepared: true, diagnostics };
  }

  async function preflightRequest(tabId, message) {
    const response = await sendWithScript(
      tabId,
      { type: "chat2api.request.preflight", requestId: message.request_id, prompt: message.prompt || "" },
      ["content_request_v2.js", "content_multimodal.js", "content_request_v3.js", "content_multimodal_v4.js", "content_request_v4.js", "content_request_v5.js", "content_runtime_log.js"],
    );
    if (!response?.ok) throw new Error(response?.error || "ChatGPT composer preflight failed");
    return response.data || {};
  }

  async function prepareAttachments(tabId, attachments) {
    if (!Array.isArray(attachments) || !attachments.length) return {};
    const response = await sendWithScript(
      tabId,
      { type: "chat2api.attach.prepare.v4", attachments },
      ["content_multimodal.js", "content_multimodal_v4.js", "content_runtime_log.js"],
    );
    if (!response?.ok) throw new Error(response?.error || "Unable to attach files to ChatGPT");
    return response.data || {};
  }

  handleServerMessage = async function handleCanonicalModelRouting(message) {
    if (message.type !== "chat.request") return baseHandleServerMessageV13(message);
    const logicalRequestedModel = normalizeModel(message.options?.model || "gpt-5.6-sol");
    const logicalRequestedReasoning = reasoningLevel(message.options || {});
    const routingStarted = Date.now();

    let accountProfile = null;
    let effectiveModel = logicalRequestedModel;
    let effectiveReasoning = logicalRequestedReasoning;
    let freeNativeMini = false;

    try {
      if (logicalRequestedModel === MINI_MODEL) {
        accountProfile = await accountProfileForRouting(true);
        freeNativeMini = String(accountProfile?.account_type || "unknown").toLowerCase() === "free";
        if (freeNativeMini) {
          effectiveModel = MINI_MODEL;
          effectiveReasoning = "";
        } else {
          effectiveModel = "gpt-5.5";
          effectiveReasoning = "instant";
        }
      }

      const tab = await resolveTargetTab();
      const tabReadyMs = Date.now() - routingStarted;
      await ensureContent(tab.id);
      const preflightDiagnostics = await preflightRequest(tab.id, message);
      const prepared = freeNativeMini
        ? {
            prepared: false,
            diagnostics: {
              requested_model: MINI_MODEL,
              requested_reasoning: null,
              effective_model: MINI_MODEL,
              effective_reasoning: null,
              account_type: "free",
              account_detection_strategy: accountProfile?.strategy || null,
              account_detection_confidence: accountProfile?.confidence || null,
              free_model_ui_bypassed: true,
              zero_op: true,
              model_switched: false,
              reasoning_switched: false,
              used_click: false,
              selection_strategy: "free-account-default-mini-no-ui-selection",
              model_selection_ms: 0,
              model_prepare_ms: 0,
            },
          }
        : await prepareRequestedState(tab, effectiveModel, effectiveReasoning);
      const attachmentDiagnostics = await prepareAttachments(tab.id, message.attachments || []);
      const diagnostics = {
        ...(prepared.diagnostics || {}),
        ...preflightDiagnostics,
        ...attachmentDiagnostics,
        logical_requested_model: logicalRequestedModel,
        logical_requested_reasoning: logicalRequestedReasoning || null,
        effective_model: effectiveModel,
        effective_reasoning: effectiveReasoning || null,
        mini_route: logicalRequestedModel === MINI_MODEL
          ? (freeNativeMini ? "free-native" : "gpt-5.5-instant-fallback")
          : null,
        account_type: accountProfile?.account_type || prepared.diagnostics?.account_type || null,
        tab_ready_ms: tabReadyMs,
        routing_ms: Date.now() - routingStarted,
        tab_id: tab.id,
      };
      await chrome.storage.local.set({
        lastRequestedModel: logicalRequestedModel,
        lastRequestedReasoning: logicalRequestedReasoning || null,
        lastEffectiveModel: effectiveModel,
        lastEffectiveReasoning: effectiveReasoning || null,
        lastModelSelectionError: "",
        lastModelDiagnostics: diagnostics,
      });
      await trySendSocket({ type: "chat.diagnostics", request_id: message.request_id, diagnostics });
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.request",
        requestId: message.request_id,
        prompt: message.prompt,
        options: {
          ...(message.options || {}),
          model: "chatgpt-web",
          requested_model: logicalRequestedModel,
          requested_reasoning: logicalRequestedReasoning || null,
          effective_model: effectiveModel,
          effective_reasoning: effectiveReasoning || null,
          mini_route: diagnostics.mini_route,
          model_prepared: prepared.prepared,
          model_selection_strategy: diagnostics.selection_strategy,
          chat2api_diagnostics: diagnostics,
        },
      });
      if (!response?.ok) throw new Error(response?.error || "ChatGPT tab rejected the request");
    } catch (error) {
      const text = String(error?.message || error);
      const errorCode = String(error?.code || "chat_routing_failed");
      const errorDiagnostics = {
        ...(error?.diagnostics && typeof error.diagnostics === "object" ? error.diagnostics : {}),
        error_code: errorCode,
        error_message: text,
        error_stage: error?.stage || "chat-routing",
        logical_requested_model: logicalRequestedModel,
        logical_requested_reasoning: logicalRequestedReasoning || null,
        effective_model: effectiveModel,
        effective_reasoning: effectiveReasoning || null,
        account_type: accountProfile?.account_type || null,
        routing_ms: Date.now() - routingStarted,
      };
      await chrome.storage.local.set({
        lastRequestedModel: logicalRequestedModel,
        lastRequestedReasoning: logicalRequestedReasoning || null,
        lastEffectiveModel: effectiveModel,
        lastEffectiveReasoning: effectiveReasoning || null,
        lastModelSelectionError: text,
        lastModelDiagnostics: errorDiagnostics,
      });
      await trySendSocket({ type: "chat.diagnostics", request_id: message.request_id, diagnostics: errorDiagnostics });
      await trySendSocket({ type: "chat.error", request_id: message.request_id, error: text, code: errorCode, diagnostics: errorDiagnostics });
    }
  };
})();