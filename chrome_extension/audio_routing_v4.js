(() => {
  const baseHandleServerMessage = handleServerMessage;

  async function activateAudioTab(tab) {
    if (!tab?.id) return tab;
    let windowFocused = null;
    try {
      if (Number.isInteger(tab.windowId)) windowFocused = Boolean((await chrome.windows.get(tab.windowId))?.focused);
    } catch (_) {}
    // Voice is an interactive foreground WebRTC mode. A merely active tab
    // inside a background window still has document.visibilityState="hidden"
    // and may never request microphone access or create a peer connection.
    const windowId = Number.isInteger(tab.windowId) ? tab.windowId : null;
    let focusError = "";
    if (windowId != null) {
      try {
        const windowInfo = await chrome.windows.get(windowId);
        if (windowInfo?.state === "minimized") {
          await chrome.windows.update(windowId, { state: "normal" });
        }
      } catch (error) { focusError = String(error?.message || error); }
    }
    try { await chrome.tabs.update(tab.id, { active: true }); }
    catch (error) { focusError ||= String(error?.message || error); }
    if (windowId != null) {
      try { await chrome.windows.update(windowId, { focused: true }); }
      catch (error) { focusError ||= String(error?.message || error); }
    }
    await sleep(280);
    const current = await chrome.tabs.get(tab.id).catch(() => tab);
    let windowFocusedAfter = null;
    if (windowId != null) {
      try { windowFocusedAfter = Boolean((await chrome.windows.get(windowId))?.focused); }
      catch (error) { focusError ||= String(error?.message || error); }
    }
    current.chat2apiWindowFocusedBefore = windowFocused;
    current.chat2apiWindowFocusedAfter = windowFocusedAfter;
    current.chat2apiWindowFocusError = focusError;
    return current;
  }

  // Shared by speech and live Voice: one physical foreground activation,
  // without creating a second window-routing or terminal owner.
  globalThis.chat2apiActivateVoiceTabV184 = activateAudioTab;

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
          audio_window_focus_strategy: "voice-foreground-window-v184",
          audio_window_was_focused: tab.chat2apiWindowFocusedBefore,
          audio_window_is_focused: tab.chat2apiWindowFocusedAfter,
          audio_window_focus_error: tab.chat2apiWindowFocusError || null,
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
