(() => {
  const KEY = "__CHAT2API_VOICE_LIVE_CONTENT_V2__";
  if (globalThis[KEY]) return;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = {
    active: null,
    mainEvents: [],
    transcriptTimer: null,
    lastUserText: "",
    lastAssistantText: "",
    currentResponseId: null,
  };
  globalThis[KEY] = state;

  function visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function textOf(el) {
    return String(el?.innerText || el?.textContent || "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function labelOf(el) {
    return `${el?.getAttribute?.("aria-label") || ""} ${el?.getAttribute?.("title") || ""} ${textOf(el)}`.replace(/\s+/g, " ").trim();
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea, textarea, [contenteditable='true']")) || null;
  }

  function voiceTrigger() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("button")]
      .filter(button => visible(button) && !button.disabled)
      .map(button => {
        const label = labelOf(button);
        let score = 0;
        if (/启动语音功能|start voice|voice mode|语音模式|开始语音|启动语音/i.test(label)) score += 160;
        if (/voice|语音/i.test(label)) score += 80;
        if (/听写|dictat|microphone|麦克风/i.test(label)) score -= 120;
        return { button, label, score };
      })
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score)[0] || null;
  }

  function voiceUiReady() {
    const orb = [...document.querySelectorAll("[data-testid='voice-floating-orb'], [data-testid*='voice-orb']")].find(visible);
    if (orb) return true;
    const body = textOf(document.body);
    if (/(准备好了，?\s*随时开始|ready when you are|start speaking|可以开始说话)/i.test(body)) return true;
    return Boolean([...document.querySelectorAll("button")].find(button => visible(button) && /(end voice|exit voice|结束语音|退出语音|关闭语音)/i.test(labelOf(button))));
  }

  function endVoiceButton() {
    return [...document.querySelectorAll("button")]
      .filter(visible)
      .find(button => /(end voice|exit voice|结束语音|退出语音|关闭语音)/i.test(labelOf(button))) || null;
  }

  async function waitFor(fn, timeout = 20000, interval = 100) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      try {
        const value = fn();
        if (value) return value;
      } catch (_) {}
      await delay(interval);
    }
    return null;
  }

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); }
    catch (_) {}
  }

  function emitLive(active, liveEvent, data = {}) {
    return emit({
      type: "image.progress",
      kind: "voice-live",
      request_id: active.requestId,
      live_event: liveEvent,
      session_id: active.sessionId,
      ...data,
    });
  }

  function postMain(type, active, data = {}) {
    window.postMessage({
      source: "chat2api-isolated",
      type,
      request_id: active.requestId,
      session_id: active.sessionId,
      ...data,
    }, "*");
  }

  function latestRoleText(role) {
    const nodes = [...document.querySelectorAll(`[data-message-author-role='${role}']`)].filter(visible);
    return nodes.length ? textOf(nodes[nodes.length - 1]) : "";
  }

  function startTranscriptPolling(active) {
    clearInterval(state.transcriptTimer);
    state.lastUserText = latestRoleText("user");
    state.lastAssistantText = latestRoleText("assistant");
    state.transcriptTimer = setInterval(() => {
      if (state.active !== active) return;
      const userText = latestRoleText("user");
      if (userText && userText !== state.lastUserText) {
        state.lastUserText = userText;
        emitLive(active, "transcript.final", { text: userText }).catch(() => {});
      }
      const assistantText = latestRoleText("assistant");
      if (assistantText && assistantText !== state.lastAssistantText) {
        state.lastAssistantText = assistantText;
        if (state.currentResponseId) {
          emitLive(active, "response.text.snapshot", {
            response_id: state.currentResponseId,
            text: assistantText,
          }).catch(() => {});
        }
      }
    }, 250);
  }

  function stopTranscriptPolling() {
    clearInterval(state.transcriptTimer);
    state.transcriptTimer = null;
  }

  async function openVoice(active) {
    const trigger = await waitFor(voiceTrigger, 15000, 120);
    if (!trigger) throw new Error("ChatGPT Voice button was not found");
    trigger.button.click();
    const ready = await waitFor(() => voiceUiReady(), 20000, 120);
    if (!ready) throw new Error("ChatGPT Voice UI did not become ready");
    const remote = await waitFor(
      () => state.mainEvents.find(item => item.type === "voice.live.remote.ready" || item.type === "voice.remote.track"),
      15000,
      80,
    );
    if (!remote) throw new Error("ChatGPT Voice WebRTC remote track did not become ready");
  }

  async function startLive(message) {
    if (state.active) throw new Error("This ChatGPT tab already has an active Live session");
    const active = {
      requestId: message.requestId,
      sessionId: message.sessionId,
      model: message.options?.model || "gpt-live",
      requestedModel: message.options?.requested_model || message.options?.model || "gpt-live",
      stopped: false,
    };
    state.active = active;
    state.mainEvents = [];
    state.currentResponseId = null;
    try {
      postMain("voice.prepare", active, { mode: "live" });
      postMain("voice.live.prepare", active);
      const prepared = await waitFor(() => state.mainEvents.find(item => item.type === "voice.live.prepared"), 5000, 50);
      if (!prepared) throw new Error("Live MAIN-world bridge did not acknowledge preparation");
      await openVoice(active);
      startTranscriptPolling(active);
      await emit({
        type: "image.started",
        kind: "voice-live",
        request_id: active.requestId,
        diagnostics: {
          route: "chatgpt-gpt-live-stream",
          protocol: "pcm16-16k-in-24k-out",
          requested_model: active.requestedModel,
          effective_model: active.model,
        },
      });
      await emitLive(active, "session.ready", { model: active.model, requested_model: active.requestedModel });
    } catch (error) {
      await stopLive(active, false);
      throw error;
    }
  }

  async function stopLive(active = state.active, notify = true) {
    if (!active || active.stopped) return;
    active.stopped = true;
    stopTranscriptPolling();
    postMain("voice.live.stop", active);
    postMain("voice.reset", active);
    try { endVoiceButton()?.click(); } catch (_) {}
    if (notify) await emitLive(active, "session.closed");
    if (state.active === active) state.active = null;
    state.currentResponseId = null;
    state.mainEvents = [];
  }

  window.addEventListener("message", event => {
    if (event.source !== window || event.data?.source !== "chat2api-main") return;
    const item = event.data;
    if (!state.active || item.request_id !== state.active.requestId) return;
    state.mainEvents.push(item);
    if (state.mainEvents.length > 500) state.mainEvents.splice(0, 250);
    const active = state.active;
    if (item.type === "voice.live.input.speech_started") {
      emitLive(active, "input.speech_started").catch(() => {});
    } else if (item.type === "voice.live.input.speech_stopped") {
      emitLive(active, "input.speech_stopped").catch(() => {});
    } else if (item.type === "voice.live.response.started") {
      state.currentResponseId = item.response_id || null;
      state.lastAssistantText = latestRoleText("assistant");
      emitLive(active, "response.created", { response_id: item.response_id }).catch(() => {});
      emitLive(active, "response.audio.started", { response_id: item.response_id }).catch(() => {});
    } else if (item.type === "voice.live.audio") {
      emitLive(active, "response.audio.delta", {
        response_id: item.response_id,
        seq: item.seq,
        sample_rate: item.sample_rate || 24000,
        pcm_base64: item.pcm_base64,
      }).catch(() => {});
    } else if (item.type === "voice.live.response.stopped") {
      const responseId = item.response_id;
      emitLive(active, "response.audio.done", { response_id: responseId }).catch(() => {});
      if (item.reason !== "client_cancel") {
        emitLive(active, "response.done", {
          response_id: responseId,
          text: state.lastAssistantText || "",
        }).catch(() => {});
      }
      if (state.currentResponseId === responseId) state.currentResponseId = null;
    } else if (item.type === "voice.live.response.interrupted") {
      emitLive(active, "response.interrupted", {
        response_id: item.response_id,
        reason: item.reason || "client_cancel",
      }).catch(() => {});
      if (state.currentResponseId === item.response_id) state.currentResponseId = null;
    } else if (item.type === "voice.live.error") {
      emitLive(active, "error", { code: "GPT_LIVE_MEDIA_ERROR", message: item.error || "Live media bridge error" }).catch(() => {});
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.voice.live.start") {
      startLive(message)
        .then(() => sendResponse({ ok: true }))
        .catch(error => {
          emit({
            type: "image.error",
            kind: "voice-live",
            request_id: message.requestId,
            error: String(error?.message || error),
          }).catch(() => {});
          sendResponse({ ok: false, error: String(error?.message || error) });
        });
      return true;
    }
    if (message.type === "chat2api.voice.live.audio") {
      const active = state.active;
      if (!active || active.requestId !== message.requestId) {
        sendResponse({ ok: false, error: "Live session is not active" });
        return false;
      }
      postMain("voice.live.audio.push", active, {
        pcm_base64: message.pcmBase64 || "",
        sample_rate: message.sampleRate || 16000,
      });
      sendResponse({ ok: true });
      return false;
    }
    if (message.type === "chat2api.voice.live.cancel") {
      const active = state.active;
      if (active && active.requestId === message.requestId) postMain("voice.live.cancel", active);
      sendResponse({ ok: true });
      return false;
    }
    if (message.type === "chat2api.voice.live.stop") {
      const active = state.active;
      stopLive(active, false).then(() => sendResponse({ ok: true })).catch(() => sendResponse({ ok: true }));
      return true;
    }
    return false;
  });
})();
