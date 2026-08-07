(() => {
  const baseHandleServerMessage = handleServerMessage;
  const baseDiscoverModels = discoverModels;
  const DEFAULT_MODEL_IDS = new Set(["default", "chatgpt-web", ""]);

  async function sendCachedExtensionStatus() {
    const settings = await config();
    const tabs = await chatTabs();
    let bound = null;
    if (Number.isInteger(settings.boundTabId)) {
      bound = tabs.find(tab => tab.id === settings.boundTabId) || null;
    }
    return trySendSocket({
      type: "extension.status",
      metadata: {
        extension_version: chrome.runtime.getManifest().version,
        tab_count: tabs.length,
        bound_tab_id: bound?.id || null,
        bound_url: bound?.url || "",
        bound_title: bound?.title || "",
        models: Array.isArray(settings.models) ? settings.models : [],
        current_model: settings.currentModel || "default",
        capabilities: ["text", "model-selection"],
      },
    });
  }

  sendExtensionStatus = async function sendCachedStatusOnly() {
    return sendCachedExtensionStatus();
  };

  function hasLaunchMarker(value = "") {
    try {
      return Boolean(new URL(value).searchParams.get("chat2api_launch"));
    } catch (_) {
      return false;
    }
  }

  async function waitForMarkedTabBinding(timeoutMs = 12000) {
    const tabs = await chrome.tabs.query({ url: CHATGPT_URLS });
    if (!tabs.some(tab => hasLaunchMarker(tab.pendingUrl || tab.url || ""))) return null;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const settings = await config();
      if (Number.isInteger(settings.boundTabId)) {
        try {
          const tab = await chrome.tabs.get(settings.boundTabId);
          if (isChatGptUrl(tab.url || tab.pendingUrl || "")) return tab;
        } catch (_) {}
      }
      await sleep(200);
    }
    return null;
  }

  async function persistSelectedModel(data, requestedModel) {
    const models = Array.isArray(data?.models) ? data.models : [];
    const currentModel = data?.current_model || requestedModel || "default";
    await chrome.storage.local.set({
      models,
      currentModel,
      modelsUpdatedAt: Date.now(),
      lastRequestedModel: requestedModel || "default",
      lastModelSelectionError: "",
      modelRouterVersion: data?.router_version || "0.3.4",
      modelSelectionStrategy: data?.selection_strategy || "",
    });
    await sendCachedExtensionStatus();
  }

  async function sendModelPrepare(tabId, model) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, {
        type: "chat2api.model.prepare.v5",
        model,
      });
      if (response) return response;
    } catch (_) {}

    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content_model_v5.js"],
    });
    await sleep(180);
    return chrome.tabs.sendMessage(tabId, {
      type: "chat2api.model.prepare.v5",
      model,
    });
  }

  async function sendModelDiscover(tabId) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, {
        type: "chat2api.models.discover.v5",
      });
      if (response) return response;
    } catch (_) {}

    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content_model_v5.js"],
    });
    await sleep(180);
    return chrome.tabs.sendMessage(tabId, {
      type: "chat2api.models.discover.v5",
    });
  }

  discoverModels = async function discoverModelsHybrid(tab, force = false) {
    const settings = await config();
    if (!force && settings.modelsUpdatedAt && Date.now() - Number(settings.modelsUpdatedAt) < 300000) {
      return {
        models: Array.isArray(settings.models) ? settings.models : [],
        current_model: settings.currentModel || "default",
      };
    }
    try {
      await ensureContent(tab.id);
      const response = await sendModelDiscover(tab.id);
      if (!response?.ok) throw new Error(response?.error || "Model discovery failed");
      await persistSelectedModel(response.data || {}, response.data?.current_model || "default");
      return response.data || {};
    } catch (error) {
      await chrome.storage.local.set({ lastModelSelectionError: String(error?.message || error) });
      return baseDiscoverModels(tab, force);
    }
  };

  async function prepareRequestedModel(tab, requestedModel) {
    const model = String(requestedModel || "default").trim().toLowerCase() || "default";

    if (DEFAULT_MODEL_IDS.has(model)) {
      await chrome.storage.local.set({
        lastRequestedModel: model || "default",
        lastModelSelectionError: "",
        modelSelectionStrategy: "default-no-ui",
      });
      return { model: "default", prepared: false, executionModel: "chatgpt-web" };
    }

    const response = await sendModelPrepare(tab.id, model);
    if (!response?.ok) {
      throw new Error(response?.error || `Unable to select requested ChatGPT model: ${model}`);
    }
    await persistSelectedModel(response.data || {}, model);
    return { model, prepared: true, executionModel: "chatgpt-web", data: response.data || {} };
  }

  handleServerMessage = async function handleRequestDrivenModelRouting(message) {
    if (message.type !== "chat.request") {
      return baseHandleServerMessage(message);
    }

    const requestedModel = String(message.options?.model || "default").trim() || "default";
    try {
      const markedTab = await waitForMarkedTabBinding();
      const tab = markedTab || await resolveTargetTab();
      await ensureContent(tab.id);
      const prepared = await prepareRequestedModel(tab, requestedModel);
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.request",
        requestId: message.request_id,
        prompt: message.prompt,
        options: {
          ...(message.options || {}),
          model: prepared.executionModel,
          requested_model: requestedModel,
          model_prepared: prepared.prepared,
          model_selection_strategy: prepared.data?.selection_strategy || (prepared.prepared ? "hybrid" : "default-no-ui"),
        },
      });
      if (!response?.ok) throw new Error(response?.error || "ChatGPT tab rejected the request");
    } catch (error) {
      const text = String(error?.message || error);
      await chrome.storage.local.set({
        lastRequestedModel: requestedModel,
        lastModelSelectionError: text,
      });
      await trySendSocket({
        type: "chat.error",
        request_id: message.request_id,
        error: text,
      });
    }
  };
})();