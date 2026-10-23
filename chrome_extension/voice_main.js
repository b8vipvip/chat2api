(() => {
  const KEY = "__CHAT2API_VOICE_MAIN_V1__";
  if (globalThis[KEY]) return;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
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
    remoteTrackSeenAt: 0,
    remoteTrack: null,
    captureArmed: false,
    recordingStartedAt: 0,
    lastSoundAt: 0,
    soundStarted: false,
    analyserTimer: null,
    analyserContext: null,
    peerHook: null,
    micHook: null,
    peerHookReady: false,
    micHookReady: false,
    peerConnectionsCreated: 0,
    micRequestsSeen: 0,
    lastTransportError: "",
  };
  globalThis[KEY] = state;

  const post = (type, data = {}, ownerRequestId = state.requestId) => window.postMessage({
    source: "chat2api-main",
    type,
    request_id: ownerRequestId,
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
    try { state.analyserContext?.close?.().catch?.(() => {}); } catch (_) {}
    state.analyserContext = null;
  }

  function startAnalyser(stream, ownerRequestId, recordingState) {
    stopAnalyser();
    try {
      const context = new AudioContext();
      state.analyserContext = context;
      if (context.state === "suspended" && typeof context.resume === "function") {
        context.resume()
          .then(() => post("voice.audio.context", { state: context.state || "running" }, ownerRequestId))
          .catch(error => post("voice.audio.level.error", {
            error: "AudioContext resume failed: " + String(error?.message || error),
          }, ownerRequestId));
      }
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      const data = new Float32Array(analyser.fftSize);
      state.analyserTimer = setInterval(() => {
        if (state.requestId !== ownerRequestId) return;
        try {
          analyser.getFloatTimeDomainData(data);
          let sum = 0;
          for (const sample of data) sum += sample * sample;
          const rms = Math.sqrt(sum / data.length);
          const speaking = rms > 0.008;
          if (speaking) {
            recordingState.soundStarted = true;
            recordingState.lastSoundAt = Date.now();
            state.soundStarted = true;
            state.lastSoundAt = recordingState.lastSoundAt;
          }
          post("voice.audio.level", {
            speaking, rms, last_sound_at: recordingState.lastSoundAt,
          }, ownerRequestId);
        } catch (_) {}
      }, 160);
    } catch (error) {
      post("voice.audio.level.error", { error: String(error?.message || error) }, ownerRequestId);
    }
  }

  // The remote WebRTC track proves the transport exists. It is NOT itself
  // permission to record a response: the Voice orb/welcome audio can play
  // before our prompt or uploaded microphone input has been accepted.
  function startCapture(track, ownerRequestId) {
    if (!state.captureArmed || state.requestId !== ownerRequestId ||
        !track || track.kind !== "audio" || track.readyState === "ended") return;
    if (state.recorder && state.recorder.state !== "inactive") return;
    try {
      const stream = new MediaStream([track]);
      const mimeType = recorderMime();
      // Audio blobs and asynchronous callbacks belong to the request that
      // created the recorder, NEVER to a later Voice request on the same tab.
      const chunks = [];
      const recordingState = { soundStarted: false, lastSoundAt: 0 };
      const recordedMime = mimeType || "audio/webm";
      const recordedAt = performance.now();
      state.recorderChunks = chunks;
      state.recorderMime = recordedMime;
      state.soundStarted = false;
      state.lastSoundAt = 0;
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      state.recorder = recorder;
      state.recordingStartedAt = recordedAt;
      recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
      recorder.onerror = event => post("voice.record.error", {
        error: String(event?.error?.message || event?.error || "MediaRecorder error"),
      }, ownerRequestId);
      recorder.onstop = async () => {
        if (state.recorder === recorder) {
          stopAnalyser();
          state.recorder = null;
        }
        try {
          const blob = new Blob(chunks, { type: recordedMime });
          const buffer = await blob.arrayBuffer();
          post("voice.record.complete", {
            mime_type: blob.type || recordedMime,
            b64_json: arrayBufferToBase64(buffer),
            size: buffer.byteLength,
            duration_ms: Math.round((performance.now() - recordedAt) * 10) / 10,
            remote_track_seen: true,
            sound_started: recordingState.soundStarted,
          }, ownerRequestId);
        } catch (error) {
          post("voice.record.error", { error: String(error?.message || error) }, ownerRequestId);
        }
      };
      recorder.start(250);
      startAnalyser(stream, ownerRequestId, recordingState);
      post("voice.record.started", { mime_type: recordedMime }, ownerRequestId);
    } catch (error) {
      post("voice.record.error", { error: String(error?.message || error) }, ownerRequestId);
    }
  }

  function attachRemoteTrack(track) {
    if (!state.requestId || !track || track.kind !== "audio" || track.readyState === "ended") return;
    const ownerRequestId = state.requestId;
    state.remoteTrack = track;
    state.remoteTrackSeen = true;
    state.remoteTrackSeenAt = performance.now();
    post("voice.remote.track", { track_state: track.readyState || "live" }, ownerRequestId);
    // A track arriving *after* the confirmed input still works; if it arrived
    // before the input, voice.record.arm will start capture from this track.
    if (state.captureArmed) startCapture(track, ownerRequestId);
  }

  // This MAIN-world script is declared at document_start: it must install
  // before ChatGPT constructs its voice peer. Do not infer transport readiness
  // merely because the Voice orb is visible.
  const NativePC = globalThis.RTCPeerConnection;
  if (NativePC) {
    function WrappedRTCPeerConnection(...args) {
      const pc = new NativePC(...args);
      state.peerConnectionsCreated += 1;
      pc.addEventListener("track", event => attachRemoteTrack(event.track));
      if (state.requestId) post("voice.transport.peer-created", {
        peer_connections_created: state.peerConnectionsCreated,
      });
      return pc;
    }
    WrappedRTCPeerConnection.prototype = NativePC.prototype;
    Object.setPrototypeOf(WrappedRTCPeerConnection, NativePC);
    try {
      globalThis.RTCPeerConnection = WrappedRTCPeerConnection;
      state.peerHook = WrappedRTCPeerConnection;
      state.peerHookReady = globalThis.RTCPeerConnection === WrappedRTCPeerConnection;
    } catch (error) {
      state.lastTransportError = String(error?.message || error);
    }
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
    const intercepted = async function chat2apiGetUserMedia(constraints) {
      if (state.requestId && constraints?.audio) {
        state.micRequestsSeen += 1;
        post("voice.mic.requested", { mode: state.mode, mic_requests_seen: state.micRequestsSeen });
        const synthetic = await ensureSyntheticMic();
        const tracks = [...synthetic.getAudioTracks()];
        post("voice.mic.synthetic", { track_count: tracks.length, mode: state.mode });
        if (!constraints?.video) return new MediaStream(tracks);
        const native = await nativeGetUserMedia({ ...constraints, audio: false });
        return new MediaStream([...tracks, ...native.getVideoTracks()]);
      }
      return nativeGetUserMedia(constraints);
    };
    // Assignment to a browser-native method may silently fail. Prove it was
    // installed; a false "mic ready" must not trigger an unbounded voice wait.
    try {
      mediaDevices.getUserMedia = intercepted;
      state.micHook = intercepted;
      state.micHookReady = mediaDevices.getUserMedia === intercepted;
      if (!state.micHookReady) {
        Object.defineProperty(mediaDevices, "getUserMedia", {
          value: intercepted, writable: true, configurable: true,
        });
        state.micHookReady = mediaDevices.getUserMedia === intercepted;
      }
    } catch (error) {
      state.lastTransportError = String(error?.message || error);
    }
  }

  async function waitForRemoteTrackBeforeInput(timeoutMs = 8000) {
    const startedAt = performance.now();
    const deadline = Date.now() + Math.max(1000, Number(timeoutMs || 8000));
    while (!state.remoteTrackSeen && Date.now() < deadline) await delay(50);
    if (!state.remoteTrackSeen) {
      throw new Error("Voice transport did not become ready before prepared input playback");
    }
    // The track event is the first reliable sign that SDP/WebRTC negotiation is
    // complete. Give the peer connection a small settling window so a short test
    // clip cannot finish before the outbound microphone sender is fully live.
    await delay(250);
    return Math.round((performance.now() - startedAt) * 10) / 10;
  }

  async function playPreparedInput() {
    if (!state.preparedInput?.base64) throw new Error("No prepared voice input");
    const transportWaitMs = await waitForRemoteTrackBeforeInput(8000);
    const stream = await ensureSyntheticMic();
    const context = state.micContext;
    try { await context.resume(); } catch (_) {}
    const buffer = await context.decodeAudioData(decodeBase64(state.preparedInput.base64).slice(0));
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(state.micDestination);
    source.onended = () => post("voice.input.ended", {
      duration_ms: Math.round(buffer.duration * 1000),
      transport_wait_ms: transportWaitMs,
      transport_ready: true,
    });
    source.start();
    post("voice.input.started", {
      duration_ms: Math.round(buffer.duration * 1000),
      mime_type: state.preparedInput.mime_type || "",
      transport_wait_ms: transportWaitMs,
      transport_ready: true,
    });
    return stream;
  }

  window.addEventListener("message", event => {
    if (event.source !== window || event.data?.source !== "chat2api-isolated") return;
    const message = event.data;
    if (message.type === "voice.prepare") {
      // A previous recorder may still be finalizing asynchronously.
      // Detach it before starting this request; its callbacks carry its own ID.
      if (state.recorder && state.recorder.state !== "inactive") {
        try { state.recorder.stop(); } catch (_) {}
      }
      stopAnalyser();
      state.recorder = null;
      state.requestId = message.request_id || null;
      state.mode = message.mode || "speech";
      state.preparedInput = message.input_base64 ? { base64: message.input_base64, mime_type: message.input_mime || "" } : null;
      state.remoteTrackSeen = false;
      state.remoteTrackSeenAt = 0;
      state.remoteTrack = null;
      state.captureArmed = false;
      state.recordingStartedAt = 0;
      state.soundStarted = false;
      state.lastSoundAt = 0;
      post("voice.prepared", {
        mode: state.mode, has_input: Boolean(state.preparedInput),
        peer_hook_ready: state.peerHookReady &&
          globalThis.RTCPeerConnection === state.peerHook,
        mic_hook_ready: state.micHookReady &&
          mediaDevices?.getUserMedia === state.micHook,
        peer_connections_created: state.peerConnectionsCreated,
        mic_requests_seen: state.micRequestsSeen,
        transport_error: state.lastTransportError,
      });
    } else if (message.type === "voice.input.play" && message.request_id === state.requestId) {
      playPreparedInput().catch(error => post("voice.input.error", { error: String(error?.message || error) }));
    } else if (message.type === "voice.record.arm" && message.request_id === state.requestId) {
      state.captureArmed = true;
      const track = state.remoteTrack;
      const hasTrack = Boolean(track && track.kind === "audio" && track.readyState !== "ended");
      // The acknowledgement is independent of recorder readiness; the
      // existing response-state guard verifies that MediaRecorder starts.
      post("voice.record.armed", { remote_track_seen: hasTrack });
      if (hasTrack) startCapture(track, state.requestId);
    } else if (message.type === "voice.record.stop" && message.request_id === state.requestId) {
      if (state.recorder && state.recorder.state !== "inactive") state.recorder.stop();
      else post("voice.record.error", { error: "Remote voice audio track was not captured" });
    } else if (message.type === "voice.reset" && message.request_id === state.requestId) {
      if (state.recorder && state.recorder.state !== "inactive") {
        try { state.recorder.stop(); } catch (_) {}
      }
      state.requestId = null;
      state.preparedInput = null;
      state.captureArmed = false;
      state.remoteTrack = null;
    }
  });
})();