(() => {
  const KEY = "__CHAT2API_VOICE_LIVE_MAIN_V1__";
  if (globalThis[KEY]) return;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const live = {
    requestId: null,
    sessionId: null,
    prepared: false,
    inputNextAt: 0,
    inputSources: new Set(),
    remoteContext: null,
    remoteSource: null,
    remoteProcessor: null,
    remoteSink: null,
    remoteWatchTimer: null,
    recorderDrainTimer: null,
    speaking: false,
    responseId: null,
    lastSoundAt: 0,
    suppressed: false,
    seq: 0,
  };
  globalThis[KEY] = live;

  const base = () => globalThis.__CHAT2API_VOICE_MAIN_V1__ || null;
  const post = (type, data = {}) => window.postMessage({
    source: "chat2api-main",
    type,
    request_id: live.requestId,
    session_id: live.sessionId,
    ...data,
  }, "*");

  function decodeBase64(value) {
    const raw = atob(String(value || ""));
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
    return bytes;
  }

  function encodeBase64(bytes) {
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function pcm16ToFloat(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const output = new Float32Array(Math.floor(bytes.byteLength / 2));
    for (let i = 0; i < output.length; i += 1) output[i] = view.getInt16(i * 2, true) / 32768;
    return output;
  }

  function resample(input, sourceRate, targetRate) {
    if (!input.length || sourceRate <= 0 || targetRate <= 0) return new Float32Array();
    if (sourceRate === targetRate) return new Float32Array(input);
    const outputLength = Math.max(1, Math.round(input.length * targetRate / sourceRate));
    const output = new Float32Array(outputLength);
    const ratio = sourceRate / targetRate;
    for (let i = 0; i < outputLength; i += 1) {
      const position = i * ratio;
      const left = Math.min(input.length - 1, Math.floor(position));
      const right = Math.min(input.length - 1, left + 1);
      const fraction = position - left;
      output[i] = input[left] * (1 - fraction) + input[right] * fraction;
    }
    return output;
  }

  function floatToPcm16Bytes(input) {
    const bytes = new Uint8Array(input.length * 2);
    const view = new DataView(bytes.buffer);
    for (let i = 0; i < input.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, input[i]));
      view.setInt16(i * 2, sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767), true);
    }
    return bytes;
  }

  async function waitForSyntheticMic(timeoutMs = 12000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const state = base();
      if (state?.micContext && state?.micDestination) {
        try { await state.micContext.resume(); } catch (_) {}
        return state;
      }
      await delay(40);
    }
    throw new Error("ChatGPT Voice synthetic microphone did not become ready");
  }

  async function pushInput(encoded, sourceRate = 16000) {
    if (!live.prepared || !encoded) return;
    const state = await waitForSyntheticMic();
    const bytes = decodeBase64(encoded);
    if (bytes.byteLength < 2) return;
    const samples = pcm16ToFloat(bytes);
    const context = state.micContext;
    const buffer = context.createBuffer(1, samples.length, Number(sourceRate || 16000));
    buffer.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(state.micDestination);
    const earliest = context.currentTime + 0.025;
    const startAt = Math.max(earliest, live.inputNextAt || earliest);
    live.inputNextAt = startAt + buffer.duration;
    live.inputSources.add(source);
    source.onended = () => live.inputSources.delete(source);
    source.start(startAt);
  }

  function rmsOf(samples) {
    if (!samples.length) return 0;
    let sum = 0;
    for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
    return Math.sqrt(sum / samples.length);
  }

  function newResponseId() {
    try { return `gptlive_${crypto.randomUUID()}`; }
    catch (_) { return `gptlive_${Date.now()}_${Math.random().toString(16).slice(2)}`; }
  }

  function beginRemoteSpeech() {
    if (live.speaking) return;
    live.speaking = true;
    live.responseId = newResponseId();
    live.lastSoundAt = Date.now();
    live.suppressed = false;
    post("voice.live.response.started", { response_id: live.responseId });
  }

  function endRemoteSpeech(reason = "silence") {
    if (!live.speaking || !live.responseId) return;
    const responseId = live.responseId;
    live.speaking = false;
    live.responseId = null;
    live.lastSoundAt = 0;
    post("voice.live.response.stopped", { response_id: responseId, reason });
  }

  async function attachRemoteStream(stream) {
    if (!live.prepared || live.remoteProcessor || !stream) return;
    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(2048, 1, 1);
    const sink = context.createGain();
    sink.gain.value = 0;
    source.connect(processor);
    processor.connect(sink).connect(context.destination);
    live.remoteContext = context;
    live.remoteSource = source;
    live.remoteProcessor = processor;
    live.remoteSink = sink;
    try { await context.resume(); } catch (_) {}

    processor.onaudioprocess = event => {
      if (!live.prepared) return;
      const samples = event.inputBuffer.getChannelData(0);
      const rms = rmsOf(samples);
      const now = Date.now();
      const audible = rms > 0.0055;
      if (audible) {
        if (!live.speaking) beginRemoteSpeech();
        live.lastSoundAt = now;
      }
      if (live.speaking && !audible && live.lastSoundAt && now - live.lastSoundAt > 900) {
        endRemoteSpeech("silence");
        live.suppressed = false;
        return;
      }
      if (!live.speaking || live.suppressed || !live.responseId) return;
      const pcm = floatToPcm16Bytes(resample(samples, context.sampleRate, 24000));
      if (!pcm.byteLength) return;
      live.seq += 1;
      post("voice.live.audio", {
        response_id: live.responseId,
        seq: live.seq,
        sample_rate: 24000,
        pcm_base64: encodeBase64(pcm),
      });
    };
    post("voice.live.remote.ready", { sample_rate: context.sampleRate });
  }

  async function watchRemoteRecorder() {
    clearInterval(live.remoteWatchTimer);
    live.remoteWatchTimer = setInterval(() => {
      if (!live.prepared) return;
      const state = base();
      const stream = state?.recorder?.stream;
      if (stream && !live.remoteProcessor) attachRemoteStream(stream).catch(error => post("voice.live.error", { error: String(error?.message || error) }));
    }, 80);
    clearInterval(live.recorderDrainTimer);
    live.recorderDrainTimer = setInterval(() => {
      if (!live.prepared) return;
      const state = base();
      if (Array.isArray(state?.recorderChunks) && state.recorderChunks.length > 8) {
        state.recorderChunks.splice(0, state.recorderChunks.length - 2);
      }
    }, 1000);
  }

  function cleanupRemote() {
    clearInterval(live.remoteWatchTimer);
    clearInterval(live.recorderDrainTimer);
    live.remoteWatchTimer = null;
    live.recorderDrainTimer = null;
    try { live.remoteProcessor?.disconnect(); } catch (_) {}
    try { live.remoteSource?.disconnect(); } catch (_) {}
    try { live.remoteSink?.disconnect(); } catch (_) {}
    try { live.remoteContext?.close(); } catch (_) {}
    live.remoteProcessor = null;
    live.remoteSource = null;
    live.remoteSink = null;
    live.remoteContext = null;
    if (live.speaking) endRemoteSpeech("session_stop");
  }

  function reset() {
    live.prepared = false;
    live.suppressed = false;
    live.inputNextAt = 0;
    for (const source of [...live.inputSources]) {
      try { source.stop(); } catch (_) {}
    }
    live.inputSources.clear();
    cleanupRemote();
    live.requestId = null;
    live.sessionId = null;
  }

  window.addEventListener("message", event => {
    if (event.source !== window || event.data?.source !== "chat2api-isolated") return;
    const message = event.data;
    if (message.type === "voice.live.prepare") {
      reset();
      live.requestId = message.request_id || null;
      live.sessionId = message.session_id || null;
      live.prepared = true;
      live.inputNextAt = 0;
      watchRemoteRecorder().catch(() => {});
      post("voice.live.prepared");
    } else if (message.type === "voice.live.audio.push" && message.request_id === live.requestId) {
      pushInput(message.pcm_base64, Number(message.sample_rate || 16000)).catch(error => post("voice.live.error", { error: String(error?.message || error) }));
    } else if (message.type === "voice.live.cancel" && message.request_id === live.requestId) {
      live.suppressed = true;
      if (live.responseId) {
        const responseId = live.responseId;
        endRemoteSpeech("client_cancel");
        post("voice.live.response.interrupted", { response_id: responseId, reason: "client_cancel" });
      }
    } else if (message.type === "voice.live.stop" && message.request_id === live.requestId) {
      reset();
    }
  });
})();
