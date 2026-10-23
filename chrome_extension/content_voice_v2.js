(() => {
  const KEY = "__CHAT2API_VOICE_CONTENT_V2__";
  if (globalThis[KEY]) return;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { active: null, mainEvents: new Map(), mainSequence: 0 };
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
    return `${el?.getAttribute?.("aria-label") || ""} ${el?.getAttribute?.("title") || ""} ${textOf(el)}`.replace(/\s+/g, " ").trim();
  }

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); }
    catch (_) {}
  }

  async function diagnostic(active, stage, extra = {}) {
    active.lastStage = stage;
    await emit({
      type: "image.diagnostics",
      kind: "voice",
      request_id: active.requestId,
      diagnostics: {
        route: "chatgpt-voice-v2",
        voice_stage: stage,
        voice_mode: active.mode,
        ...extra,
      },
    });
  }

  function voiceFailure(code, message) {
    const error = new Error(code + ": " + message);
    error.code = code;
    return error;
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
    const buttons = [...root.querySelectorAll("button")].filter(button => visible(button) && !button.disabled);
    return buttons.find(button => button.dataset.testid === "send-button") ||
      buttons.find(button => /send prompt|send message|发送提示|发送消息/i.test(labelOf(button))) ||
      buttons.find(button => {
        const rect = button.getBoundingClientRect();
        const label = labelOf(button);
        return rect.bottom > innerHeight * 0.55 && /submit|arrow-up|发送/i.test(label + " " + (button.innerHTML || ""));
      }) || null;
  }

  function voiceTrigger() {
    const root = composerRoot() || document;
    const candidates = [...root.querySelectorAll("button")]
      .filter(button => visible(button) && !button.disabled)
      .map(button => {
        const label = labelOf(button);
        let score = 0;
        if (/启动语音功能|start voice|voice mode|语音模式|开始语音|启动语音/i.test(label)) score += 160;
        if (/voice|语音/i.test(label)) score += 80;
        if (/听写|dictat|microphone|麦克风/i.test(label)) score -= 120;
        if (/(end voice|exit voice|结束语音|退出语音|关闭语音)/i.test(label)) score = -10000;
        if (button.querySelector("svg") && button.getBoundingClientRect().bottom > innerHeight * 0.55) score += 10;
        return { button, label, score };
      })
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score);
    return candidates[0] || null;
  }

  function voiceUiReady() {
    const orb = [...document.querySelectorAll("[data-testid='voice-floating-orb'], [data-testid*='voice-orb']")].find(visible);
    if (orb) return { reason: "voice-orb" };
    const exit = [...document.querySelectorAll("button")].find(button => visible(button) && /(end voice|exit voice|结束语音|退出语音|关闭语音)/i.test(labelOf(button)));
    if (exit) return { reason: "voice-exit-button" };
    return null;
  }

  function endVoiceButton() {
    const buttons = [...document.querySelectorAll("button")].filter(visible);
    return buttons.find(button => /(end voice|exit voice|结束语音|退出语音|关闭语音)/i.test(labelOf(button))) ||
      buttons.find(button => {
        const label = labelOf(button);
        const html = button.innerHTML || "";
        return /close|stop/i.test(label) && /voice/i.test(label + html);
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
    // Ignore callbacks belonging to a Voice request that already ended.
    // Otherwise a late blob-completion event recreates an abandoned bucket
    // and holds its Base64 audio in this long-lived content script.
    if (!requestId || state.active?.requestId !== requestId) return;
    eventBucket(requestId).push({ ...event.data, _voice_sequence: ++state.mainSequence });
    if (eventBucket(requestId).length > 300) eventBucket(requestId).splice(0, 150);
  });

  async function settleVoiceWelcome(active) {
    const gate = globalThis.__CHAT2API_VOICE_UI_GATE_V185__;
    let clicked = false;
    // Give the first-use Meet Voice modal time to appear after Start Voice.
    // The isolated controller owns the single click for this request.
    const deadline = Date.now() + 3400;
    while (Date.now() < deadline) {
      const failure = gate?.connectionFailure?.();
      if (failure) {
        await diagnostic(active, "chatgpt-voice-connection-failed", {
          voice_failure_code: failure.code, chatgpt_voice_error:failure.message,
        });
        throw voiceFailure(failure.code, failure.message);
      }
      const welcome = gate?.welcomeDialog?.();
      if (welcome && !clicked) {
        const action = gate.continueWelcome();
        clicked = Boolean(action?.clicked);
        if (clicked) await diagnostic(active, "voice-welcome-continued", {
          voice_welcome_evidence:action.evidence,
        });
      }
      await delay(120);
    }
    return clicked;
  }

  async function openVoice(active) {
    // Chrome reports a focused window even when its Xvfb document is hidden.
    // Record visibility as evidence, not as permission to bypass Voice startup.
    await diagnostic(active, "page-visibility-observed", {
      browser_visibility: String(document.visibilityState || "unknown"),
      browser_has_focus: typeof document.hasFocus === "function" ? Boolean(document.hasFocus()) : null,
    });
    // Do not click Start Voice if a genuine session is already visible.
    // A generic sentence in page text is deliberately NOT proof of Voice UI.
    const preexisting = voiceUiReady();
    if (preexisting) {
      await diagnostic(active, "session-already-visible", { voice_ready_reason: preexisting.reason });
    } else {
      const trigger = await waitFor(voiceTrigger, 15000, 150);
      if (!trigger) {
        const labels = [...(composerRoot() || document).querySelectorAll("button")]
          .filter(visible).map(labelOf).filter(Boolean).slice(0, 40);
        throw new Error(`ChatGPT Voice button was not found. Visible composer buttons: ${labels.join(" | ")}`);
      }
      active.voiceTriggerLabel = trigger.label;
      await diagnostic(active, "trigger-found", { voice_trigger_label: trigger.label });
      trigger.button.click();
      await diagnostic(active, "trigger-clicked");
    }

    await settleVoiceWelcome(active);
    const ready = preexisting || await waitFor(() => voiceUiReady(), 20000, 150);
    if (!ready) {
      const error = pageError();
      throw voiceFailure("VOICE_UI_NOT_READY", error || "ChatGPT Voice UI did not become ready");
    }
    await diagnostic(active, "ui-visible", { voice_ready_reason: ready.reason });
    // Seeing an orb is NOT proof of usable WebRTC. The actual browser must
    // request our microphone stream or expose the remote audio track.
    let transport = null;
    const transportDeadline = Date.now() + 16000;
    while (Date.now() < transportDeadline) {
      const failure = globalThis.__CHAT2API_VOICE_UI_GATE_V185__?.connectionFailure?.();
      if (failure) {
        postMain("voice.transport.snapshot", active);
        const snapshot = await waitMain(active.requestId,
          item => item.type === "voice.transport.snapshot", 1200);
        await diagnostic(active, "chatgpt-voice-connection-failed", {
          voice_failure_code: failure.code,
          chatgpt_voice_error: failure.message,
          peer_connections_in_request: Number(snapshot?.peer_connections_in_request || 0),
          mic_requests_in_request: Number(snapshot?.mic_requests_in_request || 0),
          ice_connection_state: String(snapshot?.ice_connection_state || "unknown"),
          peer_connection_state: String(snapshot?.peer_connection_state || "unknown"),
          browser_visibility: String(snapshot?.page_visibility || document.visibilityState || "unknown"),
        });
        throw voiceFailure(failure.code, failure.message);
      }
      transport = eventBucket(active.requestId).find(item =>
        item.type === "voice.mic.synthetic" || item.type === "voice.remote.track");
      if (transport) break;
      await delay(100);
    }
    if (!transport) {
      // Report what the installed MAIN-world hooks actually saw during THIS
      // request. Do not treat an earlier session's cumulative counters as
      // proof of an active WebRTC connection.
      postMain("voice.transport.snapshot", active);
      const snapshot = await waitMain(active.requestId,
        item => item.type === "voice.transport.snapshot", 1200);
      await diagnostic(active, "transport-not-ready", {
        voice_failure_code: "VOICE_TRANSPORT_NOT_READY",
        synthetic_mic_seen: false, remote_track_seen: false,
        transport_wait_ms: 16000,
        peer_connections_in_request: Number(snapshot?.peer_connections_in_request || 0),
        mic_requests_in_request: Number(snapshot?.mic_requests_in_request || 0),
        peer_hook_still_ready: snapshot?.peer_hook_ready === true,
        mic_hook_still_ready: snapshot?.mic_hook_ready === true,
        browser_visibility: String(snapshot?.page_visibility || "unknown"),
        ice_connection_state: String(snapshot?.ice_connection_state || "unknown"),
        peer_connection_state: String(snapshot?.peer_connection_state || "unknown"),
        ice_candidate_errors: Number(snapshot?.ice_candidate_errors || 0),
        transport_snapshot_received: Boolean(snapshot),
      });
      throw voiceFailure("VOICE_TRANSPORT_NOT_READY",
        "ChatGPT Voice never acquired the microphone or remote WebRTC track; inspect ICE/proxy diagnostics and Voice connection toast");
    }
    await diagnostic(active, "transport-preflight", {
      voice_ready_reason: ready.reason,
      synthetic_mic_seen: transport.type === "voice.mic.synthetic",
      remote_track_seen: transport.type === "voice.remote.track",
    });
    return { ready, transport };
  }

  function normVoiceText(value) {
    // Normalize layout whitespace, not the literal characters "\\s".
    // Voice prompts can contain newlines when called through the public API.
    return String(value || "").normalize("NFC").replace(/\s+/g, " ").trim();
  }

  function userTurnConfirmed(prompt, existingCount) {
    const nodes = [...document.querySelectorAll("[data-message-author-role='user']")].filter(visible);
    if (nodes.length <= existingCount) return false;
    const target = normVoiceText(prompt);
    return nodes.slice(existingCount).some(node => normVoiceText(textOf(node)).includes(target));
  }

  async function sendTypedPrompt(active, prompt) {
    const input = await waitFor(composerInput, 15000, 150);
    if (!input) throw voiceFailure("VOICE_TEXT_COMPOSER_MISSING",
      "Voice UI has no accessible text composer");
    const baselineUsers = [...document.querySelectorAll("[data-message-author-role='user']")].filter(visible).length;
    const baselineAssistants = assistantNodes().length;
    setText(input, prompt);
    const written = await waitFor(() => {
      const value = ("value" in input ? input.value : input.innerText || input.textContent) || "";
      return normVoiceText(value) === normVoiceText(prompt) ? true : false;
    }, 3500, 100);
    if (!written) throw voiceFailure("VOICE_PROMPT_EDITOR_MISMATCH",
      "The Voice text composer did not retain the complete requested prompt");
    const button = await waitFor(sendButton, 5000, 100);
    if (!button) throw voiceFailure("VOICE_SEND_BUTTON_NOT_READY",
      "Voice text composer has no verified enabled send button");
    await diagnostic(active, "prompt-editor-verified", { prompt_chars: prompt.length });
    button.click();
    await diagnostic(active, "prompt-submit-attempted", { prompt_chars: prompt.length });
    // A button click is not delivery. Require evidence from the same ChatGPT
    // conversation before waiting for the model's remote speech audio.
    const accepted = await waitFor(() => {
      if (userTurnConfirmed(prompt, baselineUsers)) return "user-turn";
      if (assistantNodes().length > baselineAssistants) return "assistant-turn";
      return null;
    }, 12000, 160);
    if (!accepted) {
      await diagnostic(active, "prompt-unverified", {
        voice_failure_code: "VOICE_TEXT_SUBMISSION_UNVERIFIED",
        send_button_clicked: true,
      });
      throw voiceFailure("VOICE_TEXT_SUBMISSION_UNVERIFIED",
        "Clicked Voice send, but ChatGPT did not confirm a new user or assistant turn");
    }
    await diagnostic(active, "prompt-accepted", {
      prompt_chars: prompt.length, confirmation: accepted,
    });
  }

  async function verifyInjectedMicrophone(active) {
    // A synthetic AudioContext destination alone is not microphone delivery.
    // In conversation mode ChatGPT must actually call intercepted
    // getUserMedia and receive our track before we inject uploaded audio.
    const attached = await waitMain(active.requestId,
      item => item.type === "voice.mic.synthetic" && Number(item.track_count || 0) > 0,
      7000);
    if (!attached) {
      await diagnostic(active, "mic-not-attached", {
        voice_failure_code: "VOICE_SYNTHETIC_MIC_NOT_ATTACHED",
      });
      throw voiceFailure("VOICE_SYNTHETIC_MIC_NOT_ATTACHED",
        "Voice UI never acquired the synthetic microphone track for conversation input");
    }
    await diagnostic(active, "mic-injection-confirmed", {
      synthetic_mic_seen: true, synthetic_tracks: Number(attached.track_count),
    });
  }

  async function playVoiceInput(active) {
    postMain("voice.input.play", active);
    const started = await waitMain(active.requestId, item => item.type === "voice.input.started" || item.type === "voice.input.error", 10000);
    if (!started || started.type === "voice.input.error") throw new Error(started?.error || "Unable to play test audio into ChatGPT Voice");
    await diagnostic(active, "input-started", { input_duration_ms: started.duration_ms || null });
    const ended = await waitMain(active.requestId, item => item.type === "voice.input.ended" || item.type === "voice.input.error", Math.max(12000, Number(started.duration_ms || 0) + 8000));
    if (!ended || ended.type === "voice.input.error") throw new Error(ended?.error || "Voice input did not finish playing");
    await diagnostic(active, "input-ended", { input_duration_ms: ended.duration_ms || started.duration_ms || null });
  }

  async function finalizeRecording(active) {
    postMain("voice.record.stop", active);
    const recorded = await waitMain(active.requestId, item => item.type === "voice.record.complete" || item.type === "voice.record.error", 10000);
    if (!recorded) throw new Error("Timed out finalizing GPT-Live audio capture");
    if (recorded.type === "voice.record.error") throw new Error(recorded.error || "GPT-Live audio capture failed");
    if (!recorded.b64_json || Number(recorded.size || 0) < 100) throw new Error("GPT-Live remote track was captured but produced no usable audio bytes");
    return recorded;
  }

  async function armRecording(active) {
    if (active.cancelled) throw voiceFailure("VOICE_CANCELLED", "Voice request cancelled");
    // A Voice orb or remote track may precede submission. Request-scoped
    // capture starts only after text receipt or uploaded audio completion.
    // The MAIN-world bridge owns MediaRecorder, not this controller.
    active.captureArmSequence = state.mainSequence;
    postMain("voice.record.arm", active);
    const armed = await waitMain(active.requestId,
      item => item.type === "voice.record.armed" || item.type === "voice.record.error", 5000);
    if (!armed) throw voiceFailure("VOICE_CAPTURE_ARM_TIMEOUT",
      "Voice bridge did not confirm response capture arming");
    if (armed.type === "voice.record.error") throw voiceFailure("VOICE_RECORDER_FAILED",
      String(armed.error || "Voice recorder could not be armed"));
    if (active.cancelled) throw voiceFailure("VOICE_CANCELLED", "Voice request cancelled");
    active.captureArmSawTrack = armed.remote_track_seen === true;
    await diagnostic(active, "capture-armed", {
      transport_track_preexisting: active.captureArmSawTrack,
    });
  }

  async function waitResponse(active, baselineCount) {
    const timeout = Math.max(30000, Number(active.timeoutSeconds || 180) * 1000);
    const deadline = Date.now() + timeout;
    let transcript = "";
    let stableAt = 0;
    let soundStarted = false;
    let lastSoundAt = 0;
    const responseStartedAt = Date.now();
    // The remote track may have been observed during Voice UI preflight,
    // long before the model's confirmed input. Its earlier event can be
    // evicted from the rolling diagnostic queue; the arm ack preserves proof.
    let remoteTrackAt = active.captureArmSawTrack ? responseStartedAt : 0;
    let recordStartedAt = 0;
    let lastSeenSequence = active.captureArmSequence || 0;
    let lastStatusAt = 0;

    while (Date.now() < deadline && !active.cancelled) {
      const error = pageError();
      if (error) throw new Error(`ChatGPT Voice UI error: ${error}`);
      const events = eventBucket(active.requestId);
      // Never lose one-shot track/recorder proof behind frequent (~6Hz)
      // voice.audio.level events. The monotonic event sequence also avoids
      // interpreting welcome audio from before capture arming as reply audio.
      for (const item of events) {
        const sequence = Number(item._voice_sequence || 0);
        if (sequence && sequence <= lastSeenSequence) continue;
        if (item.type === "voice.remote.track" && !remoteTrackAt) {
          remoteTrackAt = Date.now();
          diagnostic(active, "remote-track", { remote_track_seen: true }).catch(() => {});
        }
        if (item.type === "voice.record.started" && !recordStartedAt) recordStartedAt = Date.now();
        if (item.type === "voice.audio.level") {
          if (item.speaking) soundStarted = true;
          if (item.last_sound_at) lastSoundAt = Number(item.last_sound_at);
        }
        if (item.type === "voice.record.error") throw voiceFailure("VOICE_RECORDER_FAILED",
          item.error || "Voice audio recorder failed");
        if (item.type === "voice.audio.level.error") throw voiceFailure("VOICE_AUDIO_ANALYZER_UNAVAILABLE",
          item.error || "Remote audio analyser failed");
        if (item.type === "voice.input.error") throw voiceFailure("VOICE_INPUT_PLAYBACK_FAILED",
          item.error || "Voice input playback failed");
        if (sequence) lastSeenSequence = Math.max(lastSeenSequence, sequence);
      }

      const nodes = assistantNodes();
      if (nodes.length > baselineCount) {
        const text = assistantText(nodes[nodes.length - 1]);
        if (text && text !== transcript) {
          transcript = text;
          stableAt = Date.now();
        }
      }

      const now = Date.now();
      const silentFor = lastSoundAt ? now - lastSoundAt : 0;
      const stableFor = stableAt ? now - stableAt : 0;
      if (!remoteTrackAt && now - responseStartedAt > 18000) {
        throw voiceFailure("VOICE_REMOTE_TRACK_NOT_FOUND",
          "ChatGPT did not expose a remote WebRTC audio track within 18 seconds of confirmed input");
      }
      if (remoteTrackAt && !recordStartedAt && now - remoteTrackAt > 4500) {
        throw voiceFailure("VOICE_RECORDER_NOT_STARTED",
          "Remote WebRTC track was found but MediaRecorder never started");
      }
      if (recordStartedAt && !soundStarted && now - recordStartedAt > 25000) {
        throw voiceFailure("VOICE_REMOTE_AUDIO_SILENT",
          "Remote track was recorded but no actual voice audio energy was detected");
      }
      if (soundStarted && silentFor > 1700 && (!transcript || stableFor > 700)) break;
      // A transcript and a track alone must never count as successful audio:
      // only the analyser's actual sound activity authorizes recording handoff.
      if (soundStarted && transcript && remoteTrackAt && stableFor > 2200) break;
      if (now - lastStatusAt > 5000) {
        lastStatusAt = now;
        diagnostic(active, "response-progress", {
          remote_track_seen: Boolean(remoteTrackAt),
          recorder_started: Boolean(recordStartedAt),
          remote_sound_started: soundStarted,
          elapsed_ms: now - responseStartedAt,
        }).catch(() => {});
      }
      await delay(140);
    }

    if (active.cancelled) throw voiceFailure("VOICE_CANCELLED", "Voice request cancelled");
    if (!remoteTrackAt) throw voiceFailure("VOICE_REMOTE_TRACK_NOT_FOUND",
      "Response deadline elapsed before a remote track was observed");
    if (!soundStarted) throw voiceFailure("VOICE_REMOTE_AUDIO_SILENT",
      "Response deadline elapsed without confirmed remote audio energy");
    const recorded = await finalizeRecording(active);
    return { transcript, recorded, soundStarted, lastSoundAt };
  }

  async function runVoice(message) {
    if (state.active || globalThis.__CHAT2API_VOICE_LIVE_CONTENT_V2__?.active)
      throw new Error("This ChatGPT tab is already handling a Voice request");
    const active = {
      requestId: message.requestId,
      mode: message.options?.mode || (message.audio ? "conversation" : "speech"),
      timeoutSeconds: message.options?.timeout_seconds || 180,
      cancelled: false,
      voiceTriggerLabel: "",
    };
    state.active = active;
    state.mainEvents.set(active.requestId, []);
    const startedAt = performance.now();

    try {
      await diagnostic(active, "request-received");
      let inputData = null;
      if (message.audio?.file_id) inputData = await fetchAudio(message.audio);
      postMain("voice.prepare", active, {
        mode: active.mode,
        input_base64: inputData?.base64 || "",
        input_mime: inputData?.mime_type || message.audio?.mime_type || "",
      });
      const prepared = await waitMain(active.requestId, item => item.type === "voice.prepared", 5000);
      if (!prepared) throw voiceFailure("VOICE_MAIN_BRIDGE_UNAVAILABLE",
        "Voice MAIN-world bridge did not acknowledge preparation");
      await diagnostic(active, "main-prepared", {
        has_input: Boolean(inputData?.base64),
        peer_hook_ready: Boolean(prepared.peer_hook_ready),
        mic_hook_ready: Boolean(prepared.mic_hook_ready),
        peer_connections_created: Number(prepared.peer_connections_created || 0),
        transport_hook_error: prepared.transport_error || "",
      });
      if (!prepared.peer_hook_ready) throw voiceFailure("VOICE_WEBRTC_HOOK_UNAVAILABLE",
        "The ChatGPT page's RTCPeerConnection constructor could not be observed");
      if (active.mode === "conversation" && !prepared.mic_hook_ready) {
        throw voiceFailure("VOICE_MIC_HOOK_UNAVAILABLE",
          "The ChatGPT page did not accept synthetic microphone interception");
      }

      const baselineCount = assistantNodes().length;
      await openVoice(active);
      await emit({
        type: "image.started",
        kind: "voice",
        request_id: active.requestId,
        diagnostics: {
          route: "chatgpt-voice-v2",
          voice_mode: active.mode,
          voice_trigger_label: active.voiceTriggerLabel,
          voice_prepare_ms: Math.round((performance.now() - startedAt) * 10) / 10,
        },
      });

      if (active.mode === "speech") {
        const prompt = String(message.prompt || "").trim();
        if (!prompt) throw new Error("Voice speech input is empty");
        await sendTypedPrompt(active, prompt);
      } else {
        if (!inputData?.base64) throw new Error("Voice conversation requires an audio input file");
        await verifyInjectedMicrophone(active);
        if (active.cancelled) throw voiceFailure("VOICE_CANCELLED", "Voice request cancelled");
        await delay(500);
        await playVoiceInput(active);
      }

      await armRecording(active);
      const result = await waitResponse(active, baselineCount);
      if (active.cancelled) throw voiceFailure("VOICE_CANCELLED", "Voice request cancelled");
      const diagnostics = {
        route: "chatgpt-voice-v2",
        voice_mode: active.mode,
        voice_trigger_label: active.voiceTriggerLabel,
        remote_track_seen: Boolean(result.recorded.remote_track_seen),
        remote_sound_started: Boolean(result.recorded.sound_started || result.soundStarted),
        audio_capture_ms: result.recorded.duration_ms || null,
        audio_bytes: result.recorded.size || 0,
        transcript_chars: result.transcript.length,
        total_browser_ms: Math.round((performance.now() - startedAt) * 10) / 10,
        voice_stage: "completed",
      };
      await emit({ type: "image.diagnostics", kind: "voice", request_id: active.requestId, diagnostics });
      if (active.cancelled) throw voiceFailure("VOICE_CANCELLED", "Voice request cancelled");
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
    } catch (error) {
      const code = String(error?.code || "VOICE_EXECUTION_FAILED");
      const failedAfter = String(active.lastStage || "unknown");
      await diagnostic(active, "failed", {
        voice_failure_code: code,
        voice_failure_stage: failedAfter,
        voice_error_message: String(error?.message || error).slice(0, 240),
      });
      throw error;
    } finally {
      try { endVoiceButton()?.click(); } catch (_) {}
      // Detach the old synthetic microphone *before* releasing MAIN-world
      // request ownership, so late old-request cleanup cannot reach a newer
      // Voice request on the same tab.
      postMain("voice.mic.stop", active);
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
    if (message.type === "chat2api.voice.request.v2") {
      runVoice(message).catch(error => emit({
        type: "image.error",
        kind: "voice",
        request_id: message.requestId,
        error: String(error?.message || error),
        error_code: String(error?.code || "VOICE_EXECUTION_FAILED"),
      }));
      sendResponse({ ok: true, controller: "voice-v2" });
      return false;
    }
    if (message.type === "chat2api.voice.cancel.v2") {
      cancelVoice(message.requestId).then(() => sendResponse({ ok: true }));
      return true;
    }
    return false;
  });
})();
