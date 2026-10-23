(() => {
  const baseHandleServerMessage = handleServerMessage;
  const liveTabs = new Map();

  async function ensureLiveContent(tabId) {
    await ensureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["voice_main.js", "voice_live_main.js"],
        world: "MAIN",
        injectImmediately: true,
      });
    } catch (_) {}
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content_voice_ui_gate_v185.js", "content_voice_live.js", "content_voice_live_text_v24.js"],
      });
    } catch (_) {}
    await sleep(120);
  }

  async function reportInputError(requestId, itemId, error) {
    await trySendSocket({
      type: "image.progress",
      kind: "voice-live",
      request_id: requestId,
      live_event: "input.error",
      item_id: itemId || "",
      message: String(error?.message || error),
    });
  }

  async function reportError(requestId, error) {
    await trySendSocket({
      type: "image.error",
      kind: "voice-live",
      request_id: requestId,
      error: String(error?.message || error),
    });
  }

  async function resolveLiveWorkerTab(message) {
    const routed = globalThis.chat2apiResolveRoutedWorkerTabV24;
    if (message?.routing?.api_key_id && typeof routed === "function") {
      return routed(message);
    }
    return resolveTargetTab();
  }

  handleServerMessage = async function handleLiveVoiceServerMessage(message) {
    const type = String(message?.type || "");
    if (!type.startsWith("voice.live.")) return baseHandleServerMessage(message);
    const requestId = String(message.request_id || "");
    if (!requestId) return;

    if (type === "voice.live.start") {
      try {
        const tab = await resolveLiveWorkerTab(message);
        if (!tab?.id) throw new Error("No ChatGPT tab is available for GPT-Live");
        // Reuse the same physical foreground activation as speech requests.
        // Do not silently leave WebRTC Voice in a background/hidden document.
        const activate = globalThis.chat2apiActivateVoiceTabV184;
        if (typeof activate !== "function") throw new Error("Shared Voice foreground activation is unavailable");
        const activeTab = await activate(tab);
        if (activeTab.chat2apiWindowFocusedAfter === false) {
          throw new Error("VOICE_WINDOW_NOT_FOCUSED: Live Voice browser window cannot be focused");
        }
        await ensureLiveContent(tab.id);
        liveTabs.set(requestId, tab.id);

        // Starting ChatGPT Voice can take many seconds while the page establishes
        // WebRTC. Worker allocation above is serialized, but after the tab is bound
        // we deliberately do not hold that dispatch lock for the WebRTC startup.
        chrome.tabs.sendMessage(tab.id, {
          type: "chat2api.voice.live.start",
          requestId,
          sessionId: message.session_id,
          options: message.options || {},
        }).then(response => {
          if (!response?.ok) throw new Error(response?.error || "GPT-Live tab rejected the live session");
        }).catch(error => {
          liveTabs.delete(requestId);
          reportError(requestId, error).catch(() => {});
        });
      } catch (error) {
        liveTabs.delete(requestId);
        await reportError(requestId, error);
      }
      return;
    }

    const tabId = liveTabs.get(requestId);
    if (!Number.isInteger(tabId)) {
      await reportError(requestId, new Error("GPT-Live tab binding was lost"));
      return;
    }

    try {
      if (type === "voice.live.audio") {
        const response = await chrome.tabs.sendMessage(tabId, {
          type: "chat2api.voice.live.audio",
          requestId,
          pcmBase64: message.pcm_base64 || "",
          sampleRate: message.sample_rate || 16000,
        });
        if (!response?.ok) throw new Error(response?.error || "Live audio chunk was rejected");
      } else if (type === "voice.live.text" || type === "voice.live.multimodal") {
        const response = await chrome.tabs.sendMessage(tabId, {
          type: type === "voice.live.multimodal" ? "chat2api.voice.live.multimodal" : "chat2api.voice.live.text",
          requestId,
          itemId: message.item_id || "",
          text: message.text || "",
          attachments: message.attachments || [],
        });
        if (!response?.ok) throw new Error(response?.error || "Live text/media input was rejected");
      } else if (type === "voice.live.cancel_response") {
        await chrome.tabs.sendMessage(tabId, { type: "chat2api.voice.live.cancel", requestId });
      } else if (type === "voice.live.stop") {
        try { await chrome.tabs.sendMessage(tabId, { type: "chat2api.voice.live.stop", requestId }); }
        finally { liveTabs.delete(requestId); }
      }
    } catch (error) {
      if (type === "voice.live.stop") liveTabs.delete(requestId);
      if (type === "voice.live.text" || type === "voice.live.multimodal") {
        // An unsupported attachment or failed composer turn must not
        // terminate a live phone call or release its Worker lease.
        await reportInputError(requestId, message.item_id, error);
      } else {
        await reportError(requestId, error);
      }
    }
  };
})();
