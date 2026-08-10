(() => {
  const baseHandleServerMessage = handleServerMessage;
  const baseSendExtensionStatus = sendExtensionStatus;

  async function ensureVoiceContent(tabId) {
    await ensureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["voice_main.js"],
        world: "MAIN",
        injectImmediately: true,
      });
    } catch (_) {}
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content_guard.js", "content_voice.js"],
      });
    } catch (_) {}
    await sleep(120);
  }

  handleServerMessage = async function handleVoiceServerMessage(message) {
    if (message.type === "voice.request") {
      try {
        const tab = await resolveTargetTab();
        await ensureVoiceContent(tab.id);
        const response = await chrome.tabs.sendMessage(tab.id, {
          type: "chat2api.voice.request",
          requestId: message.request_id,
          prompt: message.prompt || "",
          audio: message.audio || null,
          options: message.options || {},
        });
        if (!response?.ok) throw new Error(response?.error || "ChatGPT Voice tab rejected the request");
      } catch (error) {
        await trySendSocket({
          type: "image.error",
          kind: "voice",
          request_id: message.request_id,
          error: String(error?.message || error),
        });
      }
      return;
    }
    if (message.type === "voice.cancel") {
      try {
        const tab = await resolveTargetTab();
        await ensureVoiceContent(tab.id);
        await chrome.tabs.sendMessage(tab.id, { type: "chat2api.voice.cancel", requestId: message.request_id });
      } catch (error) {
        await trySendSocket({
          type: "image.cancelled",
          kind: "voice",
          request_id: message.request_id,
          reason: String(error?.message || error),
        });
      }
      return;
    }
    return baseHandleServerMessage(message);
  };

  sendExtensionStatus = async function sendVoiceExtensionStatus(forceModelDiscovery = false) {
    await baseSendExtensionStatus(forceModelDiscovery);
    await trySendSocket({
      type: "extension.status",
      metadata: {
        capabilities: [
          "text",
          "vision",
          "file-understanding",
          "image-generation",
          "voice-generation",
          "voice-conversation",
          "gpt-live",
          "model-selection",
          "diagnostics",
          "estimated-token-usage",
        ],
      },
    });
  };
})();
