/* Browser-side Chat2API GPT-Live client for Web Chat. */
(() => {
  "use strict";

  const INPUT_RATE = 16000;
  const OUTPUT_RATE = 24000;
  const MAX_VIDEO_FRAME_BYTES = 2 * 1024 * 1024;
  const MAX_VIDEO_FRAMES = 64;

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
    const fetcher = options.fetcher || window.fetch.bind(window);
    const WebSocketImpl = options.WebSocketImpl || window.WebSocket;
    const AudioContextImpl = options.AudioContextImpl || window.AudioContext || window.webkitAudioContext;
    const mediaDevices = options.mediaDevices || window.navigator?.mediaDevices;
    const location = options.location || window.location;
    const onEvent = typeof options.onEvent === "function" ? options.onEvent : () => {};
    const onState = typeof options.onState === "function" ? options.onState : () => {};
    const timers = options.timers || window;

    let socket = null;
    let audioContext = null;
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
    let stopping = false;
    let cleaned = false;
    let cleanupPromise = null;
    let readyResolve = null;
    let readyReject = null;
    let closeResolve = null;
    let videoPending = false;
    let pendingVideoItemId = "";
    let videoSequence = 0;
    let videoFrameCount = 0;
    let sessionStartedAt = 0;
    let supportsVideoFrames = false;
    let readyTimeoutId = null;
    let closeTimeoutId = null;

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
    function playPcm(buffer) {
      if (!audioContext || cleaned) return;
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
      silentGain = audioContext.createGain();
      silentGain.gain.value = 0;
      microphoneProcessor.onaudioprocess = input => {
        if (!ready || stopping || !socketOpen()) return;
        const channel = input.inputBuffer?.getChannelData?.(0);
        if (!channel || !channel.length) return;
        try {
          const frame = resamplePcm16(channel, audioContext.sampleRate, INPUT_RATE);
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
        ready = true;
        supportsVideoFrames = Array.isArray(payload.supports) && payload.supports.includes("input_video.frame");
        sessionStartedAt = Date.now();
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
      if (started) return;
      if (!token) throw new Error("请先选择一个 API Key");
      if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function") {
        throw new Error("当前浏览器无法访问麦克风");
      }
      if (!WebSocketImpl || !AudioContextImpl) throw new Error("当前浏览器缺少 WebSocket 或 Web Audio 支持");
      cleaned = false;
      cleanupPromise = null;
      stopping = false;
      state("requesting_microphone", "等待麦克风授权");
      try {
      microphoneStream = await mediaDevices.getUserMedia({
        audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},
        video:false,
      });
      audioContext = new AudioContextImpl();
      if (typeof audioContext.resume === "function") await audioContext.resume();
      const response = await fetcher("/v1/audio/realtime/sessions", {
        method:"POST",
        credentials:"same-origin",
        headers:{Authorization:"Bearer " + token,Accept:"application/json"},
      });
      let session = {};
      try { session = await response.json(); } catch {}
      if (!response.ok || typeof session.websocket_path !== "string") {
        throw new Error(session.detail || "无法创建 GPT-Live 会话，HTTP " + response.status);
      }
      const url = new URL(session.websocket_path, location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocketImpl(url.href);
      socket.binaryType = "arraybuffer";
      const readyPromise = new Promise((resolve,reject) => {
        readyResolve = resolve;
        readyReject = reject;
        readyTimeoutId = timers.setTimeout(() => {
          readyReject?.(new Error("等待 GPT-Live 就绪超时"));
          readyReject = null;
        },30000);
        socket.onopen = () => {
          socket.send(JSON.stringify({
            type:"session.start",
            model:"gpt-live",
            instructions:"请用自然、清晰、简洁的中文与用户进行实时语音对话。需要时结合用户提供的摄像头画面回答。",
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
      if (cleaned || !ready) throw new Error("GPT-Live 会话未能保持连接");
      startMicrophonePipeline();
      heartbeatTimer = timers.setInterval(() => {
        sendJson({type:"ping",timestamp:Date.now()});
      },20000);
      started = true;
      state("ready","麦克风已连接");
      } catch (error) {
        await cleanup();
        throw error;
      }
    }
    async function blobBase64(blob) {
      const buffer = await blob.arrayBuffer();
      return arrayBufferToBase64(buffer, value => window.btoa(value));
    }
    async function captureAndSendFrame() {
      if (!ready || stopping || !cameraStream || videoPending || videoFrameCount >= MAX_VIDEO_FRAMES) return;
      const width = Number(videoElement?.videoWidth || 0);
      const height = Number(videoElement?.videoHeight || 0);
      if (!width || !height) return;
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
        if (!blob) throw new Error("无法编码摄像头画面");
        if (blob.size > MAX_VIDEO_FRAME_BYTES) {
          blob = await new Promise(resolve => canvas.toBlob(resolve,"image/jpeg",0.48));
        }
        if (!blob || blob.size > MAX_VIDEO_FRAME_BYTES) throw new Error("摄像头画面超过 2 MiB 限制");
        const dataBase64 = await blobBase64(blob);
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
        let file = {};
        try { file = await upload.json(); } catch {}
        const fileId = String(file.id || "");
        if (!upload.ok || !/^file_[A-Za-z0-9]{8,64}$/.test(fileId)) {
          throw new Error(file.detail || "摄像头画面上传失败");
        }
        if (!sendJson({
          type:"input_video.frame",
          item_id:itemId,
          sequence,
          timestamp_ms:Math.max(0,Date.now() - sessionStartedAt),
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
        videoPending = false;
        pendingVideoItemId = "";
        event({type:"client.error",message:String(error?.message || error)});
        stopCamera();
      }
    }
    async function startCamera(targetVideo) {
      if (!ready || stopping) throw new Error("请先连接 GPT-Live 语音");
      if (!supportsVideoFrames) throw new Error("当前 Worker 会话不支持摄像头抽帧");
      if (cameraStream) return;
      if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function") {
        throw new Error("当前浏览器无法访问摄像头");
      }
      if (!targetVideo) throw new Error("摄像头预览元素不可用");
      state("camera_starting","正在请求摄像头权限");
      videoElement = targetVideo;
      cameraStream = await mediaDevices.getUserMedia({
        video:{facingMode:"environment",width:{ideal:1280},height:{ideal:720}},
        audio:false,
      });
      videoElement.srcObject = cameraStream;
      if (typeof videoElement.play === "function") await videoElement.play();
      cameraTimer = timers.setInterval(() => { void captureAndSendFrame(); },1000);
      void captureAndSendFrame();
      state("camera_ready","摄像头已开启（每秒最多发送一张快照）");
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
        closeTimeoutId = timers.setTimeout(resolve,5000);
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

  window.Chat2APIVoiceClientV1 = Object.freeze({create,resamplePcm16,decodePcm16});
})();