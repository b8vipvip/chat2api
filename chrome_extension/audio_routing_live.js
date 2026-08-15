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
        files: ["content_voice_live.js", "content_voice_live_text_v24.js"],
      });
    } catch (_) {}
    await sleep(120);
  }

  async function reportError(requestId, error) {
    await trySendSocket({
      type: "image.error",
      kind: "voice-live",
      request_id: requestId,
      error: String(error?.message || error),
    });
  }

  handleServerMessage = async function handleLiveVoiceServerMessage(message) {
    const type = String(message?.type || "");
    if (!type.startsWith("voice.live.")) return baseHandleServerMessage(message);
    const requestId = String(message.request_id || "");
    if (!requestId) return;

    if (type === "voice.live.start") {
      try {
        const tab = await resolveTargetTab();
        if (!tab?.id) throw new Error("No ChatGPT tab is available for GPT-Live");
        try { await chrome.tabs.update(tab.id, { active: true }); } catch (_) {}
        await ensureLiveContent(tab.id);
        liveTabs.set(requestId, tab.id);

        // Starting ChatGPT Voice can take many seconds while the page establishes
        // WebRTC. Do not hold the global routed-dispatch chain for that whole time;
        // the tab binding is already fixed, so other worker tabs may start requests.
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
      } else if (type === "voice.live.text") {
        const response = await chrome.tabs.sendMessage(tabId, {
          type: "chat2api.voice.live.text",
          requestId,
          itemId: message.item_id || "",
          text: message.text || "",
        });
        if (!response?.ok) throw new Error(response?.error || "Live text input was rejected");
      } else if (type === "voice.live.cancel_response") {
        await chrome.tabs.sendMessage(tabId, { type: "chat2api.voice.live.cancel", requestId });
      } else if (type === "voice.live.stop") {
        try { await chrome.tabs.sendMessage(tabId, { type: "chat2api.voice.live.stop", requestId }); }
        finally { liveTabs.delete(requestId); }
      }
    } catch (error) {
      if (type === "voice.live.stop") liveTabs.delete(requestId);
      await reportError(requestId, error);
    }
  };
})();
