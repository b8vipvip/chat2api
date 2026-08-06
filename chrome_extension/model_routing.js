(() => {
  const baseHandleServerMessage = handleServerMessage;
  const baseDiscoverModels = discoverModels;

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
        current_model: settings.currentModel || "chatgpt-web",
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

  async function waitForMarkedTabBinding(timeoutMs = 10000) {
    const tabs = await chatTabs();
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
    const currentModel = data?.current_model || requestedModel || "chatgpt-web";
    await chrome.storage.local.set({
      models,
      currentModel,
      modelsUpdatedAt: Date.now(),
      lastRequestedModel: requestedModel || "chatgpt-web",
      lastModelSelectionError: "",
      modelRouterVersion: data?.router_version || "0.3.3",
    });
    await sendCachedExtensionStatus();
  }

  async function sendModelPrepare(tabId, model) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, {
        type: "chat2api.model.prepare.v4",
        model,
      });
      if (response) return response;
    } catch (_) {}

    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content_model_v4.js"],
    });
    await sleep(180);
    return chrome.tabs.sendMessage(tabId, {
      type: "chat2api.model.prepare.v4",
      model,
    });
  }

  discoverModels = async function discoverModelsWithNestedMenu(tab, force = false) {
    const settings = await config();
    if (!force && settings.modelsUpdatedAt && Date.now() - Number(settings.modelsUpdatedAt) < 300000) {
      return {
        models: Array.isArray(settings.models) ? settings.models : [],
        current_model: settings.currentModel || "chatgpt-web",
      };
    }
    try {
      await ensureContent(tab.id);
      const response = await sendModelPrepare(tab.id, settings.currentModel || "chatgpt-web");
      if (!response?.ok) throw new Error(response?.error || "Model discovery failed");
      await persistSelectedModel(response.data || {}, response.data?.current_model || settings.currentModel || "chatgpt-web");
      return response.data || {};
    } catch (error) {
      await chrome.storage.local.set({ lastModelSelectionError: String(error?.message || error) });
      return baseDiscoverModels(tab, force);
    }
  };

  async function prepareRequestedModel(tab, requestedModel) {
    const model = String(requestedModel || "chatgpt-web").trim() || "chatgpt-web";
    if (model === "chatgpt-web") return { model, prepared: false };

    const response = await sendModelPrepare(tab.id, model);
    if (!response?.ok) {
      throw new Error(response?.error || `Unable to select requested ChatGPT model: ${model}`);
    }
    await persistSelectedModel(response.data || {}, model);
    return { model, prepared: true, data: response.data || {} };
  }

  handleServerMessage = async function handleRequestDrivenModelRouting(message) {
    if (message.type !== "chat.request") {
      return baseHandleServerMessage(message);
    }

    const requestedModel = String(message.options?.model || "chatgpt-web").trim() || "chatgpt-web";
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
          model: prepared.prepared ? "chatgpt-web" : requestedModel,
          requested_model: requestedModel,
          model_prepared: prepared.prepared,
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
