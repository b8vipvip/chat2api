(() => {
  const KEY = "__CHAT2API_DICTATION_CONTENT_V3__";
  if (globalThis[KEY]) return;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { active: null, mainEvents: new Map() };
  globalThis[KEY] = state;

  function visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function textOf(el) {
    return String(el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  }

  function labelOf(el) {
    return `${el?.dataset?.testid || ""} ${el?.getAttribute?.("aria-label") || ""} ${el?.getAttribute?.("title") || ""} ${textOf(el)}`.replace(/\s+/g, " ").trim();
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea, textarea, [contenteditable='true']")) || null;
  }

  function composerInput() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("#prompt-textarea, textarea, [contenteditable='true']")].find(visible) || null;
  }

  function composerText() {
    const input = composerInput();
    if (!input) return "";
    if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) return String(input.value || "").trim();
    return String(input.innerText || input.textContent || "").replace(/\s+/g, " ").trim();
  }

  function userMessageCount() {
    return document.querySelectorAll("[data-message-author-role='user']").length;
  }

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); } catch (_) {}
  }

  async function diagnostic(active, stage, extra = {}) {
    await emit({
      type: "image.diagnostics",
      kind: "dictation",
      request_id: active.requestId,
      diagnostics: { route: "chatgpt-dictation-v3", dictation_stage: stage, ...extra },
    });
  }

  async function fetchAudio(spec) {
    const response = await chrome.runtime.sendMessage({ type: "chat2api.attachment.fetch", fileId: spec.file_id });
    if (!response?.ok) throw new Error(response?.error || "Unable to fetch dictation audio file");
    return response.data || {};
  }

  function postMain(type, active, data = {}) {
    window.postMessage({ source: "chat2api-isolated", type, request_id: active.requestId, ...data }, "*");
  }

  function bucket(requestId) {
    if (!state.mainEvents.has(requestId)) state.mainEvents.set(requestId, []);
    return state.mainEvents.get(requestId);
  }

  async function waitMain(requestId, predicate, timeout = 15000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const found = bucket(requestId).find(predicate);
      if (found) return found;
      await delay(100);
    }
    return null;
  }

  async function waitFor(fn, timeout = 12000, interval = 120) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const value = fn();
      if (value) return value;
      await delay(interval);
    }
    return null;
  }

  window.addEventListener("message", event => {
    if (event.source !== window || event.data?.source !== "chat2api-main") return;
    const requestId = event.data.request_id;
    if (!requestId) return;
    bucket(requestId).push(event.data);
    if (bucket(requestId).length > 250) bucket(requestId).splice(0, 120);
  });

  function dictationTrigger() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("button")]
      .filter(button => visible(button) && !button.disabled)
      .map(button => {
        const label = labelOf(button);
        let score = 0;
        if (/^(听写|dictat(?:e|ion))$/i.test(label.trim())) score += 260;
        if (/听写|dictat/i.test(label)) score += 190;
        if (/microphone|麦克风|话筒/i.test(label)) score += 40;
        if (/启动语音|语音模式|voice mode|start voice/i.test(label) && !/dictat|听写/i.test(label)) score -= 240;
        return { button, label, score };
      })
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score)[0] || null;
  }

  function dictationFinishButton() {
    const root = composerRoot() || document;
    const buttons = [...root.querySelectorAll("button")].filter(button => visible(button) && !button.disabled);
    return buttons.find(button => /(停止听写|结束听写|完成听写|停止录音|stop dictat|done dictat|finish dictat|stop recording)/i.test(labelOf(button))) ||
      buttons.find(button => /stop-button|stop-recording|dictation-stop/i.test(String(button.dataset.testid || ""))) || null;
  }

  function sendButton() {
    const root = composerRoot() || document;
    const buttons = [...root.querySelectorAll("button")].filter(button => visible(button) && !button.disabled);
    return buttons.find(button => button.dataset.testid === "send-button") ||
      buttons.find(button => /(发送提示|发送消息|发送|send prompt|send message|submit)/i.test(labelOf(button))) || null;
  }

  function pageError() {
    const roots = [...document.querySelectorAll("[role='alert'], article[data-testid^='conversation-turn']")].filter(visible).slice(-4);
    for (const root of roots) {
      const text = textOf(root);
      if (/(出了点问题|发生错误|something went wrong|network error|请重试|please retry)/i.test(text)) return text.slice(0, 500);
    }
    return "";
  }

  function visibleComposerButtons() {
    return [...(composerRoot() || document).querySelectorAll("button")]
      .filter(visible).map(labelOf).filter(Boolean).slice(0, 30);
  }

  async function autoSendTranscription(active, text) {
    const input = await waitFor(composerInput, 6000, 100);
    if (!input) throw new Error("Dictation transcription is ready but the ChatGPT composer is unavailable for auto-send");
    const beforeUsers = userMessageCount();
    const current = composerText();
    if (!current || current !== text) throw new Error("Dictation transcription changed before auto-send; refusing to send unexpected text");

    const button = await waitFor(sendButton, 5000, 100);
    let strategy = "enter-key";
    if (button) {
      strategy = button.dataset.testid === "send-button" ? "send-button-testid" : "send-button-label";
      button.click();
    } else {
      input.focus();
      for (const type of ["keydown", "keypress", "keyup"]) {
        input.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
      }
    }
    await diagnostic(active, "send-triggered", { auto_send: true, send_strategy: strategy, transcript_chars: text.length });

    const confirmed = await waitFor(() => {
      const composerCleared = composerText() === "";
      const newUserMessage = userMessageCount() > beforeUsers;
      return (composerCleared || newUserMessage) ? { composerCleared, newUserMessage } : null;
    }, 10000, 120);
    if (!confirmed) {
      const error = pageError();
      throw new Error(error ? `ChatGPT rejected Dictation auto-send: ${error}` : "Dictation transcription was produced but automatic send could not be confirmed");
    }
    await diagnostic(active, "send-confirmed", {
      auto_send: true,
      send_strategy: strategy,
      send_confirmed: true,
      composer_cleared: Boolean(confirmed.composerCleared),
      user_message_observed: Boolean(confirmed.newUserMessage),
    });
    return strategy;
  }

  async function waitTranscription(active, timeoutMs) {
    let text = "";
    let stableAt = 0;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline && !active.cancelled) {
      const error = pageError();
      if (error) throw new Error(`ChatGPT Dictation UI error: ${error}`);
      const current = composerText();
      if (current) {
        if (current !== text) {
          text = current;
          stableAt = Date.now();
        }
        if (stableAt && Date.now() - stableAt > 850) return text;
      }
      await delay(120);
    }
    return "";
  }

  async function runDictation(message) {
    if (state.active) throw new Error("This ChatGPT tab is already handling a Dictation request");
    const active = {
      requestId: message.requestId,
      timeoutSeconds: message.options?.timeout_seconds || 90,
      cancelled: false,
      trigger: null,
      triggerLabel: "",
    };
    state.active = active;
    state.mainEvents.set(active.requestId, []);
    const started = performance.now();

    try {
      await diagnostic(active, "request-received", { auto_send: true });
      if (!message.audio?.file_id) throw new Error("Dictation requires an audio input file");
      const audio = await fetchAudio(message.audio);
      if (!audio?.base64) throw new Error("Dictation audio download returned no data");

      if (composerText()) throw new Error("ChatGPT composer already contains a manual draft; gpt-dictation will not auto-send over existing text");
      postMain("voice.prepare", active, {
        mode: "dictation",
        input_base64: audio.base64,
        input_mime: audio.mime_type || message.audio.mime_type || "audio/mpeg",
      });
      const prepared = await waitMain(active.requestId, item => item.type === "voice.prepared", 5000);
      if (!prepared) throw new Error("MAIN-world audio bridge did not acknowledge Dictation preparation");
      await diagnostic(active, "main-prepared", { audio_bytes: Number(audio.size || 0), auto_send: true });

      const trigger = await waitFor(dictationTrigger, 15000, 150);
      if (!trigger) throw new Error(`ChatGPT Dictation button was not found. Visible composer buttons: ${visibleComposerButtons().join(" | ")}`);
      active.trigger = trigger.button;
      active.triggerLabel = trigger.label;
      await diagnostic(active, "trigger-found", { dictation_trigger_label: trigger.label, auto_send: true });
      trigger.button.click();
      await diagnostic(active, "trigger-clicked", { auto_send: true });

      const mic = await waitMain(active.requestId, item => item.type === "voice.mic.synthetic", 15000);
      if (!mic) {
        const error = pageError();
        throw new Error(error ? `ChatGPT Dictation failed to start: ${error}` : "ChatGPT Dictation did not request microphone audio after the button click");
      }
      await emit({
        type: "image.started",
        kind: "dictation",
        request_id: active.requestId,
        diagnostics: {
          route: "chatgpt-dictation-v3",
          dictation_stage: "recording",
          dictation_trigger_label: active.triggerLabel,
          synthetic_mic_seen: true,
          auto_send: true,
        },
      });

      postMain("voice.input.play", active);
      const inputStarted = await waitMain(active.requestId, item => item.type === "voice.input.started" || item.type === "voice.input.error", 8000);
      if (!inputStarted || inputStarted.type === "voice.input.error") throw new Error(inputStarted?.error || "Unable to play audio into ChatGPT Dictation");
      await diagnostic(active, "input-started", { input_duration_ms: inputStarted.duration_ms || null, auto_send: true });
      const inputEnded = await waitMain(
        active.requestId,
        item => item.type === "voice.input.ended" || item.type === "voice.input.error",
        Math.max(12000, Number(inputStarted.duration_ms || 0) + 8000),
      );
      if (!inputEnded || inputEnded.type === "voice.input.error") throw new Error(inputEnded?.error || "Dictation input playback did not finish");
      await diagnostic(active, "input-ended", { input_duration_ms: inputEnded.duration_ms || inputStarted.duration_ms || null });

      postMain("voice.mic.stop", active);
      const micStopped = await waitMain(active.requestId, item => item.type === "voice.mic.stopped" || item.type === "voice.mic.stop.error", 6000);
      await diagnostic(active, "mic-ended", {
        synthetic_mic_stopped: micStopped?.type === "voice.mic.stopped" && micStopped?.stopped !== false,
        stopped_tracks: micStopped?.stopped_tracks || 0,
        mic_stop_error: micStopped?.type === "voice.mic.stop.error" ? micStopped.error : null,
      });

      let text = await waitTranscription(active, 6500);
      if (!text) {
        const finish = dictationFinishButton();
        if (finish) {
          finish.click();
          await diagnostic(active, "finish-clicked", { finish_button_label: labelOf(finish) });
        } else if (active.trigger && document.contains(active.trigger) && visible(active.trigger)) {
          active.trigger.click();
          await diagnostic(active, "finish-clicked", { finish_button_label: active.triggerLabel, finish_strategy: "trigger-toggle" });
        } else {
          await diagnostic(active, "finish-control-missing", { visible_buttons: visibleComposerButtons() });
        }
        text = await waitTranscription(active, 18000);
      }

      if (active.cancelled) throw new Error("Dictation request cancelled");
      if (!text) {
        throw new Error(`ChatGPT Dictation audio finished but no transcription appeared. Visible composer buttons: ${visibleComposerButtons().join(" | ")}`);
      }
      await diagnostic(active, "transcription-ready", { transcript_chars: text.length, auto_send: true });

      const sendStrategy = await autoSendTranscription(active, text);
      const diagnostics = {
        route: "chatgpt-dictation-v3",
        dictation_stage: "completed",
        dictation_trigger_label: active.triggerLabel,
        synthetic_mic_seen: true,
        synthetic_mic_stopped: true,
        input_duration_ms: inputEnded.duration_ms || inputStarted.duration_ms || null,
        transcript_chars: text.length,
        auto_send: true,
        send_confirmed: true,
        send_strategy: sendStrategy,
        total_browser_ms: Math.round((performance.now() - started) * 10) / 10,
      };
      await emit({ type: "image.diagnostics", kind: "dictation", request_id: active.requestId, diagnostics });
      await emit({ type: "image.completed", kind: "dictation", request_id: active.requestId, text, sent: true });
    } finally {
      postMain("voice.reset", active);
      state.mainEvents.delete(active.requestId);
      if (state.active === active) state.active = null;
    }
  }

  async function cancelDictation(requestId) {
    if (!state.active || state.active.requestId !== requestId) return;
    state.active.cancelled = true;
    postMain("voice.mic.stop", state.active);
    postMain("voice.reset", state.active);
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.dictation.request.v3") {
      runDictation(message).catch(error => emit({
        type: "image.error",
        kind: "dictation",
        request_id: message.requestId,
        error: String(error?.message || error),
      }));
      sendResponse({ ok: true, controller: "dictation-v3", auto_send: true });
      return false;
    }
    if (message.type === "chat2api.dictation.cancel.v3") {
      cancelDictation(message.requestId).then(() => sendResponse({ ok: true }));
      return true;
    }
    return false;
  });
})();
