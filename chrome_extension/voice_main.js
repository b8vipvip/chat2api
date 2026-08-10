(() => {
  const KEY = "__CHAT2API_VOICE_MAIN_V1__";
  if (globalThis[KEY]) return;

  const state = {
    requestId: null,
    mode: "speech",
    preparedInput: null,
    micContext: null,
    micDestination: null,
    micOscillator: null,
    recorder: null,
    recorderChunks: [],
    recorderMime: "",
    remoteTrackSeen: false,
    recordingStartedAt: 0,
    lastSoundAt: 0,
    soundStarted: false,
    analyserTimer: null,
  };
  globalThis[KEY] = state;

  const post = (type, data = {}) => window.postMessage({
    source: "chat2api-main",
    type,
    request_id: state.requestId,
    ...data,
  }, "*");

  function decodeBase64(value) {
    const raw = atob(String(value || ""));
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
    return bytes.buffer;
  }

  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    return btoa(binary);
  }

  function recorderMime() {
    const options = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
    return options.find(value => globalThis.MediaRecorder?.isTypeSupported?.(value)) || "";
  }

  function stopAnalyser() {
    clearInterval(state.analyserTimer);
    state.analyserTimer = null;
  }

  function startAnalyser(stream) {
    stopAnalyser();
    try {
      const context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      const data = new Float32Array(analyser.fftSize);
      state.analyserTimer = setInterval(() => {
        try {
          analyser.getFloatTimeDomainData(data);
          let sum = 0;
          for (const sample of data) sum += sample * sample;
          const rms = Math.sqrt(sum / data.length);
          const speaking = rms > 0.008;
          if (speaking) {
            state.soundStarted = true;
            state.lastSoundAt = Date.now();
          }
          post("voice.audio.level", { speaking, rms, last_sound_at: state.lastSoundAt });
        } catch (_) {}
      }, 160);
    } catch (error) {
      post("voice.audio.level.error", { error: String(error?.message || error) });
    }
  }

  function attachRemoteTrack(track) {
    if (!track || track.kind !== "audio") return;
    state.remoteTrackSeen = true;
    post("voice.remote.track", { track_state: track.readyState || "live" });
    if (state.recorder && state.recorder.state !== "inactive") return;
    try {
      const stream = new MediaStream([track]);
      const mimeType = recorderMime();
      state.recorderChunks = [];
      state.recorderMime = mimeType || "audio/webm";
      state.soundStarted = false;
      state.lastSoundAt = 0;
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      state.recorder = recorder;
      state.recordingStartedAt = performance.now();
      recorder.ondataavailable = event => { if (event.data?.size) state.recorderChunks.push(event.data); };
      recorder.onerror = event => post("voice.record.error", { error: String(event?.error?.message || event?.error || "MediaRecorder error") });
      recorder.onstop = async () => {
        stopAnalyser();
        try {
          const blob = new Blob(state.recorderChunks, { type: state.recorderMime || "audio/webm" });
          const buffer = await blob.arrayBuffer();
          post("voice.record.complete", {
            mime_type: blob.type || state.recorderMime || "audio/webm",
            b64_json: arrayBufferToBase64(buffer),
            size: buffer.byteLength,
            duration_ms: Math.round((performance.now() - state.recordingStartedAt) * 10) / 10,
            remote_track_seen: state.remoteTrackSeen,
            sound_started: state.soundStarted,
          });
        } catch (error) {
          post("voice.record.error", { error: String(error?.message || error) });
        }
      };
      recorder.start(250);
      startAnalyser(stream);
      post("voice.record.started", { mime_type: state.recorderMime });
    } catch (error) {
      post("voice.record.error", { error: String(error?.message || error) });
    }
  }

  const NativePC = globalThis.RTCPeerConnection;
  if (NativePC) {
    function WrappedRTCPeerConnection(...args) {
      const pc = new NativePC(...args);
      pc.addEventListener("track", event => attachRemoteTrack(event.track));
      return pc;
    }
    WrappedRTCPeerConnection.prototype = NativePC.prototype;
    Object.setPrototypeOf(WrappedRTCPeerConnection, NativePC);
    try { globalThis.RTCPeerConnection = WrappedRTCPeerConnection; } catch (_) {}
  }

  async function ensureSyntheticMic() {
    if (state.micDestination) return state.micDestination.stream;
    const context = new AudioContext();
    const destination = context.createMediaStreamDestination();
    const gain = context.createGain();
    gain.gain.value = 0;
    const oscillator = context.createOscillator();
    oscillator.frequency.value = 30;
    oscillator.connect(gain).connect(destination);
    oscillator.start();
    state.micContext = context;
    state.micDestination = destination;
    state.micOscillator = oscillator;
    try { await context.resume(); } catch (_) {}
    return destination.stream;
  }

  const mediaDevices = navigator.mediaDevices;
  const nativeGetUserMedia = mediaDevices?.getUserMedia?.bind(mediaDevices);
  if (mediaDevices && nativeGetUserMedia) {
    try {
      mediaDevices.getUserMedia = async function chat2apiGetUserMedia(constraints) {
        if (state.requestId && constraints?.audio) {
          const synthetic = await ensureSyntheticMic();
          const tracks = [...synthetic.getAudioTracks()];
          post("voice.mic.synthetic", { track_count: tracks.length, mode: state.mode });
          if (!constraints?.video) return new MediaStream(tracks);
          const native = await nativeGetUserMedia({ ...constraints, audio: false });
          return new MediaStream([...tracks, ...native.getVideoTracks()]);
        }
        return nativeGetUserMedia(constraints);
      };
    } catch (_) {}
  }

  async function playPreparedInput() {
    if (!state.preparedInput?.base64) throw new Error("No prepared voice input");
    const stream = await ensureSyntheticMic();
    const context = state.micContext;
    try { await context.resume(); } catch (_) {}
    const buffer = await context.decodeAudioData(decodeBase64(state.preparedInput.base64).slice(0));
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(state.micDestination);
    source.onended = () => post("voice.input.ended", { duration_ms: Math.round(buffer.duration * 1000) });
    source.start();
    post("voice.input.started", { duration_ms: Math.round(buffer.duration * 1000), mime_type: state.preparedInput.mime_type || "" });
    return stream;
  }

  window.addEventListener("message", event => {
    if (event.source !== window || event.data?.source !== "chat2api-isolated") return;
    const message = event.data;
    if (message.type === "voice.prepare") {
      state.requestId = message.request_id || null;
      state.mode = message.mode || "speech";
      state.preparedInput = message.input_base64 ? { base64: message.input_base64, mime_type: message.input_mime || "" } : null;
      state.remoteTrackSeen = false;
      state.soundStarted = false;
      state.lastSoundAt = 0;
      post("voice.prepared", { mode: state.mode, has_input: Boolean(state.preparedInput) });
    } else if (message.type === "voice.input.play" && message.request_id === state.requestId) {
      playPreparedInput().catch(error => post("voice.input.error", { error: String(error?.message || error) }));
    } else if (message.type === "voice.record.stop" && message.request_id === state.requestId) {
      if (state.recorder && state.recorder.state !== "inactive") state.recorder.stop();
      else post("voice.record.error", { error: "Remote voice audio track was not captured" });
    } else if (message.type === "voice.reset" && message.request_id === state.requestId) {
      if (state.recorder && state.recorder.state !== "inactive") {
        try { state.recorder.stop(); } catch (_) {}
      }
      state.requestId = null;
      state.preparedInput = null;
    }
  });
})();
