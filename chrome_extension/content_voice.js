(() => {
  const KEY = "__CHAT2API_VOICE_CONTENT_V1__";
  if (globalThis[KEY]) return;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = {
    active: null,
    mainEvents: new Map(),
  };
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

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); }
    catch (_) {}
  }

  async function waitFor(fn, timeout = 12000, interval = 120) {
    const deadline = Date.now() + timeout;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const value = fn();
        if (value) return value;
      } catch (error) { lastError = error; }
      await delay(interval);
    }
    if (lastError) throw lastError;
    return null;
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea, textarea, [contenteditable='true']")) || null;
  }

  function composerInput() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("#prompt-textarea, textarea, [contenteditable='true']")].find(visible) || null;
  }

  function setText(el, text) {
    el.focus();
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      setter?.call(el, text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand("insertText", false, text);
    if (!(el.textContent || "").trim()) {
      const p = document.createElement("p");
      p.textContent = text;
      el.replaceChildren(p);
    }
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
  }

  function sendButton() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("button")].find(button => {
      if (!visible(button) || button.disabled) return false;
      const label = `${button.getAttribute("aria-label") || ""} ${textOf(button)}`;
      return /send prompt|send message|发送提示|发送消息/i.test(label) || button.dataset.testid === "send-button";
    }) || null;
  }

  function voiceTrigger() {
    const root = composerRoot() || document;
    const candidates = [...root.querySelectorAll("button")].filter(button => visible(button) && !button.disabled).map(button => {
      const label = `${button.getAttribute("aria-label") || ""} ${button.getAttribute("title") || ""} ${textOf(button)}`.trim();
      let score = 0;
      if (/start voice|voice mode|语音模式|开始语音|启动语音/i.test(label)) score += 120;
      if (/voice|语音/i.test(label)) score += 70;
      if (/microphone|dictat|麦克风|听写/i.test(label)) score -= 60;
      const rect = button.getBoundingClientRect();
      if (rect.bottom > innerHeight * 0.55) score += 15;
      return { button, label, score };
    }).filter(item => item.score > 0).sort((a, b) => b.score - a.score);
    return candidates[0] || null;
  }

  function endVoiceButton() {
    return [...document.querySelectorAll("button")].find(button => {
      if (!visible(button)) return false;
      const label = `${button.getAttribute("aria-label") || ""} ${button.getAttribute("title") || ""} ${textOf(button)}`;
      return /end voice|exit voice|close voice|结束语音|退出语音|关闭语音/i.test(label);
    }) || null;
  }

  function assistantNodes() {
    return [...document.querySelectorAll("[data-message-author-role='assistant']")].filter(visible);
  }

  function assistantText(node) {
    if (!node) return "";
    const rich = [...node.querySelectorAll("[data-message-content], .markdown, [class*='markdown']")].filter(visible);
    const source = rich[rich.length - 1] || node;
    return String(source.innerText || source.textContent || "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function pageError() {
    const recent = [...document.querySelectorAll("article[data-testid^='conversation-turn'], [role='alert']")].filter(visible).slice(-4);
    for (const root of recent) {
      const text = textOf(root);
      if (/(出了点问题|发生错误|something went wrong|network error|请重试|please retry)/i.test(text)) return text.slice(0, 500);
    }
    return "";
  }

  async function fetchAudio(spec) {
    const response = await chrome.runtime.sendMessage({ type: "chat2api.attachment.fetch", fileId: spec.file_id });
    if (!response?.ok) throw new Error(response?.error || "Unable to fetch voice input file");
    return response.data || {};
  }

  function postMain(type, active, data = {}) {
    window.postMessage({ source: "chat2api-isolated", type, request_id: active.requestId, ...data }, "*");
  }

  function eventBucket(requestId) {
    if (!state.mainEvents.has(requestId)) state.mainEvents.set(requestId, []);
    return state.mainEvents.get(requestId);
  }

  async function waitMain(requestId, predicate, timeout = 15000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const list = eventBucket(requestId);
      const found = list.find(predicate);
      if (found) return found;
      await delay(100);
    }
    return null;
  }

  window.addEventListener("message", event => {
    if (event.source !== window || event.data?.source !== "chat2api-main") return;
    const requestId = event.data.request_id;
    if (!requestId) return;
    eventBucket(requestId).push(event.data);
    if (eventBucket(requestId).length > 200) eventBucket(requestId).splice(0, 100);
  });

  async function openVoice(active) {
    const trigger = await waitFor(voiceTrigger, 15000, 150);
    if (!trigger) {
      const labels = [...(composerRoot() || document).querySelectorAll("button")].filter(visible).map(button => button.getAttribute("aria-label") || textOf(button)).filter(Boolean).slice(0, 30);
      throw new Error(`ChatGPT Voice button was not found. Visible composer buttons: ${labels.join(" | ")}`);
    }
    trigger.button.click();
    active.voiceTriggerLabel = trigger.label;
    const track = await waitMain(active.requestId, item => item.type === "voice.remote.track", 20000);
    if (!track) {
      const error = pageError();
      throw new Error(error ? `ChatGPT Voice failed to start: ${error}` : "ChatGPT Voice did not expose a remote audio track. The Voice UI may have changed.");
    }
    return track;
  }

  async function sendTypedPrompt(active, prompt) {
    const input = await waitFor(composerInput, 12000, 150);
    if (!input) throw new Error("Voice session is active but the text composer was not found");
    setText(input, prompt);
    await delay(250);
    const button = await waitFor(sendButton, 5000, 100);
    if (button) button.click();
    else input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
  }

  async function waitResponse(active, baselineCount) {
    const timeout = Math.max(30000, Number(active.timeoutSeconds || 180) * 1000);
    const deadline = Date.now() + timeout;
    let transcript = "";
    let stableAt = 0;
    let lastSoundAt = 0;
    let soundStarted = false;
    while (Date.now() < deadline && !active.cancelled) {
      const error = pageError();
      if (error) throw new Error(`ChatGPT Voice UI error: ${error}`);
      const audioEvents = eventBucket(active.requestId);
      for (const item of audioEvents.slice(-20)) {
        if (item.type === "voice.audio.level") {
          soundStarted ||= Boolean(item.speaking);
          if (item.last_sound_at) lastSoundAt = Number(item.last_sound_at);
        }
        if (item.type === "voice.record.error") throw new Error(item.error || "Voice audio recorder failed");
        if (item.type === "voice.input.error") throw new Error(item.error || "Voice input playback failed");
      }
      const nodes = assistantNodes();
      if (nodes.length > baselineCount) {
        const text = assistantText(nodes[nodes.length - 1]);
        if (text && text !== transcript) {
          transcript = text;
          stableAt = Date.now();
        }
      }
      if (transcript && stableAt) {
        const stableFor = Date.now() - stableAt;
        const silentFor = lastSoundAt ? Date.now() - lastSoundAt : 0;
        if ((soundStarted && silentFor > 1400 && stableFor > 700) || (!soundStarted && stableFor > 2800)) break;
      }
      await delay(140);
    }
    if (active.cancelled) throw new Error("Voice request cancelled");
    if (!transcript) throw new Error("Timed out waiting for GPT-Live transcript");
    postMain("voice.record.stop", active);
    const recorded = await waitMain(active.requestId, item => item.type === "voice.record.complete" || item.type === "voice.record.error", 8000);
    if (!recorded) throw new Error("Timed out finalizing GPT-Live audio capture");
    if (recorded.type === "voice.record.error") throw new Error(recorded.error || "GPT-Live audio capture failed");
    return { transcript, recorded, soundStarted, lastSoundAt };
  }

  async function runVoice(message) {
    if (state.active) throw new Error("This ChatGPT tab is already handling a Voice request");
    const active = {
      requestId: message.requestId,
      mode: message.options?.mode || (message.audio ? "conversation" : "speech"),
      timeoutSeconds: message.options?.timeout_seconds || 180,
      cancelled: false,
      voiceTriggerLabel: "",
    };
    state.active = active;
    state.mainEvents.set(active.requestId, []);
    const started = performance.now();
    try {
      let inputData = null;
      if (message.audio?.file_id) inputData = await fetchAudio(message.audio);
      postMain("voice.prepare", active, {
        mode: active.mode,
        input_base64: inputData?.base64 || "",
        input_mime: inputData?.mime_type || message.audio?.mime_type || "",
      });
      await waitMain(active.requestId, item => item.type === "voice.prepared", 4000);
      const baselineCount = assistantNodes().length;
      await openVoice(active);
      await emit({
        type: "image.started",
        kind: "voice",
        request_id: active.requestId,
        diagnostics: {
          route: "chatgpt-voice",
          voice_mode: active.mode,
          voice_trigger_label: active.voiceTriggerLabel,
          voice_prepare_ms: Math.round((performance.now() - started) * 10) / 10,
        },
      });
      if (active.mode === "speech") {
        const prompt = String(message.prompt || "").trim();
        if (!prompt) throw new Error("Voice speech input is empty");
        await sendTypedPrompt(active, prompt);
      } else {
        if (!inputData?.base64) throw new Error("Voice conversation requires an audio input file");
        await delay(700);
        postMain("voice.input.play", active);
        const startedInput = await waitMain(active.requestId, item => item.type === "voice.input.started" || item.type === "voice.input.error", 8000);
        if (!startedInput || startedInput.type === "voice.input.error") throw new Error(startedInput?.error || "Unable to play voice input into GPT-Live");
      }
      const result = await waitResponse(active, baselineCount);
      const diagnostics = {
        route: "chatgpt-voice",
        voice_mode: active.mode,
        voice_trigger_label: active.voiceTriggerLabel,
        remote_track_seen: Boolean(result.recorded.remote_track_seen),
        remote_sound_started: Boolean(result.recorded.sound_started || result.soundStarted),
        audio_capture_ms: result.recorded.duration_ms || null,
        audio_bytes: result.recorded.size || 0,
        transcript_chars: result.transcript.length,
        total_browser_ms: Math.round((performance.now() - started) * 10) / 10,
      };
      await emit({ type: "image.diagnostics", kind: "voice", request_id: active.requestId, diagnostics });
      await emit({
        type: "image.completed",
        kind: "voice",
        request_id: active.requestId,
        voice: {
          transcript: result.transcript,
          b64_json: result.recorded.b64_json,
          mime_type: result.recorded.mime_type,
          size: result.recorded.size,
          duration_ms: result.recorded.duration_ms,
        },
      });
    } finally {
      try { endVoiceButton()?.click(); } catch (_) {}
      postMain("voice.reset", active);
      state.mainEvents.delete(active.requestId);
      if (state.active === active) state.active = null;
    }
  }

  async function cancelVoice(requestId) {
    if (!state.active || state.active.requestId !== requestId) return;
    state.active.cancelled = true;
    try { endVoiceButton()?.click(); } catch (_) {}
    postMain("voice.record.stop", state.active);
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.voice.request") {
      runVoice(message).catch(error => emit({
        type: "image.error",
        kind: "voice",
        request_id: message.requestId,
        error: String(error?.message || error),
      }));
      sendResponse({ ok: true });
      return false;
    }
    if (message.type === "chat2api.voice.cancel") {
      cancelVoice(message.requestId).then(() => sendResponse({ ok: true }));
      return true;
    }
    return false;
  });
})();
