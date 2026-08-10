(() => {
  const baseHandleServerMessage = handleServerMessage;
  const baseSendExtensionStatus = sendExtensionStatus;

  async function activateAudioTab(tab) {
    if (!tab?.id) return tab;
    let windowFocused = null;
    try {
      if (Number.isInteger(tab.windowId)) windowFocused = Boolean((await chrome.windows.get(tab.windowId))?.focused);
    } catch (_) {}
    try { await chrome.tabs.update(tab.id, { active: true }); } catch (_) {}
    await sleep(180);
    try {
      const current = await chrome.tabs.get(tab.id);
      current.chat2apiWindowFocusedBefore = windowFocused;
      return current;
    } catch (_) {
      tab.chat2apiWindowFocusedBefore = windowFocused;
      return tab;
    }
  }

  async function ensureAudioControllers(tabId) {
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
        files: ["content_voice_v2.js"],
      });
    } catch (_) {}
    await sleep(120);
  }

  async function resolveActiveAudioTab() {
    const tab = await resolveTargetTab();
    return activateAudioTab(tab);
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
        const tab = await resolveActiveAudioTab();
        await ensureAudioControllers(tab.id);
        await trySendSocket({
          type: "image.diagnostics",
          kind: "voice",
          request_id: message.request_id,
          diagnostics: {
            audio_tab_id: tab.id,
            audio_window_id: tab.windowId ?? null,
            audio_window_focus_strategy: "tab-active-only",
            audio_window_was_focused: tab.chat2apiWindowFocusedBefore,
          },
        });
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
        const tab = await resolveActiveAudioTab();
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
          "gpt-live",
          "model-selection",
          "diagnostics",
          "estimated-token-usage"
        ],
      },
    });
  };
})();
