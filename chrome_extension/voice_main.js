(() => {
  const KEY = "__CHAT2API_VOICE_MAIN_V1__";
  if (globalThis[KEY]) return;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const LIVE_VIDEO_FRAME_RATE = 5;
  const MAX_LIVE_VIDEO_FRAMES = 300;
  const MAX_LIVE_VIDEO_FRAME_BYTES = 2 * 1024 * 1024;
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
    recorderCapture: null,
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
    sessionPeerCreatedBaseline: 0,
    sessionMicRequestedBaseline: 0,
    lastTransportError: "",
    iceConnectionState: "unknown",
    peerConnectionState: "unknown",
    iceCandidateErrors: 0,
    liveVideoCanvas: null,
    liveVideoContext: null,
    liveVideoStream: null,
    liveVideoTrack: null,
    liveVideoLastTimestamp: -1,
    liveVideoLastSequence: -1,
    liveVideoFrameCount: 0,
    liveVideoFrameRequestMode: "",
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
      const recordingState = { soundStarted: false, lastSoundAt: 0, discarded: false };
      state.recorderCapture = recordingState;
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
          state.recorderCapture = null;
        }
        if (recordingState.discarded) return;
        try {
          const blob = new Blob(chunks, { type: recordedMime });
          const buffer = await blob.arrayBuffer();
          // reset/prepare may run while arrayBuffer() awaits. The onstop
          // callback has already detached recorderCapture, so re-check the
          // request lease immediately before exposing encoded old audio.
          if (recordingState.discarded || state.requestId !== ownerRequestId) return;
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
      const ownerRequestId = state.requestId;
      state.peerConnectionsCreated += 1;
      pc.addEventListener("iceconnectionstatechange", () => {
        if (ownerRequestId && state.requestId === ownerRequestId) {
          state.iceConnectionState = String(pc.iceConnectionState || "unknown");
          post("voice.transport.ice", { ice_connection_state:state.iceConnectionState }, ownerRequestId);
        }
      });
      pc.addEventListener("connectionstatechange", () => {
        if (ownerRequestId && state.requestId === ownerRequestId) {
          state.peerConnectionState = String(pc.connectionState || "unknown");
          post("voice.transport.connection", { peer_connection_state:state.peerConnectionState }, ownerRequestId);
        }
      });
      pc.addEventListener("icecandidateerror", event => {
        if (ownerRequestId && state.requestId === ownerRequestId) {
          state.iceCandidateErrors += 1;
          post("voice.transport.ice-error", {
            error_code: Number(event?.errorCode || 0),
            // Never log ICE candidate URLs or network addresses.
            ice_candidate_errors: state.iceCandidateErrors,
          }, ownerRequestId);
        }
      });
      pc.addEventListener("track", event => {
        // A peer created by a prior Voice session can still fire a late
        // remote-track event after the tab starts a new request. Never
        // attribute that old audio to the new request/MediaRecorder.
        if (!ownerRequestId || state.requestId !== ownerRequestId) return;
        attachRemoteTrack(event.track);
      });
      const nativeAddTrack = typeof pc.addTrack === "function" ? pc.addTrack.bind(pc) : null;
      if (nativeAddTrack) {
        try {
          pc.addTrack = function chat2apiAddTrack(track, ...streams) {
            const sender = nativeAddTrack(track, ...streams);
            const videoRequestId = state.requestId;
            if (videoRequestId && track?.kind === "video" && track === state.liveVideoTrack) {
              post("voice.live.video.sender.attached", {
                track_state: String(track.readyState || "unknown"),
              }, videoRequestId);
            }
            return sender;
          };
        } catch (_) {}
      }
      const nativeRemoveTrack = typeof pc.removeTrack === "function"
        ? pc.removeTrack.bind(pc) : null;
      if (nativeRemoveTrack) {
        try {
          pc.removeTrack = function chat2apiRemoveTrack(sender) {
            const removedTrack = sender?.track;
            const removeRequestId = state.requestId;
            const result = nativeRemoveTrack(sender);
            if (removeRequestId && state.requestId === removeRequestId &&
                removedTrack === state.liveVideoTrack) {
              post("voice.live.video.sender.detached", {
                reason: "sender-removed",
              }, removeRequestId);
            }
            return result;
          };
        } catch (_) {}
      }
      const nativeAddTransceiver = typeof pc.addTransceiver === "function"
        ? pc.addTransceiver.bind(pc) : null;
      if (nativeAddTransceiver) {
        try {
          pc.addTransceiver = function chat2apiAddTransceiver(trackOrKind, init) {
            const transceiver = nativeAddTransceiver(trackOrKind, init);
            const videoRequestId = state.requestId;
            if (videoRequestId && trackOrKind?.kind === "video" && trackOrKind === state.liveVideoTrack) {
              post("voice.live.video.sender.attached", {
                track_state: String(trackOrKind.readyState || "unknown"),
              }, videoRequestId);
            }
            return transceiver;
          };
        } catch (_) {}
      }
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

  async function resumeSyntheticMicContext(context) {
    try {
      await context.resume();
    } catch (error) {
      throw new Error("ChatGPT Voice synthetic microphone AudioContext could not resume: " + String(error?.message || error));
    }
    if (typeof context.state === "string" && context.state !== "running") {
      throw new Error("ChatGPT Voice synthetic microphone AudioContext is not running (" + context.state + ")");
    }
  }

  async function ensureSyntheticMic() {
    if (state.micDestination && state.micContext && state.micContext.state !== "closed") {
      const existingContext = state.micContext;
      await resumeSyntheticMicContext(existingContext);
      const existingTracks = state.micDestination.stream?.getAudioTracks?.() || [];
      if (existingTracks.length && existingTracks.every(track => track.readyState !== "ended")) {
        return state.micDestination.stream;
      }
      // ChatGPT may stop the MediaStreamTrack it received when its Voice UI
      // closes. The AudioContext can remain running while its destination track
      // is ended; rebuild it instead of returning a non-functional stream.
      try { state.micOscillator?.stop(); } catch (_) {}
      try { await existingContext.close(); } catch (_) {}
      if (state.micContext === existingContext) {
        state.micContext = null;
        state.micDestination = null;
        state.micOscillator = null;
      }
    }
    // A closed AudioContext or an ended destination track must never be
    // returned to ChatGPT as a ready microphone on the next Voice session.
    state.micContext = null;
    state.micDestination = null;
    state.micOscillator = null;
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
    try {
      await resumeSyntheticMicContext(context);
      const tracks = destination.stream?.getAudioTracks?.() || [];
      if (!tracks.length || tracks.some(track => track.readyState === "ended")) {
        throw new Error("ChatGPT Voice synthetic microphone returned no live audio track");
      }
    } catch (error) {
      try { oscillator.stop(); } catch (_) {}
      try { await context.close(); } catch (_) {}
      if (state.micContext === context) {
        state.micContext = null;
        state.micDestination = null;
        state.micOscillator = null;
      }
      throw error;
    }
    return destination.stream;
  }

  function cleanupLiveVideo() {
    const stream = state.liveVideoStream;
    // Clear ownership before stop(): stop() fires "ended" synchronously in
    // some browsers, and an intentional teardown must not look like a sender
    // failure that forces a still-image fallback in the next Voice session.
    state.liveVideoTrack = null;
    state.liveVideoCanvas = null;
    state.liveVideoContext = null;
    state.liveVideoStream = null;
    if (stream?.getTracks) {
      for (const track of stream.getTracks()) {
        try { track.stop(); } catch (_) {}
      }
    }
    state.liveVideoLastTimestamp = -1;
    state.liveVideoLastSequence = -1;
    state.liveVideoFrameCount = 0;
  }

  function prepareLiveVideo(ownerRequestId) {
    if (!ownerRequestId || state.requestId !== ownerRequestId || state.mode !== "live") {
      throw new Error("Live video preparation does not own the active Voice request");
    }
    cleanupLiveVideo();
    const canvas = document.createElement("canvas");
    canvas.width = 1280;
    canvas.height = 720;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context || typeof canvas.captureStream !== "function") {
      throw new Error("This ChatGPT browser cannot create a synthetic live video track");
    }
    context.fillStyle = "#000";
    context.fillRect(0, 0, canvas.width, canvas.height);
    let stream = null;
    let track = null;
    try {
      // Manual frame requests let the camera track use the same audio clock
      // as synthetic microphone PCM. Older implementations fall back to the
      // bounded 5 FPS timer-driven canvas capture.
      stream = canvas.captureStream(0);
      track = stream?.getVideoTracks?.()[0] || null;
    } catch (_) {}
    if (!track || typeof track.requestFrame !== "function") {
      for (const item of stream?.getTracks?.() || []) {
        try { item.stop(); } catch (_) {}
      }
      stream = canvas.captureStream(LIVE_VIDEO_FRAME_RATE);
      track = stream?.getVideoTracks?.()[0] || null;
    }
    if (!track || track.readyState === "ended") {
      for (const item of stream?.getTracks?.() || []) {
        try { item.stop(); } catch (_) {}
      }
      throw new Error("Synthetic live video track did not become active");
    }
    try { track.contentHint = "motion"; } catch (_) {}
    try {
      track.addEventListener?.("ended", () => {
        if (state.requestId !== ownerRequestId || state.liveVideoTrack !== track) return;
        post("voice.live.video.track.ended", {
          track_state: String(track.readyState || "ended"),
        }, ownerRequestId);
      }, { once: true });
    } catch (_) {}
    state.liveVideoCanvas = canvas;
    state.liveVideoContext = context;
    state.liveVideoStream = stream;
    state.liveVideoTrack = track;
    state.liveVideoLastTimestamp = -1;
    state.liveVideoLastSequence = -1;
    state.liveVideoFrameCount = 0;
    state.liveVideoFrameRequestMode = typeof track.requestFrame === "function" ? "manual" : "timed_5fps";
    return track;
  }

  async function waitForLiveAVFrameClock(ownerRequestId,timestampMs) {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const clock = state.liveAVClock;
      if (clock?.requestId === ownerRequestId &&
          Number.isFinite(clock.clientOriginMs) && Number.isFinite(clock.audioOriginSec)) break;
      await delay(20);
      if (state.requestId !== ownerRequestId) {
        throw new Error("Live video session ended while waiting for the shared media clock");
      }
    }
    const clock = state.liveAVClock;
    const audioContext = state.micContext;
    if (!(clock?.requestId === ownerRequestId && audioContext &&
          Number.isFinite(clock.clientOriginMs) && Number.isFinite(clock.audioOriginSec) &&
          Number.isFinite(audioContext.currentTime))) {
      return {mode:"arrival_fallback",syncSkewMs:null};
    }
    const targetAudioTime = clock.audioOriginSec + (timestampMs - clock.clientOriginMs) / 1000;
    const waitMs = Math.max(0,(targetAudioTime - audioContext.currentTime) * 1000);
    if (waitMs > 0) await delay(Math.min(waitMs,750));
    if (state.requestId !== ownerRequestId) {
      throw new Error("Live video session ended before the synchronized frame was rendered");
    }
    return {
      mode:"shared_audio_context",
      syncSkewMs:Math.round((audioContext.currentTime - targetAudioTime) * 1000),
    };
  }

  async function drawLiveVideoFrame(message) {
    const ownerRequestId = String(message.request_id || "");
    if (!ownerRequestId || state.requestId !== ownerRequestId ||
        state.mode !== "live" || !state.liveVideoTrack ||
        state.liveVideoTrack.readyState === "ended") {
      throw new Error("Synthetic live video track is not active for this request");
    }
    const sequence = Number(message.sequence);
    const timestampMs = Number(message.timestamp_ms);
    const mime = String(message.mime_type || "image/jpeg").toLowerCase();
    if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence >= MAX_LIVE_VIDEO_FRAMES ||
        state.liveVideoFrameCount >= MAX_LIVE_VIDEO_FRAMES ||
        !Number.isSafeInteger(timestampMs) || timestampMs < 0 ||
        sequence <= state.liveVideoLastSequence ||
        timestampMs <= state.liveVideoLastTimestamp) {
      throw new Error("Live video frame sequence and timestamp must increase monotonically");
    }
    if (!["image/jpeg", "image/png", "image/webp"].includes(mime)) {
      throw new Error("Live video frames must be JPEG, PNG or WebP images");
    }
    const bytes = decodeBase64(message.base64 || "");
    if (!bytes.byteLength || bytes.byteLength > MAX_LIVE_VIDEO_FRAME_BYTES) {
      throw new Error("Live video frame must be between 1 byte and 2 MiB");
    }
    const bitmap = await createImageBitmap(new Blob([bytes], { type: mime }));
    try {
      if (state.requestId !== ownerRequestId || !state.liveVideoContext ||
          !state.liveVideoTrack || state.liveVideoTrack.readyState === "ended") {
        throw new Error("Live video session ended before the frame could be rendered");
      }
      const avSync = await waitForLiveAVFrameClock(ownerRequestId,timestampMs);
      const avSyncMode = avSync.mode;
      const syncSkewMs = avSync.syncSkewMs;
      const context = state.liveVideoContext;
      const canvas = state.liveVideoCanvas;
      const scale = Math.min(canvas.width / bitmap.width, canvas.height / bitmap.height);
      const width = Math.max(1, Math.round(bitmap.width * scale));
      const height = Math.max(1, Math.round(bitmap.height * scale));
      const left = Math.floor((canvas.width - width) / 2);
      const top = Math.floor((canvas.height - height) / 2);
      context.fillStyle = "#000";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(bitmap, left, top, width, height);
      if (typeof state.liveVideoTrack.requestFrame === "function") {
        state.liveVideoTrack.requestFrame();
      }
      state.liveVideoLastTimestamp = timestampMs;
      state.liveVideoLastSequence = sequence;
      state.liveVideoFrameCount += 1;
      post("voice.live.video.frame.received", {
        sequence, timestamp_ms: timestampMs, frame_count: state.liveVideoFrameCount,
        av_sync_mode: avSyncMode,
        sync_skew_ms: syncSkewMs,
      }, ownerRequestId);
    } finally {
      try { bitmap.close?.(); } catch (_) {}
    }
  }

  const NativeRtpSender = globalThis.RTCRtpSender;
  if (NativeRtpSender?.prototype && typeof NativeRtpSender.prototype.replaceTrack === "function") {
    const nativeReplaceTrack = NativeRtpSender.prototype.replaceTrack;
    try {
      NativeRtpSender.prototype.replaceTrack = function chat2apiReplaceTrack(track) {
        const ownerRequestId = state.requestId;
        const previousTrack = this.track;
        const result = nativeReplaceTrack.call(this, track);
        const reportReplacement = () => {
          if (!ownerRequestId || state.requestId !== ownerRequestId) return;
          if (previousTrack === state.liveVideoTrack && track !== state.liveVideoTrack) {
            post("voice.live.video.sender.detached", {
              reason: "sender-replaced",
              replacement_track_kind: String(track?.kind || "none"),
            }, ownerRequestId);
          }
          if (track?.kind === "video" && track === state.liveVideoTrack) {
            post("voice.live.video.sender.attached", {
              track_state: String(track.readyState || "unknown"),
            }, ownerRequestId);
          }
        };
        if (result && typeof result.then === "function") {
          return result.then(value => { reportReplacement(); return value; });
        }
        reportReplacement();
        return result;
      };
    } catch (_) {}
  }

  const mediaDevices = navigator.mediaDevices;
  const nativeGetUserMedia = mediaDevices?.getUserMedia?.bind(mediaDevices);
  if (mediaDevices && nativeGetUserMedia) {
    const intercepted = async function chat2apiGetUserMedia(constraints) {
      if (state.requestId && state.mode === "live" && constraints?.video) {
        if (!state.liveVideoTrack || state.liveVideoTrack.readyState === "ended") {
          throw new Error("Chat2API live video track was not prepared before ChatGPT requested its camera");
        }
        const tracks = [];
        if (constraints?.audio) {
          state.micRequestsSeen += 1;
          post("voice.mic.requested", { mode: state.mode, mic_requests_seen: state.micRequestsSeen });
          const synthetic = await ensureSyntheticMic();
          const audioTracks = [...synthetic.getAudioTracks()];
          if (!audioTracks.length || audioTracks.some(track => track.readyState === "ended")) {
            throw new Error("ChatGPT Voice synthetic microphone returned no live audio track");
          }
          tracks.push(...audioTracks);
          post("voice.mic.synthetic", { track_count: audioTracks.length, mode: state.mode });
        }
        tracks.push(state.liveVideoTrack);
        post("voice.live.video.track.provided", {
          track_state: String(state.liveVideoTrack.readyState || "unknown"),
        });
        return new MediaStream(tracks);
      }
      if (state.requestId && constraints?.audio) {
        state.micRequestsSeen += 1;
        post("voice.mic.requested", { mode: state.mode, mic_requests_seen: state.micRequestsSeen });
        let synthetic;
        try {
          synthetic = await ensureSyntheticMic();
        } catch (error) {
          if (state.mode === "live") {
            post("voice.live.error", { error: String(error?.message || error) });
          }
          throw error;
        }
        const tracks = [...synthetic.getAudioTracks()];
        if (!tracks.length || tracks.some(track => track.readyState === "ended")) {
          const error = new Error("ChatGPT Voice synthetic microphone returned no live audio track");
          if (state.mode === "live") post("voice.live.error", { error: error.message });
          throw error;
        }
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
    await resumeSyntheticMicContext(context);
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
      cleanupLiveVideo();
      // A previous recorder may still be finalizing asynchronously.
      // Detach it before starting this request; its callbacks carry its own ID.
      if (state.recorderCapture) state.recorderCapture.discarded = true;
      if (state.recorder && state.recorder.state !== "inactive") {
        try { state.recorder.stop(); } catch (_) {}
      }
      stopAnalyser();
      state.recorder = null;
      state.recorderCapture = null;
      state.requestId = message.request_id || null;
      state.sessionPeerCreatedBaseline = state.peerConnectionsCreated;
      state.sessionMicRequestedBaseline = state.micRequestsSeen;
      state.mode = message.mode || "speech";
      state.preparedInput = message.input_base64 ? { base64: message.input_base64, mime_type: message.input_mime || "" } : null;
      state.remoteTrackSeen = false;
      state.remoteTrackSeenAt = 0;
      state.remoteTrack = null;
      state.iceConnectionState = "unknown";
      state.peerConnectionState = "unknown";
      state.iceCandidateErrors = 0;
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
    } else if (message.type === "voice.live.video.prepare" && message.request_id === state.requestId) {
      try {
        const track = prepareLiveVideo(state.requestId);
        post("voice.live.video.prepared", {
          track_state: String(track.readyState || "unknown"),
          frame_rate: LIVE_VIDEO_FRAME_RATE,
        });
      } catch (error) {
        post("voice.live.video.error", { error: String(error?.message || error) });
      }
    } else if (message.type === "voice.live.video.frame" && message.request_id === state.requestId) {
      drawLiveVideoFrame(message).catch(error => post("voice.live.video.error", {
        sequence: Number(message.sequence),
        timestamp_ms: Number(message.timestamp_ms),
        error: String(error?.message || error),
      }, state.requestId));
    } else if (message.type === "voice.transport.snapshot" && message.request_id === state.requestId) {
      post("voice.transport.snapshot", {
        peer_connections_in_request: Math.max(0, state.peerConnectionsCreated - state.sessionPeerCreatedBaseline),
        mic_requests_in_request: Math.max(0, state.micRequestsSeen - state.sessionMicRequestedBaseline),
        peer_hook_ready: Boolean(state.peerHookReady && globalThis.RTCPeerConnection === state.peerHook),
        mic_hook_ready: Boolean(state.micHookReady && mediaDevices?.getUserMedia === state.micHook),
        remote_track_seen: state.remoteTrackSeen,
        page_visibility: String(document.visibilityState || "unknown"),
        page_has_focus: typeof document.hasFocus === "function" ? Boolean(document.hasFocus()) : null,
        ice_connection_state: state.iceConnectionState,
        peer_connection_state: state.peerConnectionState,
        ice_candidate_errors: state.iceCandidateErrors,
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
      state.captureArmed = false;
      if (state.recorder && state.recorder.state !== "inactive") state.recorder.stop();
      else post("voice.record.error", { error: "Remote voice audio track was not captured" });
    } else if (message.type === "voice.reset" && message.request_id === state.requestId) {
      cleanupLiveVideo();
      if (state.recorderCapture) state.recorderCapture.discarded = true;
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