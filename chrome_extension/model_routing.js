(() => {
  const baseHandleServerMessage = handleServerMessage;

  async function persistSelectedModel(data, requestedModel) {
    const models = Array.isArray(data?.models) ? data.models : [];
    const currentModel = data?.current_model || requestedModel || "chatgpt-web";
    await chrome.storage.local.set({
      models,
      currentModel,
      modelsUpdatedAt: Date.now(),
      lastRequestedModel: requestedModel || "chatgpt-web",
      lastModelSelectionError: "",
    });
    await sendExtensionStatus(false);
  }

  async function prepareRequestedModel(tab, requestedModel) {
    const model = String(requestedModel || "chatgpt-web").trim() || "chatgpt-web";
    if (model === "chatgpt-web") return { model, prepared: false };

    const response = await chrome.tabs.sendMessage(tab.id, {
      type: "chat2api.model.select",
      model,
    });
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
      const tab = await resolveTargetTab();
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
