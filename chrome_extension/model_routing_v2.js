(() => {
  const baseHandleServerMessage = handleServerMessage;
  const LIBRARY_REVISION = 145;
  const SPECIAL_MODELS = new Set(["gpt-image", "gpt-live", "gpt-live-mini"]);

  function canonicalModel(value) {
    const raw = String(value || "").trim().toLowerCase().replace(/\s+/g, "-");
    if (!raw || ["default", "chatgpt-web"].includes(raw)) return "";
    if (!/^[a-z0-9._:-]{1,128}$/.test(raw)) throw new Error(`Invalid model id: ${value}`);
    return raw;
  }

  function reasoningLevel(options = {}) {
    const direct = String(options.reasoning_level || "").trim().toLowerCase().replace(/_/g, "-");
    if (["instant", "low", "medium", "high", "extra-high", "xhigh"].includes(direct)) {
      return direct === "xhigh" ? "extra-high" : direct === "low" ? "instant" : direct;
    }
    const effort = String(options.reasoning_effort || "").trim().toLowerCase().replace(/_/g, "-");
    if (["low", "minimal", "none", "fast", "instant"].includes(effort)) return "instant";
    if (effort === "medium") return "medium";
    if (["high", "extended"].includes(effort)) return "high";
    if (["xhigh", "extra-high", "extra high"].includes(effort)) return "extra-high";
    return "";
  }

  async function validatedLibrary() {
    const settings = await chrome.storage.local.get({
      models: [], currentModel: null, modelValidationState: "pending", modelValidationRevision: 0,
    });
    const rows = [];
    const seen = new Set();
    if (settings.modelValidationState === "validated" && Number(settings.modelValidationRevision || 0) >= LIBRARY_REVISION) {
      for (const raw of Array.isArray(settings.models) ? settings.models : []) {
        if (!raw || raw.validated !== true || Number(raw.validation_revision || 0) < LIBRARY_REVISION) continue;
        const id = canonicalModel(raw.id || raw.model || raw.family);
        if (!id || SPECIAL_MODELS.has(id) || seen.has(id)) continue;
        seen.add(id);
        rows.push({ ...raw, id });
      }
    }
    return { settings, rows };
  }

  async function resolveRequestedModel(value) {
    const requested = canonicalModel(value);
    const { settings, rows } = await validatedLibrary();
    if (!rows.length) throw new Error("Worker model library is not validated yet");
    if (!requested) {
      const current = canonicalModel(settings.currentModel);
      if (current && rows.some(item => item.id === current)) return current;
      const selected = rows.find(item => item.selected)?.id;
      return selected || rows[0].id;
    }
    if (!rows.some(item => item.id === requested)) {
      throw new Error(`Requested model is not validated on this Worker: ${requested}`);
    }
    return requested;
  }

  async function sendWithScript(tabId, message, files) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, message);
      if (response) return response;
    } catch (_) {}
    await chrome.scripting.executeScript({ target: { tabId }, files });
    await sleep(140);
    return chrome.tabs.sendMessage(tabId, message);
  }

  async function selectValidatedModel(tabId, model) {
    await ensureContent(tabId);
    const response = await sendWithScript(
      tabId,
      { type: "chat2api.model.select.v145", model },
      ["content_model_library_v145.js"],
    );
    if (!response?.ok || response?.data?.validated !== true) {
      throw new Error(response?.error || `Worker failed to select validated model: ${model}`);
    }
    return response.data || {};
  }

  async function prepareReasoning(tabId, reasoning) {
    if (!reasoning) return { ok: true, skipped: true };
    const legacyLevel = reasoning === "extra-high" ? "high" : reasoning;
    const response = await sendWithScript(
      tabId,
      { type: "chat2api.reasoning.prepare.v7", reasoning_level: legacyLevel },
      ["content_reasoning_v7.js"],
    );
    if (!response?.ok) throw new Error(response?.error || `Unable to select requested reasoning level: ${reasoning}`);
    return response.data || {};
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

  async function persistPrepared(model, reasoning, diagnostics) {
    await chrome.storage.local.set({
      currentModel: model,
      currentReasoning: reasoning || null,
      lastRequestedModel: model,
      lastRequestedReasoning: reasoning || null,
      lastEffectiveModel: model,
      lastEffectiveReasoning: reasoning || null,
      lastModelSelectionError: "",
      lastModelDiagnostics: diagnostics || {},
      modelRouterVersion: "v145-dynamic-library",
    });
    if (typeof sendExtensionStatus === "function") await sendExtensionStatus(false).catch(() => {});
  }

  discoverModels = async function discoverValidatedModels(_tab, force = false) {
    const authority = globalThis.__CHAT2API_BACKGROUND_MODEL_LIBRARY_V145__;
    if (force && typeof authority?.validateNow === "function") await authority.validateNow("routing-discover");
    const { settings, rows } = await validatedLibrary();
    return {
      models: rows,
      current_model: rows.some(item => item.id === settings.currentModel) ? settings.currentModel : null,
      current_reasoning: settings.currentReasoning || null,
      validation_state: settings.modelValidationState,
      validation_revision: Number(settings.modelValidationRevision || 0),
      selection_strategy: "worker-validated-library-v145",
    };
  };

  sendExtensionStatus = async function sendDynamicModelStatus(forceModelDiscovery = false) {
    const authority = globalThis.__CHAT2API_BACKGROUND_MODEL_LIBRARY_V145__;
    if (typeof authority?.publishStatus === "function") return authority.publishStatus(Boolean(forceModelDiscovery));
    const settings = await config();
    const tabs = await chatTabs();
    let bound = null;
    if (Number.isInteger(settings.boundTabId)) bound = tabs.find(tab => tab.id === settings.boundTabId) || null;
    const { rows } = await validatedLibrary();
    return trySendSocket({
      type: "extension.status",
      metadata: {
        extension_version: chrome.runtime.getManifest().version,
        tab_count: tabs.length,
        bound_tab_id: bound?.id || null,
        bound_url: bound?.url || "",
        bound_title: bound?.title || "",
        models: rows,
        current_model: rows.some(item => item.id === settings.currentModel) ? settings.currentModel : null,
        model_validation_state: settings.modelValidationState || "pending",
        model_validation_revision: Number(settings.modelValidationRevision || 0),
        capabilities: ["text", "vision", "file-understanding", "image-generation", "model-selection", "reasoning-selection", "diagnostics"],
      },
    });
  };

  handleServerMessage = async function handleDynamicModelRouting(message) {
    if (message?.type !== "chat.request") return baseHandleServerMessage(message);
    const routingStarted = Date.now();
    let requestedModel = null;
    let requestedReasoning = reasoningLevel(message.options || {});
    try {
      requestedModel = await resolveRequestedModel(message.options?.model || message.model || "");
      const tab = await resolveTargetTab();
      const tabReadyMs = Date.now() - routingStarted;
      await ensureContent(tab.id);
      const preflightDiagnostics = await preflightRequest(tab.id, message);
      const selected = await selectValidatedModel(tab.id, requestedModel);
      const reasoningDiagnostics = await prepareReasoning(tab.id, requestedReasoning);
      const attachmentDiagnostics = await prepareAttachments(tab.id, message.attachments || []);
      const diagnostics = {
        ...preflightDiagnostics,
        ...attachmentDiagnostics,
        model_library_revision: LIBRARY_REVISION,
        model_library_authority: "worker-validated",
        logical_requested_model: requestedModel,
        logical_requested_reasoning: requestedReasoning || null,
        effective_model: requestedModel,
        effective_reasoning: requestedReasoning || null,
        model_selection_validated: true,
        model_selection_current: selected.current_model || selected.model || requestedModel,
        reasoning_selection: reasoningDiagnostics,
        tab_ready_ms: tabReadyMs,
        routing_ms: Date.now() - routingStarted,
        tab_id: tab.id,
      };
      await persistPrepared(requestedModel, requestedReasoning, diagnostics);
      await trySendSocket({ type: "chat.diagnostics", request_id: message.request_id, diagnostics });
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.request",
        requestId: message.request_id,
        prompt: message.prompt,
        attachments: message.attachments || [],
        options: {
          ...(message.options || {}),
          // The browser UI has already been switched and verified above. Keep
          // legacy content handlers from running their old static model picker;
          // v144 MAIN-world authority still rewrites the outgoing request to the
          // server-authoritative transport model.
          model: "chatgpt-web",
          requested_model: requestedModel,
          effective_model: requestedModel,
          requested_reasoning: requestedReasoning || null,
          effective_reasoning: requestedReasoning || null,
          model_prepared: true,
          model_selection_strategy: "worker-model-library-v145",
          chat2api_diagnostics: diagnostics,
        },
      });
      if (!response?.ok) throw new Error(response?.error || "ChatGPT tab rejected the request");
    } catch (error) {
      const text = String(error?.message || error);
      const diagnostics = {
        error_code: String(error?.code || "chat_routing_failed"),
        error_message: text,
        error_stage: error?.stage || "worker-model-library-routing",
        logical_requested_model: requestedModel,
        logical_requested_reasoning: requestedReasoning || null,
        effective_model: requestedModel,
        effective_reasoning: requestedReasoning || null,
        model_library_revision: LIBRARY_REVISION,
        routing_ms: Date.now() - routingStarted,
      };
      await chrome.storage.local.set({
        lastRequestedModel: requestedModel,
        lastRequestedReasoning: requestedReasoning || null,
        lastEffectiveModel: requestedModel,
        lastEffectiveReasoning: requestedReasoning || null,
        lastModelSelectionError: text,
        lastModelDiagnostics: diagnostics,
      });
      await trySendSocket({ type: "chat.diagnostics", request_id: message.request_id, diagnostics });
      await trySendSocket({ type: "chat.error", request_id: message.request_id, error: text, code: diagnostics.error_code, diagnostics });
    }
  };
})();
