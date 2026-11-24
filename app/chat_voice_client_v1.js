/* Browser-side Chat2API GPT-Live client for Web Chat. */
(() => {
  "use strict";

  const INPUT_RATE = 16000;
  const OUTPUT_RATE = 24000;
  const MAX_VIDEO_FRAME_BYTES = 2 * 1024 * 1024;
  const MAX_VIDEO_FRAMES = 64;
  // The Broker allows up to 5 seconds for the Worker to drain its final
  // interruption snapshot. Leave network/terminal-delivery margin before the
  // browser gives up and closes the public WebSocket.
  const SESSION_CLOSE_TIMEOUT_MS = 8000;
  const SESSION_READY_TIMEOUT_MS = 90000;
  const DEFAULT_SESSION_INSTRUCTIONS = "请用自然、清晰、简洁的中文与用户进行实时语音对话。需要时结合用户提供的摄像头画面回答。";
  const MAX_SESSION_INSTRUCTIONS_CHARS = 4000;
  const MAX_SESSION_CONTEXT_CHARS = 8000;

  function sliceCodePoints(value, start, end) {
    return Array.from(String(value || "")).slice(start, end).join("");
  }

  function resamplePcm16(input, inputRate, outputRate = INPUT_RATE) {
    if (!input || typeof input.length !== "number" || !Number.isFinite(inputRate) ||
        !Number.isFinite(outputRate) || inputRate < outputRate || outputRate < 8000 ||
        outputRate > 48000) {
      throw new TypeError("Invalid PCM resampling input");
    }
    const count = Math.floor(input.length * outputRate / inputRate);
    if (!count) return new ArrayBuffer(0);
    const buffer = new ArrayBuffer(count * 2);
    const view = new DataView(buffer);
    const ratio = inputRate / outputRate;
    for (let index = 0; index < count; index += 1) {
      const start = Math.floor(index * ratio);
      const end = Math.min(input.length, Math.max(start + 1, Math.floor((index + 1) * ratio)));
      let sum = 0;
      for (let sample = start; sample < end; sample += 1) sum += input[sample];
      const value = Math.max(-1, Math.min(1, sum / (end - start)));
      view.setInt16(index * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
    }
    return buffer;
  }

  function encodePcm16(samples) {
    const buffer = new ArrayBuffer(samples.length * 2);
    const view = new DataView(buffer);
    for (let index = 0; index < samples.length; index += 1) {
      const value = Math.max(-1,Math.min(1,samples[index]));
      view.setInt16(index * 2,value < 0 ? value * 0x8000 : value * 0x7fff,true);
    }
    return buffer;
  }

  // Keep the fractional input position and one previous sample across audio
  // callbacks. Stateless per-buffer rounding otherwise accumulates clock drift.
  function resamplePcm16Stream(input,inputRate,state,outputRate = INPUT_RATE) {
    if (!input || typeof input.length !== "number" || !state ||
        !Number.isFinite(inputRate) || inputRate < outputRate ||
        outputRate < 8000 || outputRate > 48000) {
      throw new TypeError("Invalid streaming PCM resampling input");
    }
    const pending = state.pending instanceof Float32Array ? state.pending : new Float32Array(0);
    const combined = new Float32Array(pending.length + input.length);
    combined.set(pending,0);
    combined.set(input,pending.length);
    let position = Math.max(0,Number(state.position) || 0);
    const step = inputRate / outputRate;
    const output = [];
    while (position + 1 < combined.length) {
      const index = Math.floor(position);
      const fraction = position - index;
      output.push(combined[index] * (1 - fraction) + combined[index + 1] * fraction);
      position += step;
    }
    const drop = Math.max(0,Math.floor(position) - 1);
    state.pending = combined.slice(drop);
    state.position = position - drop;
    return encodePcm16(output);
  }

  function decodePcm16(buffer) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength % 2 !== 0) return null;
    const view = new DataView(buffer);
    const output = new Float32Array(buffer.byteLength / 2);
    for (let index = 0; index < output.length; index += 1) {
      output[index] = view.getInt16(index * 2, true) / 32768;
    }
    return output;
  }

  function arrayBufferToBase64(buffer, encode = value => btoa(value)) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, Math.min(bytes.length, offset + 0x8000)));
    }
    return encode(binary);
  }

  function create(options = {}) {
    const token = String(options.apiToken || "");
    const instructions = sliceCodePoints(String(options.instructions || DEFAULT_SESSION_INSTRUCTIONS).trim(),0,MAX_SESSION_INSTRUCTIONS_CHARS);
    const context = sliceCodePoints(String(options.context || "").trim(),0,MAX_SESSION_CONTEXT_CHARS);
    const beforeConnect = typeof options.beforeConnect === "function" ? options.beforeConnect : null;
    const fetcher = options.fetcher || window.fetch.bind(window);
    const WebSocketImpl = options.WebSocketImpl || window.WebSocket;
    const AudioContextImpl = options.AudioContextImpl || window.AudioContext || window.webkitAudioContext;
    const mediaDevices = options.mediaDevices || window.navigator?.mediaDevices;
    const location = options.location || window.location;
    const onEvent = typeof options.onEvent === "function" ? options.onEvent : () => {};
    const onState = typeof options.onState === "function" ? options.onState : () => {};
    const timers = options.timers || window;
    const clock = typeof options.now === "function"
      ? options.now
      : () => (typeof window.performance?.now === "function" ? window.performance.now() : Date.now());

    let socket = null;
    let audioContext = null;
    let audioContextStateListener = null;
    let microphoneStream = null;
    let microphoneSource = null;
    let microphoneProcessor = null;
    let silentGain = null;
    let cameraStream = null;
    let cameraTimer = null;
    let heartbeatTimer = null;
    let videoElement = null;
    let canvas = null;
    let nextPlaybackTime = 0;
    let activePlaybackSources = new Set();
    let ready = false;
    let started = false;
    let starting = false;
    let stopping = false;
    let cleaned = false;
    let cameraGeneration = 0;
    let cameraStartingGeneration = null;
    let cleanupPromise = null;
    let readyResolve = null;
    let readyReject = null;
    let closeResolve = null;
    let videoPending = false;
    let pendingVideoItemId = "";
    let videoSequence = 0;
    let videoFrameCount = 0;
    let sessionStartedAt = 0;
    let lastVideoTimestampMs = -1;
    let lastVideoCaptureAt = -Infinity;
    let supportsVideoFrames = false;
    let readyTimeoutId = null;
    let closeTimeoutId = null;

    function monotonicNow() { return clock(); }
    function state(status, detail = "") {
      try { onState(status, detail); } catch {}
    }
    function event(payload) {
      try { onEvent(payload); } catch {}
    }
    function socketOpen() {
      return socket && socket.readyState === WebSocketImpl.OPEN;
    }
    function sendJson(payload) {
      if (!socketOpen()) return false;
      socket.send(JSON.stringify(payload));
      return true;
    }
    function stopPlayback() {
      nextPlaybackTime = audioContext ? audioContext.currentTime : 0;
      for (const source of activePlaybackSources) {
        try { source.stop(); } catch {}
      }
      activePlaybackSources.clear();
    }
    function failIfAudioContextSuspended() {
      if (!audioContext || cleaned || stopping ||
          typeof audioContext.state !== "string" || audioContext.state === "running") return false;
      event({
        type:"client.error",
        message:"浏览器音频输出上下文已暂停（" + audioContext.state + "），实时语音已停止，请重新连接",
      });
      void cleanup();
      return true;
    }
    function playPcm(buffer) {
      if (!audioContext || cleaned || failIfAudioContextSuspended()) return;
      const samples = decodePcm16(buffer);
      if (!samples || !samples.length) return;
      const audio = audioContext.createBuffer(1, samples.length, OUTPUT_RATE);
      audio.copyToChannel(samples, 0);
      const source = audioContext.createBufferSource();
      source.buffer = audio;
      source.connect(audioContext.destination);
      const startAt = Math.max(nextPlaybackTime, audioContext.currentTime + 0.015);
      nextPlaybackTime = startAt + audio.duration;
      activePlaybackSources.add(source);
      source.onended = () => activePlaybackSources.delete(source);
      source.start(startAt);
    }
    function startMicrophonePipeline() {
      if (!microphoneStream || !audioContext || microphoneProcessor) return;
      if (typeof audioContext.createScriptProcessor !== "function") {
        throw new Error("当前浏览器不支持实时音频采集处理器");
      }
      microphoneSource = audioContext.createMediaStreamSource(microphoneStream);
      microphoneProcessor = audioContext.createScriptProcessor(4096, 1, 1);
      const resamplerState = {pending:new Float32Array(0),position:0};
      silentGain = audioContext.createGain();
      silentGain.gain.value = 0;
      microphoneProcessor.onaudioprocess = input => {
        if (!ready || stopping || !socketOpen()) return;
        const channel = input.inputBuffer?.getChannelData?.(0);
        if (!channel || !channel.length) return;
        try {
          const frame = resamplePcm16Stream(channel, audioContext.sampleRate, resamplerState, INPUT_RATE);
          if (frame.byteLength && frame.byteLength <= 64 * 1024) socket.send(frame);
        } catch (error) {
          event({type:"client.error",message:String(error?.message || error)});
        }
      };
      microphoneSource.connect(microphoneProcessor);
      microphoneProcessor.connect(silentGain);
      silentGain.connect(audioContext.destination);
    }
    function stopMicrophone() {
      if (microphoneProcessor) {
        microphoneProcessor.onaudioprocess = null;
        try { microphoneProcessor.disconnect(); } catch {}
      }
      if (microphoneSource) {
        try { microphoneSource.disconnect(); } catch {}
      }
      if (silentGain) {
        try { silentGain.disconnect(); } catch {}
      }
      microphoneProcessor = null;
      microphoneSource = null;
      silentGain = null;
      if (microphoneStream) {
        microphoneStream.getTracks().forEach(track => { try { track.stop(); } catch {} });
      }
      microphoneStream = null;
    }
    function stopCamera() {
      // Invalidate a getUserMedia()/play()/frame-upload continuation that is
      // still awaiting permission or I/O. It must not resurrect a stopped camera.
      cameraGeneration += 1;
      if (cameraTimer !== null) timers.clearInterval(cameraTimer);
      cameraTimer = null;
      if (cameraStream) cameraStream.getTracks().forEach(track => { try { track.stop(); } catch {} });
      cameraStream = null;
      if (videoElement) {
        try { videoElement.pause(); } catch {}
        videoElement.srcObject = null;
      }
      state(ready ? "ready" : "idle", "摄像头抽帧已停止");
    }
    async function cleanup() {
      if (cleanupPromise) return cleanupPromise;
      if (cleaned) return;
      cleaned = true;
      cleanupPromise = (async () => {
        ready = false;
        started = false;
        stopping = true;
        if (heartbeatTimer !== null) timers.clearInterval(heartbeatTimer);
        if (readyTimeoutId !== null) timers.clearTimeout(readyTimeoutId);
        if (closeTimeoutId !== null) timers.clearTimeout(closeTimeoutId);
        heartbeatTimer = null;
        readyTimeoutId = null;
        closeTimeoutId = null;
        stopCamera();
        stopMicrophone();
        stopPlayback();
        if (audioContext && audioContextStateListener) {
          try { audioContext.removeEventListener?.("statechange",audioContextStateListener); } catch {}
        }
        audioContextStateListener = null;
        if (audioContext && typeof audioContext.close === "function") {
          try { await audioContext.close(); } catch {}
        }
        audioContext = null;
        if (socket && socket.readyState < WebSocketImpl.CLOSING) {
          try { socket.close(); } catch {}
        }
        state("closed");
        if (closeResolve) closeResolve();
        closeResolve = null;
      })();
      return cleanupPromise;
    }
    function handleJson(payload) {
      if (!payload || typeof payload !== "object") return;
      if (payload.type === "session.ready") {
        if (cleaned || stopping) {
          // A late ready frame must not resurrect a session that the user has
          // already stopped while the permission/socket handshake was pending.
          try { socket?.close(); } catch {}
          return;
        }
        if (failIfAudioContextSuspended()) return;
        ready = true;
        supportsVideoFrames = Array.isArray(payload.supports) && payload.supports.includes("input_video.frame");
        sessionStartedAt = monotonicNow();
        if (readyResolve) readyResolve(payload);
        readyResolve = null;
        readyReject = null;
        state("ready", "GPT-Live 已连接");
      } else if (payload.type === "session.closed") {
        event(payload);
        if (closeResolve) closeResolve();
        void cleanup();
        return;
      } else if (payload.type === "input_video.frame.sent") {
        if (!payload.item_id || payload.item_id === pendingVideoItemId) {
          videoPending = false;
          pendingVideoItemId = "";
        }
      } else if (payload.type === "error") {
        if (payload.item_id && payload.item_id === pendingVideoItemId) {
          videoPending = false;
          pendingVideoItemId = "";
        }
        event(payload);
      }
      if (payload.type === "input_audio_buffer.speech_started" ||
          payload.type === "response.interrupted") stopPlayback();
      if (payload.type !== "error" && payload.type !== "session.closed") event(payload);
    }
    function handleMessage(message) {
      if (message.data instanceof ArrayBuffer) {
        playPcm(message.data);
        return;
      }
      if (typeof Blob !== "undefined" && message.data instanceof Blob) {
        void message.data.arrayBuffer().then(playPcm).catch(() => {});
        return;
      }
      if (typeof message.data !== "string" || message.data.length > 64 * 1024) return;
      try { handleJson(JSON.parse(message.data)); }
      catch { event({type:"client.error",message:"服务端返回了无效的实时语音事件"}); }
    }
    async function start() {
      if (started || starting) return;
      if (!token) throw new Error("请先选择一个 API Key");
      if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function") {
        throw new Error("当前浏览器无法访问麦克风");
      }
      if (!WebSocketImpl || !AudioContextImpl) throw new Error("当前浏览器缺少 WebSocket 或 Web Audio 支持");
      starting = true;
      cleaned = false;
      cleanupPromise = null;
      stopping = false;
      const cancelled = () => cleaned || stopping;
      const assertNotCancelled = () => {
        if (cancelled()) throw new Error("GPT-Live startup was cancelled");
      };
      state("requesting_microphone", "等待麦克风授权");
      try {
      // Unlock the browser audio graph synchronously from the user's click
      // before any network or permission prompt can consume transient activation.
      audioContext = new AudioContextImpl();
      audioContextStateListener = () => {
        if (ready && !stopping && !cleaned) failIfAudioContextSuspended();
      };
      audioContext.addEventListener?.("statechange",audioContextStateListener);
      if (typeof audioContext.resume === "function") await audioContext.resume();
      if (typeof audioContext.state === "string" && audioContext.state !== "running") {
        throw new Error("浏览器音频输出上下文未进入运行状态（" + audioContext.state + "）");
      }
      assertNotCancelled();
      if (beforeConnect) await beforeConnect();
      assertNotCancelled();
      const requestedMicrophoneStream = await mediaDevices.getUserMedia({
        audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},
        video:false,
      });
      if (cancelled()) {
        requestedMicrophoneStream?.getTracks?.().forEach(track => { try { track.stop(); } catch {} });
        throw new Error("GPT-Live startup was cancelled");
      }
      microphoneStream = requestedMicrophoneStream;
      const response = await fetcher("/v1/audio/realtime/sessions", {
        method:"POST",
        credentials:"same-origin",
        headers:{Authorization:"Bearer " + token,Accept:"application/json"},
      });
      assertNotCancelled();
      let session = {};
      try { session = await response.json(); } catch {}
      assertNotCancelled();
      if (!response.ok || typeof session.websocket_path !== "string") {
        throw new Error(session.detail || "无法创建 GPT-Live 会话，HTTP " + response.status);
      }
      const pageUrl = new URL(location.href);
      const url = new URL(session.websocket_path, pageUrl);
      if (url.origin !== pageUrl.origin) {
        throw new Error("Live WebSocket must use the same-origin Chat2API endpoint");
      }
      url.protocol = pageUrl.protocol === "https:" ? "wss:" : "ws:";
      assertNotCancelled();
      socket = new WebSocketImpl(url.href);
      socket.binaryType = "arraybuffer";
      const readyPromise = new Promise((resolve,reject) => {
        readyResolve = resolve;
        readyReject = reject;
        readyTimeoutId = timers.setTimeout(() => {
          readyReject?.(new Error("等待 GPT-Live 就绪超时"));
          readyReject = null;
        },SESSION_READY_TIMEOUT_MS);
        socket.onopen = () => {
          if (cancelled()) {
            try { socket.close(); } catch {}
            return;
          }
          socket.send(JSON.stringify({
            type:"session.start",
            model:"gpt-live",
            instructions,
            context,
          }));
        };
        socket.onmessage = handleMessage;
        socket.onerror = () => {
          if (readyReject) {
            readyReject(new Error("GPT-Live WebSocket 连接失败"));
            readyReject = null;
          }
        };
        socket.onclose = () => {
          if (readyReject) {
            readyReject(new Error("GPT-Live 连接在就绪前关闭"));
            readyReject = null;
          }
          if (closeResolve) closeResolve();
          void cleanup();
        };
        const finishReady = readyResolve;
        readyResolve = payload => {
          if (readyTimeoutId !== null) timers.clearTimeout(readyTimeoutId);
          readyTimeoutId = null;
          finishReady(payload);
        };
      });
      await readyPromise;
      assertNotCancelled();
      if (!ready) throw new Error("GPT-Live 会话未能保持连接");
      startMicrophonePipeline();
      heartbeatTimer = timers.setInterval(() => {
        sendJson({type:"ping",timestamp:Date.now()});
      },20000);
      started = true;
      state("ready","麦克风已连接");
      } catch (error) {
        await cleanup();
        throw error;
      } finally {
        starting = false;
      }
    }
    async function blobBase64(blob) {
      const buffer = await blob.arrayBuffer();
      return arrayBufferToBase64(buffer, value => window.btoa(value));
    }
    function releaseUnsentVideoFrame(itemId) {
      if (pendingVideoItemId !== itemId) return;
      videoPending = false;
      pendingVideoItemId = "";
    }
    function cameraCaptureIsCurrent(generation,itemId) {
      return generation === cameraGeneration &&
        pendingVideoItemId === itemId && ready && !stopping && Boolean(cameraStream);
    }
    async function captureAndSendFrame() {
      if (!ready || stopping || !cameraStream || videoPending || videoFrameCount >= MAX_VIDEO_FRAMES) return;
      const captureGeneration = cameraGeneration;
      const captureAt = monotonicNow();
      if (captureAt - lastVideoCaptureAt < 1000) return;
      const width = Number(videoElement?.videoWidth || 0);
      const height = Number(videoElement?.videoHeight || 0);
      if (!width || !height) return;
      lastVideoCaptureAt = captureAt;
      videoPending = true;
      const sequence = videoSequence++;
      const itemId = "webchat-video-" + sequence;
      pendingVideoItemId = itemId;
      try {
        const scale = Math.min(1,1280 / width,720 / height);
        const targetWidth = Math.max(1,Math.round(width * scale));
        const targetHeight = Math.max(1,Math.round(height * scale));
        canvas = canvas || window.document.createElement("canvas");
        canvas.width = targetWidth;
        canvas.height = targetHeight;
        const context = canvas.getContext("2d",{alpha:false});
        context.drawImage(videoElement,0,0,targetWidth,targetHeight);
        let blob = await new Promise(resolve => canvas.toBlob(resolve,"image/jpeg",0.72));
        if (!cameraCaptureIsCurrent(captureGeneration,itemId)) {
          releaseUnsentVideoFrame(itemId);
          return;
        }
        if (!blob) throw new Error("无法编码摄像头画面");
        if (blob.size > MAX_VIDEO_FRAME_BYTES) {
          blob = await new Promise(resolve => canvas.toBlob(resolve,"image/jpeg",0.48));
          if (!cameraCaptureIsCurrent(captureGeneration,itemId)) {
            releaseUnsentVideoFrame(itemId);
            return;
          }
        }
        if (!blob || blob.size > MAX_VIDEO_FRAME_BYTES) throw new Error("摄像头画面超过 2 MiB 限制");
        const dataBase64 = await blobBase64(blob);
        if (!cameraCaptureIsCurrent(captureGeneration,itemId)) {
          releaseUnsentVideoFrame(itemId);
          return;
        }
        const upload = await fetcher("/v1/files", {
          method:"POST",
          credentials:"same-origin",
          headers:{
            Authorization:"Bearer " + token,
            "Content-Type":"application/json",
            "X-Chat2API-Source":"web-chat",
          },
          body:JSON.stringify({
            filename:"webchat-camera-" + sequence + ".jpg",
            mime_type:"image/jpeg",
            data_base64:dataBase64,
            purpose:"vision",
          }),
        });
        if (!cameraCaptureIsCurrent(captureGeneration,itemId)) {
          releaseUnsentVideoFrame(itemId);
          return;
        }
        let file = {};
        try { file = await upload.json(); } catch {}
        if (!cameraCaptureIsCurrent(captureGeneration,itemId)) {
          releaseUnsentVideoFrame(itemId);
          return;
        }
        const fileId = String(file.id || "");
        if (!upload.ok || !/^file_[A-Za-z0-9]{8,64}$/.test(fileId)) {
          throw new Error(file.detail || "摄像头画面上传失败");
        }
        if (!sendJson({
          type:"input_video.frame",
          item_id:itemId,
          sequence,
          timestamp_ms:(lastVideoTimestampMs = Math.max(
            lastVideoTimestampMs + 1,
            Math.floor(captureAt - sessionStartedAt),
          )),
          file_id:fileId,
          text:"摄像头画面",
        })) throw new Error("实时语音连接已断开");
        videoFrameCount += 1;
        event({type:"input_video.frame.queued",sequence});
        if (videoFrameCount >= MAX_VIDEO_FRAMES) {
          stopCamera();
          event({type:"client.notice",message:"已达到本次语音会话 64 帧上限，摄像头抽帧已停止"});
        }
      } catch (error) {
        const stillCurrent = cameraCaptureIsCurrent(captureGeneration,itemId);
        releaseUnsentVideoFrame(itemId);
        if (!stillCurrent) return;
        event({type:"client.error",message:String(error?.message || error)});
        stopCamera();
      }
    }
    async function startCamera(targetVideo) {
      if (!ready || stopping || cleaned) throw new Error("请先连接 GPT-Live 语音");
      if (!supportsVideoFrames) throw new Error("当前 Worker 会话不支持摄像头抽帧");
      if (videoFrameCount >= MAX_VIDEO_FRAMES) throw new Error("本次语音会话已达到 64 帧上限");
      if (videoPending) throw new Error("上一张摄像头画面尚未得到 Worker 确认，请稍后重试");
      if (cameraStream) return;
      if (cameraStartingGeneration !== null) throw new Error("摄像头权限请求正在进行，请稍后重试");
      if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function") {
        throw new Error("当前浏览器无法访问摄像头");
      }
      if (!targetVideo) throw new Error("摄像头预览元素不可用");
      const generation = ++cameraGeneration;
      cameraStartingGeneration = generation;
      const cancelled = () => generation !== cameraGeneration || stopping || cleaned || !ready;
      state("camera_starting","正在请求摄像头权限");
      videoElement = targetVideo;
      let requestedCameraStream = null;
      try {
        requestedCameraStream = await mediaDevices.getUserMedia({
          video:{facingMode:"environment",width:{ideal:1280},height:{ideal:720}},
          audio:false,
        });
        if (cancelled()) {
          requestedCameraStream.getTracks().forEach(track => { try { track.stop(); } catch {} });
          throw new Error("摄像头启动已取消");
        }
        cameraStream = requestedCameraStream;
        videoElement.srcObject = cameraStream;
        if (typeof videoElement.play === "function") await videoElement.play();
        if (cancelled()) {
          requestedCameraStream.getTracks().forEach(track => { try { track.stop(); } catch {} });
          if (cameraStream === requestedCameraStream) cameraStream = null;
          if (videoElement?.srcObject === requestedCameraStream) videoElement.srcObject = null;
          throw new Error("摄像头启动已取消");
        }
        cameraTimer = timers.setInterval(() => { void captureAndSendFrame(); },1000);
        void captureAndSendFrame();
        state("camera_ready","摄像头已开启（每秒最多发送一张快照）");
      } catch (error) {
        if (generation === cameraGeneration) stopCamera();
        throw error;
      } finally {
        if (cameraStartingGeneration === generation) cameraStartingGeneration = null;
      }
    }
    async function stop() {
      if (!socket || !socketOpen()) {
        await cleanup();
        return;
      }
      stopping = true;
      stopCamera();
      if (!ready) {
        try { socket.close(); } catch {}
        await cleanup();
        return;
      }
      state("stopping","正在结束实时语音");
      const closed = new Promise(resolve => {
        closeResolve = resolve;
        closeTimeoutId = timers.setTimeout(resolve,SESSION_CLOSE_TIMEOUT_MS);
      });
      sendJson({type:"session.finish"});
      await closed;
      if (socket && socket.readyState < WebSocketImpl.CLOSING) {
        try { socket.close(); } catch {}
      }
      await cleanup();
    }
    return Object.freeze({
      start,
      stop,
      startCamera,
      stopCamera,
      isActive:() => ready && !stopping,
      isCameraActive:() => Boolean(cameraStream),
    });
  }

  window.Chat2APIVoiceClientV1 = Object.freeze({create,resamplePcm16,resamplePcm16Stream,decodePcm16});
})();