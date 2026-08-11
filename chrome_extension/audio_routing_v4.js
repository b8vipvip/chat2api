(() => {
  const baseHandleServerMessage = handleServerMessage;

  async function activateAudioTab(tab) {
    if (!tab?.id) return tab;
    let windowFocused = null;
    try {
      if (Number.isInteger(tab.windowId)) windowFocused = Boolean((await chrome.windows.get(tab.windowId))?.focused);
    } catch (_) {}
    try { await chrome.tabs.update(tab.id, { active: true }); } catch (_) {}
    await sleep(180);
    const current = await chrome.tabs.get(tab.id).catch(() => tab);
    current.chat2apiWindowFocusedBefore = windowFocused;
    return current;
  }

  async function ensureAudioV4(tabId) {
    await ensureContent(tabId);
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["voice_main.js"], world: "MAIN", injectImmediately: true });
    } catch (_) {}
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content_voice_v2.js", "content_voice_fix_v3.js", "content_voice_fix_v4.js", "content_runtime_log.js"],
      });
    } catch (_) {}
    await sleep(180);
  }

  async function resolveAudioTab() {
    const tab = await resolveTargetTab();
    return activateAudioTab(tab);
  }

  async function browserError(requestId, error) {
    await trySendSocket({
      type: "image.error",
      kind: "voice",
      request_id: requestId,
      error: String(error?.message || error),
    });
  }

  handleServerMessage = async function handleAudioServerMessageV4(message) {
    if (message.type !== "voice.request" && message.type !== "voice.cancel") return baseHandleServerMessage(message);

    if (message.type === "voice.cancel") {
      try {
        const tab = await resolveAudioTab();
        await ensureAudioV4(tab.id);
        await chrome.tabs.sendMessage(tab.id, { type: "chat2api.voice.cancel.v2", requestId: message.request_id });
      } catch (error) {
        await trySendSocket({ type: "image.cancelled", kind: "voice", request_id: message.request_id, reason: String(error?.message || error) });
      }
      return;
    }

    try {
      const tab = await resolveAudioTab();
      await ensureAudioV4(tab.id);
      const prepared = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.voice.trigger.prepare.v4",
        requestId: message.request_id,
        timeout_ms: 25000,
      });
      if (!prepared?.ok) throw new Error(prepared?.error || "Voice v4 trigger preflight failed");

      await trySendSocket({
        type: "image.diagnostics",
        kind: "voice",
        request_id: message.request_id,
        diagnostics: {
          audio_router: "audio-routing-v4",
          audio_tab_id: tab.id,
          audio_window_id: tab.windowId ?? null,
          audio_window_focus_strategy: "tab-active-only",
          audio_window_was_focused: tab.chat2apiWindowFocusedBefore,
          voice_trigger_preflight: prepared.data || null,
          stale_automation_draft_cleared: Boolean(prepared.data?.cleanup?.stale_automation_draft_cleared),
          stale_attachments_removed: Number(prepared.data?.cleanup?.stale_attachments_removed || 0),
        },
      });

      const response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.voice.request.v2",
        requestId: message.request_id,
        prompt: message.prompt || "",
        audio: message.audio || null,
        options: message.options || {},
      });
      if (!response?.ok) throw new Error(response?.error || "Voice v2 controller rejected the request after v4 trigger preflight");
    } catch (error) {
      await browserError(message.request_id, error);
    }
  };
})();
