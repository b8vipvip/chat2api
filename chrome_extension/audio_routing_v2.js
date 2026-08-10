(() => {
  const baseHandleServerMessage = handleServerMessage;
  const baseSendExtensionStatus = sendExtensionStatus;

  async function ensureAudioControllers(tabId) {
    await ensureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["voice_main.js", "voice_main_v2.js"],
        world: "MAIN",
        injectImmediately: true,
      });
    } catch (_) {}
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content_voice_v2.js", "content_dictation_v3.js"],
      });
    } catch (_) {}
    await sleep(120);
  }

  async function sendBrowserError(requestId, kind, error) {
    await trySendSocket({
      type: "image.error",
      kind,
      request_id: requestId,
      error: String(error?.message || error),
    });
  }

  handleServerMessage = async function handleAudioServerMessageV2(message) {
    if (message.type === "voice.request") {
      try {
        const tab = await resolveTargetTab();
        await ensureAudioControllers(tab.id);
        const response = await chrome.tabs.sendMessage(tab.id, {
          type: "chat2api.voice.request.v2",
          requestId: message.request_id,
          prompt: message.prompt || "",
          audio: message.audio || null,
          options: message.options || {},
        });
        if (!response?.ok) throw new Error(response?.error || "Voice v2 controller rejected the request");
      } catch (error) {
        await sendBrowserError(message.request_id, "voice", error);
      }
      return;
    }

    if (message.type === "voice.cancel") {
      try {
        const tab = await resolveTargetTab();
        await ensureAudioControllers(tab.id);
        await chrome.tabs.sendMessage(tab.id, { type: "chat2api.voice.cancel.v2", requestId: message.request_id });
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

    if (message.type === "dictation.request") {
      try {
        const tab = await resolveTargetTab();
        await ensureAudioControllers(tab.id);
        const response = await chrome.tabs.sendMessage(tab.id, {
          type: "chat2api.dictation.request.v3",
          requestId: message.request_id,
          audio: message.audio || null,
          options: message.options || {},
        });
        if (!response?.ok) throw new Error(response?.error || "Dictation v3 controller rejected the request");
      } catch (error) {
        await sendBrowserError(message.request_id, "dictation", error);
      }
      return;
    }

    if (message.type === "dictation.cancel") {
      try {
        const tab = await resolveTargetTab();
        await ensureAudioControllers(tab.id);
        await chrome.tabs.sendMessage(tab.id, { type: "chat2api.dictation.cancel.v3", requestId: message.request_id });
      } catch (error) {
        await trySendSocket({
          type: "image.cancelled",
          kind: "dictation",
          request_id: message.request_id,
          reason: String(error?.message || error),
        });
      }
      return;
    }

    return baseHandleServerMessage(message);
  };

  sendExtensionStatus = async function sendAudioExtensionStatusV2(forceModelDiscovery = false) {
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
          "dictation",
          "dictation-auto-send",
          "audio-transcription",
          "gpt-live",
          "gpt-dictation",
          "model-selection",
          "diagnostics",
          "estimated-token-usage",
        ],
      },
    });
  };
})();
