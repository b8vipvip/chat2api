(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    user: null,
    apiToken: "",
    keys: [],
    models: [],
    conversations: [],
    activeConversationId: "",
    selectedModel: "gpt-5.6-sol",
    storageKey: "",
    streaming: false,
    streamingMessageId: "",
    abortController: null,
    recoveryController: null,
    activeRequestRecord: null,
    recoveryInFlight: new Set(),
    nativeSnapshots: new Map(),
    nativeStreams: new Map(),
    pendingImages: [],
    pendingImagesConversationId: "",
    pendingFiles: [],
    pendingFilesConversationId: "",
    toastTimer: null,
    remoteSyncReady: false,
    remoteSyncTimers: new Map(),
    remoteSyncInFlight: new Map(),
    remoteSyncAgain: new Set(),
    remoteSyncFingerprints: new Map(),
    remoteSyncRetryCounts: new Map(),
    remoteSyncWarned: false,
    searchResults: null,
    searchQuery: "",
    searchController: null,
    searchTimer: null,
    searchWarned: false,
    voiceClient: null,
    voiceStarting: false,
    voiceActive: false,
    voicePanelVisible: false,
    voiceAssistantLine: null,
    voiceAssistantPersisted: false,
    voiceAuthTimer: null,
    voiceConversationId: "",
  };
  const MAX_CONVERSATIONS = 15;
  const MAX_MESSAGES = 30;
  const MAX_MESSAGE_CHARS = 8000;
  const MAX_IMAGES_PER_MESSAGE = 4;
  const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
  const MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024;
  const MAX_FILES_PER_MESSAGE = 4;
  const MAX_DOCUMENT_BYTES = 20 * 1024 * 1024;
  const ALLOWED_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
  const DOCUMENT_MIME_BY_EXTENSION = Object.freeze({
    pdf: "application/pdf", txt: "text/plain", md: "text/markdown", csv: "text/csv",
    json: "application/json", rtf: "application/rtf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  });
  const ALLOWED_DOCUMENT_MIME_TYPES = new Set(Object.values(DOCUMENT_MIME_BY_EXTENSION));
  const FILE_ID_PATTERN = /^file_[A-Za-z0-9]{8,64}$/;

  function id() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return "c2a_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 12);
  }

  function toast(message) {
    const node = $("toast");
    node.textContent = String(message || "");
    node.classList.add("visible");
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => node.classList.remove("visible"), 2800);
  }

  function setConnection(message, kind = "") {
    const node = $("connectionState");
    node.textContent = message;
    node.className = "connection-state" + (kind ? " " + kind : "");
  }

  function updateVoiceControls() {
    const start = $("voiceStartButton");
    const stop = $("voiceStopButton");
    const camera = $("voiceCameraButton");
    if (!start || !stop || !camera) return;
    start.disabled = state.streaming || state.voiceActive || !state.apiToken;
    start.classList.toggle("hidden", state.voiceActive);
    stop.classList.toggle("hidden", !state.voiceActive);
    stop.disabled = !state.voiceActive || state.voiceStarting;
    camera.classList.toggle("hidden", !state.voiceActive || state.voiceStarting);
    camera.disabled = !state.voiceActive || state.voiceStarting || !selectedKeyHasScope("files");
    if (!selectedKeyHasScope("files")) camera.title = "当前 API Key 缺少 files 权限";
    else camera.title = "在同一 Voice 会话中发送摄像头快照";
    $("apiKeySelect").disabled = state.streaming || state.voiceActive;
    $("modelSelect").disabled = state.streaming || state.voiceActive;
    $("newChatButton").disabled = state.streaming || state.voiceActive;
    $("attachButton").disabled = state.streaming || state.voiceActive || !state.apiToken || !selectedKeyHasScope("files");
    $("fileButton").disabled = state.streaming || state.voiceActive || !state.apiToken || !selectedKeyHasScope("files");
  }

  function setVoiceUi(status, detail = "") {
    state.voiceStarting = status === "requesting_microphone" || status === "camera_starting";
    state.voiceActive = ["requesting_microphone","ready","camera_starting","camera_ready","stopping"].includes(status);
    if (status !== "idle") state.voicePanelVisible = true;
    $("voicePanel").classList.toggle("hidden", !state.voicePanelVisible);
    if (detail) $("voiceStatus").textContent = detail;
    else {
      const labels = {
        requesting_microphone:"等待麦克风授权…",
        ready:"GPT-Live 已连接",
        camera_starting:"正在开启摄像头…",
        stopping:"正在结束实时语音…",
        closed:"实时语音已结束",
      };
      $("voiceStatus").textContent = labels[status] || "实时语音";
    }
    if (status === "closed") {
      const partialAssistantText = String(state.voiceAssistantLine?.textContent || "").trim();
      if (partialAssistantText && !state.voiceAssistantPersisted) {
        // The user may stop Voice mid-response or the upstream socket may
        // close before response.done. Preserve the visible partial transcript.
        state.voiceAssistantPersisted = persistVoiceTranscript("assistant",partialAssistantText,"voice_partial");
      }
      $("voiceCameraPreview").classList.add("hidden");
      $("voiceCameraButton").textContent = "开启摄像头";
      state.voiceAssistantLine = null;
      state.voiceAssistantPersisted = false;
      state.voiceClient = null;
      state.voiceConversationId = "";
      if (state.voiceAuthTimer !== null) window.clearInterval(state.voiceAuthTimer);
      state.voiceAuthTimer = null;
    }
    updateVoiceControls();
    resizeComposer();
  }

  function appendVoiceTranscript(role, value) {
    const text = String(value || "");
    if (!text && role !== "assistant") return null;
    const row = document.createElement("div");
    row.className = "voice-transcript-line " + role;
    const label = document.createElement("span");
    label.className = "voice-transcript-role";
    label.textContent = role === "assistant" ? "ChatGPT" : "你";
    const body = document.createElement("span");
    body.className = "voice-transcript-text";
    body.textContent = text;
    row.append(label,body);
    $("voiceTranscript").appendChild(row);
    while ($("voiceTranscript").childElementCount > 30) {
      $("voiceTranscript").firstElementChild.remove();
    }
    $("voiceTranscript").scrollTop = $("voiceTranscript").scrollHeight;
    return body;
  }

  function persistVoiceTranscript(role, value, recoveryStatus = "") {
    if ((role !== "user" && role !== "assistant") || !state.voiceConversationId) return false;
    const content = String(value || "").trim().slice(0,MAX_MESSAGE_CHARS);
    if (!content) return false;
    let conversation = state.conversations.find(row => row.id === state.voiceConversationId) || null;
    if (!conversation) return false;
    conversation.messages.push({
      id:id(),
      role,
      content,
      createdAt:new Date().toISOString(),
      recoveryStatus:String(recoveryStatus || "").slice(0,40),
    });
    conversation.messages = conversation.messages.slice(-MAX_MESSAGES);
    if (role === "user") autoTitle(conversation);
    conversation.updatedAt = new Date().toISOString();
    saveHistory();
    if (currentConversation()?.id === conversation.id) renderAll();
    return true;
  }

  function handleVoiceEvent(payload) {
    if (!payload || typeof payload !== "object") return;
    if (payload.type === "response.created") {
      // Start a fresh persistence boundary for each assistant turn. Keep the
      // completed turn's guard set until this event so session-close cleanup
      // cannot accidentally re-save the previous completed transcript.
      state.voiceAssistantLine = null;
      state.voiceAssistantPersisted = false;
    } else if (payload.type === "transcript.final") {
      appendVoiceTranscript("user",payload.text);
      persistVoiceTranscript("user",payload.text);
    } else if (payload.type === "response.text.delta") {
      if (!state.voiceAssistantLine) {
        state.voiceAssistantLine = appendVoiceTranscript("assistant","");
        state.voiceAssistantPersisted = false;
      }
      if (state.voiceAssistantLine) {
        state.voiceAssistantLine.textContent += String(payload.delta || "");
        $("voiceTranscript").scrollTop = $("voiceTranscript").scrollHeight;
      }
    } else if (payload.type === "response.text.replace") {
      if (!state.voiceAssistantLine) {
        state.voiceAssistantLine = appendVoiceTranscript("assistant","");
        state.voiceAssistantPersisted = false;
      }
      if (state.voiceAssistantLine) {
        state.voiceAssistantLine.textContent = String(payload.text || "");
        $("voiceTranscript").scrollTop = $("voiceTranscript").scrollHeight;
      }
    } else if (payload.type === "response.interrupted") {
      // The extension sends a synchronous final DOM snapshot with interruption
      // events because the periodic 250 ms poller may not have seen the last text.
      const partialText = String(payload.text || state.voiceAssistantLine?.textContent || "").trim();
      if (partialText && !state.voiceAssistantPersisted) {
        persistVoiceTranscript("assistant",partialText,"voice_partial");
      }
      // A barge-in interrupts only this response, not the whole Voice
      // session. Detach its partial line so the next response starts clean.
      state.voiceAssistantLine = null;
      state.voiceAssistantPersisted = false;
    } else if (payload.type === "response.done") {
      const finalText = String(payload.text || state.voiceAssistantLine?.textContent || "");
      if (!state.voiceAssistantLine && finalText) {
        state.voiceAssistantLine = appendVoiceTranscript("assistant",finalText);
      } else if (state.voiceAssistantLine && finalText) {
        // response.done is the authoritative final transcript; replace a
        // partial delta snapshot if any intermediate frame was lost.
        state.voiceAssistantLine.textContent = finalText;
      }
      if (finalText) {
        state.voiceAssistantPersisted = persistVoiceTranscript("assistant",finalText);
      }
      state.voiceAssistantLine = null;
    } else if (payload.type === "input_video.frame.sent") {
      $("voiceStatus").textContent = "摄像头画面已送达 ChatGPT Voice";
    } else if (payload.type === "error" || payload.type === "client.error") {
      $("voiceStatus").textContent = "语音提示：" + String(payload.message || payload.code || "未知错误");
      if (payload.type === "client.error" && state.voiceClient && !state.voiceClient.isCameraActive()) {
        $("voiceCameraPreview").classList.add("hidden");
        $("voiceCameraButton").textContent = "开启摄像头";
      }
    } else if (payload.type === "client.notice") {
      $("voiceStatus").textContent = String(payload.message || "摄像头抽帧已停止");
      if (state.voiceClient && !state.voiceClient.isCameraActive()) {
        $("voiceCameraPreview").classList.add("hidden");
        $("voiceCameraButton").textContent = "开启摄像头";
      }
    }
  }

  async function startVoiceCall() {
    if (state.streaming || state.voiceActive) return;
    const activeConversation = currentConversation();
    if (activeConversation?.messages.some(message => message.pending === true)) {
      toast("当前对话还有未完成的回复或恢复任务，请先处理后再开始语音。");
      return;
    }
    if (!state.apiToken) { toast("请先选择一个可用的 API Key。"); return; }
    if (!selectedKeyHasScope("audio") && !selectedKeyHasScope("chat")) {
      toast("当前 API Key 没有 audio 或 chat 权限。");
      return;
    }
    const voiceApi = window.Chat2APIVoiceClientV1;
    if (!voiceApi || typeof voiceApi.create !== "function") {
      toast("实时语音模块尚未加载，请刷新页面后重试。");
      return;
    }
    let voiceConversation = currentConversation();
    if (!voiceConversation) voiceConversation = createConversation();
    if (!voiceConversation) {
      toast("无法为语音会话创建聊天记录。");
      return;
    }
    if (voiceConversation.apiKeyId && voiceConversation.apiKeyId !== selectedKeyId()) {
      toast("当前对话绑定了另一个 API Key，请切换到该 Key 后再开始语音。");
      return;
    }
    state.voiceConversationId = voiceConversation.id;
    state.voiceAssistantLine = null;
    state.voiceAssistantPersisted = false;
    state.voicePanelVisible = true;
    $("voicePanel").classList.remove("hidden");
    $("voiceTranscript").replaceChildren();
    state.voiceAssistantLine = null;
    const client = voiceApi.create({
      apiToken:state.apiToken,
      beforeConnect:async () => {
        try {
          const session = await apiJson("/api/user/me");
          if (String(session.user?.user_id || "") === String(state.user?.user_id || "")) return;
        } catch {}
        state.apiToken = "";
        state.user = null;
        state.voicePanelVisible = false;
        $("voiceTranscript").replaceChildren();
        $("voicePanel").classList.add("hidden");
        $("composerArea").classList.add("hidden");
        showOnly("authRequired");
        setConnection("登录已失效","error");
        throw new Error("用户登录状态已失效，请重新登录后再使用实时语音");
      },
      onState:(status,detail) => setVoiceUi(status,detail),
      onEvent:handleVoiceEvent,
    });
    state.voiceClient = client;
    setVoiceUi("requesting_microphone","正在请求麦克风权限…");
    try {
      await client.start();
      state.voiceAuthTimer = window.setInterval(async () => {
        try {
          const current = await apiJson("/api/user/me");
          if (String(current.user?.user_id || "") === String(state.user?.user_id || "")) return;
        } catch {}
        state.apiToken = "";
        state.user = null;
        showOnly("authRequired");
        $("composerArea").classList.add("hidden");
        $("voiceTranscript").replaceChildren();
        state.voicePanelVisible = false;
        toast("登录已失效，实时语音已停止。请重新登录。");
        await stopVoiceCall();
      },60000);
    } catch (error) {
      await client.stop();
      if (state.voiceClient === client) state.voiceClient = null;
      setVoiceUi("closed","实时语音启动失败：" + String(error?.message || error));
      toast("无法启动实时语音：" + String(error?.message || error));
    }
  }

  async function stopVoiceCall() {
    const client = state.voiceClient;
    if (!client) { setVoiceUi("closed"); return; }
    setVoiceUi("stopping","正在结束实时语音…");
    try { await client.stop(); }
    catch (error) { toast("结束语音时发生错误：" + String(error?.message || error)); }
    if (state.voiceClient === client) state.voiceClient = null;
    setVoiceUi("closed");
  }

  async function toggleVoiceCamera() {
    const client = state.voiceClient;
    if (!client || !client.isActive()) return;
    if (client.isCameraActive()) {
      client.stopCamera();
      $("voiceCameraPreview").classList.add("hidden");
      $("voiceCameraButton").textContent = "开启摄像头";
      return;
    }
    if (!selectedKeyHasScope("files")) {
      toast("摄像头画面上传需要当前 API Key 具有 files 权限。");
      return;
    }
    try {
      $("voiceCameraPreview").classList.remove("hidden");
      await client.startCamera($("voiceCameraPreview"));
      $("voiceCameraButton").textContent = "关闭摄像头";
    } catch (error) {
      $("voiceCameraPreview").classList.add("hidden");
      toast("无法开启摄像头：" + String(error?.message || error));
    }
  }

  function showOnly(view) {
    ["welcomeScreen", "chatScreen", "authRequired", "keyRequired"].forEach((name) => {
      $(name).classList.toggle("hidden", name !== view);
    });
    $("composerArea").classList.toggle("hidden", view === "authRequired" || view === "keyRequired");
  }

  async function apiJson(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { ...(options.headers || {}) },
    });
    let payload = {};
    try { payload = await response.json(); } catch {}
    if (!response.ok) {
      throw new Error(payload.detail || payload?.error?.message || "HTTP " + response.status);
    }
    return payload;
  }

  function historyKey(userId) {
    return "chat2api.webchat.v1." + String(userId || "unknown");
  }

  function imageFileId(part) {
    const value = typeof part?.image_url === "string" ? part.image_url : part?.image_url?.url;
    return typeof value === "string" && FILE_ID_PATTERN.test(value) ? value : "";
  }

  function normalizedDocumentPart(part) {
    const fileId = typeof part?.file_id === "string" ? part.file_id :
      (typeof part?.file?.file_id === "string" ? part.file.file_id : "");
    const mimeType = String(part?.mime_type || "").toLowerCase();
    if (!FILE_ID_PATTERN.test(fileId) || !ALLOWED_DOCUMENT_MIME_TYPES.has(mimeType)) return null;
    const filename = String(part?.filename || "document").replace(/[\\/\\0]/g, "_").slice(0, 180);
    const size = Number.isFinite(Number(part?.size)) ? Math.max(0, Math.min(MAX_DOCUMENT_BYTES, Number(part.size))) : 0;
    return {type:"file",file_id:fileId,filename,mime_type:mimeType,size};
  }

  function normalizeMessageContent(content, role) {
    if (typeof content === "string") return content.slice(0, MAX_MESSAGE_CHARS);
    if (role !== "user" || !Array.isArray(content)) return "";
    const parts = [];
    let imageCount = 0;
    let fileCount = 0;
    for (const part of content) {
      if (part?.type === "text" && typeof part.text === "string") {
        parts.push({type:"text",text:part.text.slice(0,MAX_MESSAGE_CHARS)});
      } else if (part?.type === "image_url" && imageCount + fileCount < MAX_FILES_PER_MESSAGE) {
        const fileId = imageFileId(part);
        if (fileId) {
          parts.push({type:"image_url",image_url:{url:fileId}});
          imageCount += 1;
        }
      } else if (part?.type === "file" && imageCount + fileCount < MAX_FILES_PER_MESSAGE) {
        const normalized = normalizedDocumentPart(part);
        if (normalized) { parts.push(normalized); fileCount += 1; }
      }
    }
    return parts.length ? parts : "";
  }

  function messageText(message) {
    if (typeof message?.content === "string") return message.content;
    if (!Array.isArray(message?.content)) return "";
    return message.content
      .filter((part) => part?.type === "text" && typeof part.text === "string")
      .map((part) => part.text)
      .join("\n");
  }

  function messageImageIds(message) {
    if (!Array.isArray(message?.content)) return [];
    return message.content.map(imageFileId).filter(Boolean).slice(0, MAX_IMAGES_PER_MESSAGE);
  }

  function messageFileAttachments(message) {
    if (!Array.isArray(message?.content)) return [];
    return message.content.filter((part) => part?.type === "file")
      .map(normalizedDocumentPart).filter(Boolean).slice(0, MAX_FILES_PER_MESSAGE);
  }

  function messageFileIds(message) {
    return messageFileAttachments(message).map((file) => file.file_id);
  }

  function messageAttachmentIds(message) {
    return [...messageImageIds(message), ...messageFileIds(message)];
  }

  function formatFileSize(size) {
    const bytes = Math.max(0, Number(size) || 0);
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KiB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MiB";
  }

  function safeToolName(value) {
    return Array.from(String(value || "tool")).filter(character => {
      const code = character.charCodeAt(0);
      return code >= 32 && code !== 127;
    }).join("").slice(0,80) || "tool";
  }

  function cleanToolReferenceText(value, limit) {
    if (typeof value !== "string") return "";
    return value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function safeToolReference(value) {
    if (!value || typeof value !== "object" || typeof value.url !== "string") return null;
    const rawUrl = value.url;
    if (!rawUrl || rawUrl.length > 2048 || rawUrl !== rawUrl.trim()
        || /[\u0000-\u001f\u007f]/.test(rawUrl)) return null;
    try {
      const url = new URL(rawUrl);
      const host = url.hostname.toLowerCase().replace(/\.$/, "");
      if (url.protocol !== "https:" || url.username || url.password
          || (url.port && url.port !== "443") || !host || host.includes(":")
          || /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)
          || ["chatgpt.com","chat.openai.com","localhost"].includes(host)
          || /(?:^|\.)(?:chatgpt\.com|localhost|local|internal|test|invalid|example|onion)$/.test(host)
          || !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host)) return null;
      if (typeof window !== "undefined" && window.location
          && url.origin === window.location.origin) return null;
      const rawId = typeof value.id === "string" ? value.id.trim().slice(0,128) : "";
      const referenceId = /^[A-Za-z0-9_.:-]{1,128}$/.test(rawId) ? rawId : "";
      return {
        ...(referenceId ? {id:referenceId} : {}),
        url: url.href,
        title: cleanToolReferenceText(value.title, 180) || host,
        snippet: cleanToolReferenceText(value.snippet, 420),
      };
    } catch (_) {
      return null;
    }
  }

  function normalizeToolReferences(value) {
    if (!Array.isArray(value)) return [];
    const positions = new Map(), rows = [];
    value.slice(0,32).forEach(item => {
      const reference = safeToolReference(item);
      if (!reference) return;
      const identity = reference.id ? "id:" + reference.id : "url:" + reference.url;
      const existingIndex = positions.get(identity);
      if (existingIndex !== undefined) {
        const existing = rows[existingIndex];
        rows[existingIndex] = {
          ...existing,
          title: reference.title || existing.title,
          snippet: reference.snippet || existing.snippet,
          start_index: existing.start_index ?? reference.start_index,
          end_index: existing.end_index ?? reference.end_index,
        };
        return;
      }
      positions.set(identity, rows.length);
      rows.push(reference);
    });
    return rows.slice(0,8);
  }

  function toolReferencesEqual(left, right) {
    const a = Array.isArray(left) ? left : [];
    const b = Array.isArray(right) ? right : [];
    if (a.length !== b.length) return false;
    return a.every((item,index) => {
      const other = b[index] || {};
      return ["id","url","title","snippet","start_index","end_index"]
        .every(key => item?.[key] === other[key]);
    });
  }

  function referencesForMessage(message) {
    const events = Array.isArray(message?.toolEvents) ? message.toolEvents : [];
    return normalizeToolReferences([
      ...(Array.isArray(message?.references) ? message.references : []),
      ...events.flatMap(item => Array.isArray(item?.references) ? item.references : []),
    ]);
  }

  function citationTextForMessage(message) {
    const text = messageText(message);
    const references = referencesForMessage(message);
    const numberById = new Map();
    references.forEach((item,index) => {
      if (item.id && !numberById.has(item.id)) numberById.set(item.id,index + 1);
    });
    return text.replace(
      /\uE200cite\uE202([A-Za-z0-9_.:-]{1,128}(?:\uE202[A-Za-z0-9_.:-]{1,128})*)\uE201/g,
      (_marker, encodedIds) => {
        const numbers = encodedIds.split("\uE202")
          .map(referenceId => numberById.get(referenceId))
          .filter(Number.isInteger);
        return numbers.length ? " [" + numbers.join(", ") + "]" : "";
      },
    );
  }

  function normalizeToolEvents(value, role) {
    if (role !== "assistant" || !Array.isArray(value)) return [];
    return value.slice(-6).map(item => {
      if (!item || typeof item !== "object") return null;
      const toolName = safeToolName(item.toolName);
      const callIdRaw = String(item.callId || "").slice(0,128);
      const resultIdRaw = String(item.resultId || "").slice(0,128);
      const validId = value => /^[A-Za-z0-9_.:-]{1,128}$/.test(value);
      const callId = validId(callIdRaw) ? callIdRaw : "";
      const resultId = validId(resultIdRaw) ? resultIdRaw : "";
      if (!callId && !resultId) return null;
      const status = normalizeToolStatus(item.status);
      const normalized = {
        toolName, callId, resultId, status,
        arguments: typeof item.arguments === "string" ? item.arguments.slice(0,1200) : "",
        output: typeof item.output === "string" ? item.output.slice(0,1200) : "",
      };
      const references = normalizeToolReferences(item.references);
      if (references.length) normalized.references = references;
      return normalized;
    }).filter(Boolean);
  }

  function normalizeConversationRow(row) {
    if (!row || typeof row !== "object" || typeof row.id !== "string") return null;
    const messages = Array.isArray(row.messages) ? row.messages.slice(-MAX_MESSAGES).map((message) => {
      if (!message || (message.role !== "user" && message.role !== "assistant")) return null;
      const content = normalizeMessageContent(message.content, message.role);
      const pending = message.pending === true;
      const toolEvents = normalizeToolEvents(message.toolEvents,message.role);
      const references = message.role === "assistant" ? normalizeToolReferences([
        ...(Array.isArray(message.references) ? message.references : []),
        ...toolEvents.flatMap(item => Array.isArray(item.references) ? item.references : []),
      ]) : [];
      const emptyPendingAssistant = message.role === "assistant" && pending && content === "";
      if ((!content && !emptyPendingAssistant && !toolEvents.length)
          || (message.role === "assistant" && !String(content).trim() && !pending && !toolEvents.length)) return null;
      const normalized = {
        id: String(message.id || id()),
        role: message.role,
        content,
        createdAt: String(message.createdAt || ""),
        requestId: typeof message.requestId === "string" ? message.requestId.slice(0, 140) : "",
        pending,
        recoveryStatus: typeof message.recoveryStatus === "string" ? message.recoveryStatus.slice(0, 40) : "",
        lastEventId: Number.isSafeInteger(message.lastEventId) && message.lastEventId >= 0
          ? Math.min(message.lastEventId, Number.MAX_SAFE_INTEGER) : 0,
      };
      if (toolEvents.length) normalized.toolEvents = toolEvents;
      if (references.length) normalized.references = references;
      return normalized;
    }).filter(Boolean) : [];
    const hasSavedAttachments = messages.some((message) => messageAttachmentIds(message).length);
    return {
      id: row.id.slice(0, 128),
      title: String(row.title || "新对话").slice(0, 80),
      model: String(row.model || state.selectedModel).slice(0, 120),
      apiKeyId: hasSavedAttachments && typeof row.apiKeyId === "string" ? row.apiKeyId.slice(0, 128) : "",
      createdAt: String(row.createdAt || new Date().toISOString()),
      updatedAt: String(row.updatedAt || row.createdAt || new Date().toISOString()),
      messages,
      serverRevision: Number.isInteger(row.revision) ? row.revision :
        (Number.isInteger(row.serverRevision) ? row.serverRevision : 0),
    };
  }

  function loadHistory() {
    try {
      const raw = localStorage.getItem(state.storageKey);
      const rows = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(rows)) return [];
      return rows.filter((row) => row && typeof row === "object" && typeof row.id === "string")
        .slice(0, MAX_CONVERSATIONS).map(normalizeConversationRow).filter(Boolean);
    } catch {
      toast("本地聊天历史无法读取，已使用空白历史。");
      return [];
    }
  }

  function persistHistorySnapshot() {
    if (!state.storageKey) return;
    const payload = state.conversations.map((conversation) => ({
      ...conversation,
      messages: (conversation.messages || []).slice(-MAX_MESSAGES).map((message) => ({
        ...message,
        content: normalizeMessageContent(message.content, message.role),
      })),
    }));
    try {
      localStorage.setItem(state.storageKey, JSON.stringify(payload));
    } catch {
      toast("本地缓存空间不足；服务端会话仍会继续同步。");
    }
  }

  function serverConversationPayload(conversation) {
    return {
      id: conversation.id,
      title: String(conversation.title || "新对话").slice(0, 80),
      model: String(conversation.model || state.selectedModel).slice(0, 120),
      apiKeyId: typeof conversation.apiKeyId === "string" ? conversation.apiKeyId : "",
      createdAt: String(conversation.createdAt || new Date().toISOString()),
      updatedAt: String(conversation.updatedAt || new Date().toISOString()),
      messages: (conversation.messages || []).slice(-MAX_MESSAGES).map((message) => {
        const toolEvents = normalizeToolEvents(message.toolEvents,message.role);
        const references = message.role === "assistant"
          ? normalizeToolReferences(message.references) : [];
        return {
          id: String(message.id || id()),
          role: message.role,
          content: normalizeMessageContent(message.content, message.role),
          createdAt: String(message.createdAt || ""),
          requestId: typeof message.requestId === "string" ? message.requestId : "",
          pending: message.pending === true,
          recoveryStatus: typeof message.recoveryStatus === "string" ? message.recoveryStatus : "",
          lastEventId: Number.isSafeInteger(message.lastEventId) && message.lastEventId >= 0
            ? Math.min(message.lastEventId, Number.MAX_SAFE_INTEGER) : 0,
          ...(toolEvents.length ? {toolEvents} : {}),
          ...(references.length ? {references} : {}),
        };
      }),
    };
  }

  function messageContentWeight(message) {
    let contentWeight = 0;
    if (typeof message?.content === "string") contentWeight = message.content.length;
    else {
      try { contentWeight = JSON.stringify(message?.content || "").length; } catch {}
    }
    try { return contentWeight + JSON.stringify(message?.toolEvents || []).length; }
    catch { return contentWeight; }
  }

  function messageBodyWeight(message) {
    if (typeof message?.content === "string") return message.content.length;
    try { return JSON.stringify(message?.content || "").length; }
    catch { return 0; }
  }

  function toolActivityWeight(events) {
    if (!Array.isArray(events)) return 0;
    return events.reduce((weight,item) => weight
      + (item?.status === "running" ? 1 : 10)
      + String(item?.arguments || "").length
      + String(item?.output || "").length,0);
  }

  function mergeConversation(serverRow, localRow) {
    if (!localRow) return {...serverRow};
    if (!serverRow) return {...localRow};
    const messages = new Map();
    (serverRow.messages || []).forEach((message) => messages.set(message.id, message));
    (localRow.messages || []).forEach((message) => {
      const existing = messages.get(message.id);
      if (!existing) {
        messages.set(message.id, message);
        return;
      }
      const localPending = message.pending === true;
      const serverPending = existing.pending === true;
      const localCompleted = !localPending && message.recoveryStatus === "completed";
      const serverCompleted = !serverPending && existing.recoveryStatus === "completed";
      const localToolWeight = toolActivityWeight(message.toolEvents);
      const serverToolWeight = toolActivityWeight(existing.toolEvents);
      const mergedReferences = normalizeToolReferences([
        ...(Array.isArray(existing.references) ? existing.references : []),
        ...(Array.isArray(message.references) ? message.references : []),
      ]);
      const withMergedReferences = row => mergedReferences.length
        ? {...row,references:mergedReferences} : row;

      // A terminal snapshot must never be resurrected by a stale pending copy
      // from another device. Keep the longer text/tool evidence, but preserve
      // the terminal side's pending/recoveryStatus state.
      if (serverCompleted || (!serverPending && localPending)) {
        const merged = {...existing};
        if (!serverCompleted && messageBodyWeight(message) > messageBodyWeight(existing)) {
          merged.content = message.content;
        }
        if (localToolWeight > serverToolWeight) merged.toolEvents = message.toolEvents;
        messages.set(message.id, withMergedReferences(merged));
        return;
      }
      if (localCompleted || (!localPending && serverPending)) {
        const merged = {...message};
        if (!localCompleted && messageBodyWeight(existing) > messageBodyWeight(message)) {
          merged.content = existing.content;
        }
        if (serverToolWeight > localToolWeight) merged.toolEvents = existing.toolEvents;
        messages.set(message.id, withMergedReferences(merged));
        return;
      }
      if (messageContentWeight(message) > messageContentWeight(existing)) {
        messages.set(message.id, withMergedReferences(message));
      } else if (localToolWeight > serverToolWeight) {
        // Preserve newer call/result state without replacing an authoritative
        // completed answer with a stale local text snapshot.
        messages.set(message.id, withMergedReferences({...existing,toolEvents:message.toolEvents}));
      } else if (mergedReferences.length) {
        // A newer text snapshot must not erase server-side citations.
        messages.set(message.id, withMergedReferences(existing));
      }
    });
    const localNewer = String(localRow.updatedAt || "") > String(serverRow.updatedAt || "");
    const base = localNewer ? localRow : serverRow;
    return {
      ...serverRow,
      ...base,
      messages: [...messages.values()].sort((a, b) =>
        String(a.createdAt || "").localeCompare(String(b.createdAt || ""))).slice(-MAX_MESSAGES),
      serverRevision: Number(serverRow.serverRevision || 0),
    };
  }

  async function syncServerConversations() {
    const response = await fetch("/api/user/chat/conversations", {
      credentials: "same-origin",
      headers: {"Accept":"application/json"},
    });
    let payload = {};
    try { payload = await response.json(); } catch {}
    if (!response.ok) throw new Error(payload.detail || "HTTP " + response.status);
    const serverRows = Array.isArray(payload.data) ? payload.data : [];
    const serverActive = new Map();
    const deletedIds = new Set();
    state.remoteSyncFingerprints.clear();
    serverRows.forEach((row) => {
      if (!row || typeof row.id !== "string") return;
      if (row.deleted === true) {
        deletedIds.add(row.id);
        return;
      }
      const normalized = normalizeConversationRow(row);
      if (!normalized) return;
      normalized.serverRevision = Number.isInteger(row.revision) ? row.revision : 0;
      serverActive.set(normalized.id, normalized);
      state.remoteSyncFingerprints.set(normalized.id, JSON.stringify(serverConversationPayload(normalized)));
    });
    const localById = new Map(state.conversations.map((row) => [row.id, row]));
    const merged = [];
    serverActive.forEach((serverRow, conversationId) => {
      merged.push(mergeConversation(serverRow, localById.get(conversationId)));
    });
    localById.forEach((localRow, conversationId) => {
      if (!serverActive.has(conversationId) && !deletedIds.has(conversationId)) {
        merged.push({...localRow, serverRevision: 0});
      }
    });
    state.conversations = merged.sort((a, b) =>
      String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""))).slice(0, MAX_CONVERSATIONS);
    if (!state.conversations.some((row) => row.id === state.activeConversationId)) {
      state.activeConversationId = state.conversations[0]?.id || "";
    }
    state.remoteSyncReady = true;
    persistHistorySnapshot();
    state.conversations.forEach((conversation) => scheduleConversationSync(conversation.id, 250));
  }

  function scheduleConversationSync(conversationId, delay = 450) {
    if (!state.remoteSyncReady || !conversationId) return;
    if (!state.conversations.some((row) => row.id === conversationId)) return;
    // A fresh user edit starts a new retry budget; automatic backoff calls use
    // a distinct delay and preserve the accumulated retry count.
    if (delay === 450) state.remoteSyncRetryCounts.delete(conversationId);
    const previous = state.remoteSyncTimers.get(conversationId);
    if (previous) window.clearTimeout(previous);
    const timer = window.setTimeout(() => {
      state.remoteSyncTimers.delete(conversationId);
      void syncConversationToServer(conversationId);
    }, delay);
    state.remoteSyncTimers.set(conversationId, timer);
  }

  async function syncConversationToServer(conversationId) {
    if (!state.remoteSyncReady) return;
    if (state.remoteSyncInFlight.has(conversationId)) {
      state.remoteSyncAgain.add(conversationId);
      return;
    }
    const task = (async () => {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        const conversation = state.conversations.find((row) => row.id === conversationId);
        if (!conversation) return;
        const body = serverConversationPayload(conversation);
        const fingerprint = JSON.stringify(body);
        if (state.remoteSyncFingerprints.get(conversationId) === fingerprint) return;
        const response = await fetch("/api/user/chat/conversations/" + encodeURIComponent(conversationId), {
          method: "PUT",
          credentials: "same-origin",
          headers: {"Content-Type":"application/json", "Accept":"application/json"},
          body: JSON.stringify({
            expected_revision: Number(conversation.serverRevision || 0),
            conversation: body,
          }),
        });
        let result = {};
        try { result = await response.json(); } catch {}
        if (response.status === 409 && result.conversation) {
          if (result.conversation.deleted === true) {
            state.conversations = state.conversations.filter((row) => row.id !== conversationId);
            state.remoteSyncFingerprints.delete(conversationId);
            state.remoteSyncRetryCounts.delete(conversationId);
            if (state.activeConversationId === conversationId) {
              state.activeConversationId = state.conversations[0]?.id || "";
            }
            persistHistorySnapshot();
            renderAll();
            return;
          }
          const latest = normalizeConversationRow(result.conversation);
          if (!latest) throw new Error("服务端返回了无效的会话冲突记录");
          Object.assign(conversation, mergeConversation(latest, conversation));
          conversation.serverRevision = latest.serverRevision;
          persistHistorySnapshot();
          continue;
        }
        if (!response.ok || !result.conversation) {
          const error = new Error(result.detail || "HTTP " + response.status);
          error.status = response.ok ? 0 : response.status;
          throw error;
        }
        const currentFingerprint = JSON.stringify(serverConversationPayload(conversation));
        const serverUpdatedAt = String(result.conversation.updatedAt || conversation.updatedAt);
        conversation.serverRevision = Number(result.conversation.revision || 0);
        if (currentFingerprint === fingerprint) conversation.updatedAt = serverUpdatedAt;
        const savedFingerprint = JSON.stringify({...body, updatedAt:serverUpdatedAt});
        state.remoteSyncFingerprints.set(conversationId, savedFingerprint);
        state.remoteSyncRetryCounts.delete(conversationId);
        persistHistorySnapshot();
        if (JSON.stringify(serverConversationPayload(conversation)) !== savedFingerprint) {
          state.remoteSyncAgain.add(conversationId);
        }
        return;
      }
      throw new Error("会话在其他设备持续更新，请稍后重试同步");
    })();
    state.remoteSyncInFlight.set(conversationId, task);
    try {
      await task;
    } catch (error) {
      if (!state.remoteSyncWarned) {
        state.remoteSyncWarned = true;
        toast("服务端会话同步暂时失败，已保留本地记录：" + error.message);
      }
      const status = Number(error && error.status || 0);
      const retryable = !status || status === 408 || status === 425 || status === 429 || status >= 500;
      const stillPresent = state.conversations.some((row) => row.id === conversationId);
      const retries = Number(state.remoteSyncRetryCounts.get(conversationId) || 0);
      if (retryable && stillPresent && retries < 6) {
        const delay = Math.min(30000, 1000 * (2 ** retries));
        state.remoteSyncRetryCounts.set(conversationId, retries + 1);
        scheduleConversationSync(conversationId, delay);
      }
    } finally {
      state.remoteSyncInFlight.delete(conversationId);
      if (state.remoteSyncAgain.delete(conversationId)) scheduleConversationSync(conversationId, 150);
    }
  }

  async function deleteConversationFromServer(conversation) {
    const conversationId = conversation.id;
    const timer = state.remoteSyncTimers.get(conversationId);
    if (timer) window.clearTimeout(timer);
    state.remoteSyncTimers.delete(conversationId);
    state.remoteSyncRetryCounts.delete(conversationId);
    const inFlight = state.remoteSyncInFlight.get(conversationId);
    if (inFlight) {
      try { await inFlight; } catch {}
    }
    const expectedRevision = Number(conversation.serverRevision || 0);
    try {
      const response = await fetch("/api/user/chat/conversations/" + encodeURIComponent(conversationId) +
        "?expected_revision=" + encodeURIComponent(String(expectedRevision)), {
        method: "DELETE",
        credentials: "same-origin",
        headers: {"Accept":"application/json"},
      });
      let result = {};
      try { result = await response.json(); } catch {}
      if (response.status === 409 && result.conversation && result.conversation.deleted !== true) {
        const latest = normalizeConversationRow(result.conversation);
        if (latest) {
          const restored = mergeConversation(latest, conversation);
          if (!state.conversations.some((row) => row.id === conversationId)) {
            state.conversations.unshift(restored);
            state.conversations.sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
            state.conversations = state.conversations.slice(0, MAX_CONVERSATIONS);
          }
          saveHistory();
          renderAll();
          toast("该对话已在其他设备更新，已恢复最新版本；请再次确认删除。");
        }
        return;
      }
      if (!response.ok) throw new Error(result.detail || "HTTP " + response.status);
      state.remoteSyncFingerprints.delete(conversationId);
    } catch (error) {
      toast("对话已从当前设备移除，但服务器删除同步失败：" + error.message);
    }
  }

  function saveHistory() {
    if (!state.storageKey) return;
    state.conversations.sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
    state.conversations = state.conversations.slice(0, MAX_CONVERSATIONS);
    persistHistorySnapshot();
    if (state.remoteSyncReady) {
      state.conversations.forEach((conversation) => scheduleConversationSync(conversation.id));
    }
  }

  function currentConversation() {
    return state.conversations.find((row) => row.id === state.activeConversationId) || null;
  }

  function createConversation() {
    if (state.streaming) {
      toast("请先停止当前生成，再新建对话。");
      return currentConversation();
    }
    const now = new Date().toISOString();
    if (state.pendingImages.length || state.pendingFiles.length) {
      toast("请先发送或移除待发送附件，再新建对话。");
      return currentConversation();
    }
    const conversation = {
      id: id(),
      title: "新对话",
      model: state.selectedModel,
      apiKeyId: "",
      createdAt: now,
      updatedAt: now,
      messages: [],
    };
    state.conversations.unshift(conversation);
    state.activeConversationId = conversation.id;
    saveHistory();
    renderAll();
    $("messageInput").focus();
    document.body.classList.remove("sidebar-open");
    return conversation;
  }

  function setActiveConversation(conversationId) {
    if (state.voiceActive) {
      toast("请先结束实时语音，再切换对话。");
      return;
    }
    if (state.streaming) {
      toast("当前回复仍在生成；请先停止生成再切换对话。");
      return;
    }
    if ((state.pendingImages.length && state.pendingImagesConversationId !== conversationId) ||
        (state.pendingFiles.length && state.pendingFilesConversationId !== conversationId)) {
      toast("请先发送或移除待发送附件，再切换对话。");
      return;
    }
    state.activeConversationId = conversationId;
    const conversation = currentConversation();
    if (conversation) state.selectedModel = conversation.model || state.selectedModel;
    $("modelSelect").value = state.selectedModel;
    renderAll();
    document.body.classList.remove("sidebar-open");
  }

  function deleteConversation(conversationId) {
    if (state.voiceActive && conversationId === state.voiceConversationId) {
      toast("请先结束实时语音，再删除当前语音会话。");
      return;
    }
    const conversation = state.conversations.find((row) => row.id === conversationId);
    if (!conversation) return;
    if (state.streaming && conversationId === state.activeConversationId) {
      toast("当前对话正在生成；请先停止生成再删除。");
      return;
    }
    if ((state.pendingImages.length && state.pendingImagesConversationId === conversationId) ||
        (state.pendingFiles.length && state.pendingFilesConversationId === conversationId)) {
      toast("请先移除待发送附件，再删除对话。");
      return;
    }
    if (!window.confirm("删除这段对话？删除会同步到你的其他设备。附件文件暂时保留，避免误删其他对话仍在使用的文件。")) return;
    state.conversations = state.conversations.filter((row) => row.id !== conversationId);
    if (Array.isArray(state.searchResults)) {
      state.searchResults = state.searchResults.filter((row) => row.id !== conversationId);
    }
    if (state.activeConversationId === conversationId) {
      state.activeConversationId = state.conversations[0]?.id || "";
    }
    saveHistory();
    void deleteConversationFromServer(conversation);
    renderAll();
  }

  function renameConversation(conversationId) {
    const conversation = state.conversations.find((row) => row.id === conversationId);
    if (!conversation) return;
    const title = window.prompt("重命名对话", conversation.title);
    if (title === null) return;
    const clean = title.trim().slice(0, 80);
    if (!clean) return;
    conversation.title = clean;
    conversation.updatedAt = new Date().toISOString();
    saveHistory();
    renderSidebar();
  }

  function hydrateSearchConversation(row) {
    const existing = state.conversations.find((item) => item.id === row.id);
    if (existing) {
      Object.assign(existing, mergeConversation(row, existing));
      existing.serverRevision = row.serverRevision;
      if (Array.isArray(state.searchResults)) {
        state.searchResults = state.searchResults.map((item) => item.id === row.id ? existing : item);
      }
      return existing;
    }
    // Opening an older server-side search result promotes it into the local
    // recent window without mutating the server revision. A read must not race
    // a subsequent delete/rename with an unnecessary background PUT.
    const opened = {...row};
    state.conversations.unshift(opened);
    state.conversations = state.conversations.slice(0, MAX_CONVERSATIONS);
    if (Array.isArray(state.searchResults)) {
      state.searchResults = state.searchResults.map((item) => item.id === row.id ? opened : item);
    }
    persistHistorySnapshot();
    return opened;
  }

  async function searchConversationsOnServer(rawQuery) {
    const query = String(rawQuery || "").trim();
    const normalized = query.toLocaleLowerCase();
    if (!query) return;
    const controller = new AbortController();
    state.searchController = controller;
    try {
      const response = await fetch(
        "/api/user/chat/conversations/search?q=" + encodeURIComponent(query),
        {credentials:"same-origin", headers:{"Accept":"application/json"}, signal:controller.signal},
      );
      let payload = {};
      try { payload = await response.json(); } catch {}
      if (!response.ok) throw new Error(payload.detail || "HTTP " + response.status);
      if ($("conversationSearch").value.trim().toLocaleLowerCase() !== normalized) return;
      const rows = Array.isArray(payload.data) ? payload.data : [];
      state.searchResults = rows.map((row) => normalizeConversationRow(row)).filter(Boolean);
      state.searchQuery = normalized;
      state.searchResults.forEach((row) => {
        row.serverRevision = Number.isInteger(row.serverRevision) ? row.serverRevision : 0;
      });
      renderSidebar();
    } catch (error) {
      if (error.name === "AbortError") return;
      if ($("conversationSearch").value.trim().toLocaleLowerCase() !== normalized) return;
      state.searchResults = null;
      state.searchQuery = normalized;
      if (!state.searchWarned) {
        state.searchWarned = true;
        toast("服务端搜索暂时不可用，当前显示本地最近记录。");
      }
      renderSidebar();
    } finally {
      if (state.searchController === controller) state.searchController = null;
    }
  }

  function onConversationSearchInput() {
    if (state.searchTimer) window.clearTimeout(state.searchTimer);
    state.searchTimer = null;
    if (state.searchController) state.searchController.abort();
    const query = $("conversationSearch").value.trim();
    if (!query) {
      state.searchResults = null;
      state.searchQuery = "";
      renderSidebar();
      return;
    }
    state.searchResults = null;
    state.searchQuery = "";
    renderSidebar();
    state.searchTimer = window.setTimeout(() => {
      state.searchTimer = null;
      void searchConversationsOnServer(query);
    }, 250);
  }

  function renderSidebar() {
    const root = $("conversationList");
    root.replaceChildren();
    const query = $("conversationSearch").value.trim().toLocaleLowerCase();
    const localRows = state.conversations.filter((conversation) => {
      if (!query) return true;
      return conversation.title.toLocaleLowerCase().includes(query) ||
        conversation.messages.some((message) => messageText(message).toLocaleLowerCase().includes(query));
    });
    const serverIds = new Set((state.searchResults || []).map((row) => row.id));
    const serverRows = query && state.searchQuery === query && Array.isArray(state.searchResults)
      ? state.searchResults.map((row) => {
          const local = state.conversations.find((item) => item.id === row.id);
          return local ? mergeConversation(row, local) : row;
        }).concat(localRows.filter((row) => !serverIds.has(row.id)))
      : localRows;
    const rows = serverRows;
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "muted-empty";
      empty.textContent = query ? "没有匹配的对话" : "还没有对话";
      root.appendChild(empty);
      return;
    }
    rows.forEach((conversation) => {
      const item = document.createElement("div");
      item.className = "conversation-item" + (conversation.id === state.activeConversationId ? " active" : "");
      const open = document.createElement("button");
      open.type = "button";
      open.className = "conversation-title";
      open.textContent = conversation.title || "新对话";
      open.title = "打开对话；双击重命名";
      open.addEventListener("click", () => {
        const selected = query ? hydrateSearchConversation(conversation) : conversation;
        setActiveConversation(selected.id);
      });
      open.addEventListener("dblclick", () => {
        const selected = query ? hydrateSearchConversation(conversation) : conversation;
        renameConversation(selected.id);
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "conversation-delete";
      remove.textContent = "×";
      remove.title = "删除对话";
      remove.setAttribute("aria-label", "删除对话");
      remove.addEventListener("click", () => {
        const selected = query ? hydrateSearchConversation(conversation) : conversation;
        deleteConversation(selected.id);
      });
      item.append(open, remove);
      root.appendChild(item);
    });
  }

  function addInline(parent, text) {
    const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|https?:\/\/[^\s<]+)/g;
    let last = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > last) parent.appendChild(document.createTextNode(text.slice(last, match.index)));
      const token = match[0];
      if (token.startsWith("`")) {
        const code = document.createElement("code");
        code.textContent = token.slice(1, -1);
        parent.appendChild(code);
      } else if (token.startsWith("**")) {
        const strong = document.createElement("strong");
        strong.textContent = token.slice(2, -2);
        parent.appendChild(strong);
      } else if (token.startsWith("*")) {
        const emphasis = document.createElement("em");
        emphasis.textContent = token.slice(1, -1);
        parent.appendChild(emphasis);
      } else {
        let safe = false;
        try { safe = ["http:", "https:"].includes(new URL(token).protocol); } catch {}
        if (safe) {
          const link = document.createElement("a");
          link.href = token;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = token;
          parent.appendChild(link);
        } else {
          parent.appendChild(document.createTextNode(token));
        }
      }
      last = pattern.lastIndex;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function addParagraph(parent, lines) {
    const paragraph = document.createElement("p");
    lines.forEach((line, index) => {
      if (index) paragraph.appendChild(document.createElement("br"));
      addInline(paragraph, line);
    });
    parent.appendChild(paragraph);
  }

  function tableCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  }

  function renderMarkdown(container, source) {
    const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i += 1; continue; }

      if (line.trim().startsWith("```")) {
        const language = line.trim().slice(3).trim() || "code";
        const codeLines = [];
        i += 1;
        while (i < lines.length && !lines[i].trim().startsWith("```")) {
          codeLines.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) i += 1;
        const wrapper = document.createElement("div");
        wrapper.className = "code-block";
        const toolbar = document.createElement("div");
        toolbar.className = "code-toolbar";
        const label = document.createElement("span");
        label.textContent = language;
        const copy = document.createElement("button");
        copy.type = "button";
        copy.textContent = "复制代码";
        copy.addEventListener("click", async () => {
          try { await navigator.clipboard.writeText(codeLines.join("\n")); toast("代码已复制"); }
          catch { toast("无法访问剪贴板"); }
        });
        toolbar.append(label, copy);
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = codeLines.join("\n");
        pre.appendChild(code);
        wrapper.append(toolbar, pre);
        container.appendChild(wrapper);
        continue;
      }

      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        const node = document.createElement("h" + heading[1].length);
        addInline(node, heading[2]);
        container.appendChild(node);
        i += 1;
        continue;
      }

      if (/^\s*([-*_])\1\1+\s*$/.test(line)) {
        container.appendChild(document.createElement("hr"));
        i += 1;
        continue;
      }

      if (/^>\s?/.test(line)) {
        const quoteLines = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          quoteLines.push(lines[i].replace(/^>\s?/, ""));
          i += 1;
        }
        const quote = document.createElement("blockquote");
        addParagraph(quote, quoteLines);
        container.appendChild(quote);
        continue;
      }

      const listMatch = line.match(/^\s*([-*+] |\d+\. )/);
      if (listMatch) {
        const ordered = /^\s*\d+\. /.test(line);
        const list = document.createElement(ordered ? "ol" : "ul");
        while (i < lines.length) {
          const match = lines[i].match(/^\s*(?:[-*+] |\d+\. )(.*)$/);
          if (!match) break;
          const item = document.createElement("li");
          addInline(item, match[1]);
          list.appendChild(item);
          i += 1;
        }
        container.appendChild(list);
        continue;
      }

      if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
        const headers = tableCells(line);
        i += 2;
        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const headRow = document.createElement("tr");
        headers.forEach((cell) => {
          const th = document.createElement("th");
          addInline(th, cell);
          headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);
        const tbody = document.createElement("tbody");
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          const cells = tableCells(lines[i]);
          const row = document.createElement("tr");
          headers.forEach((_, index) => {
            const td = document.createElement("td");
            addInline(td, cells[index] || "");
            row.appendChild(td);
          });
          tbody.appendChild(row);
          i += 1;
        }
        table.appendChild(tbody);
        container.appendChild(table);
        continue;
      }

      const paragraphLines = [];
      while (i < lines.length && lines[i].trim()) {
        if (paragraphLines.length && (
          lines[i].trim().startsWith("```") ||
          /^#{1,4}\s+/.test(lines[i]) ||
          /^>\s?/.test(lines[i]) ||
          /^\s*([-*+] |\d+\. )/.test(lines[i]) ||
          /^\s*([-*_])\1\1+\s*$/.test(lines[i])
        )) break;
        paragraphLines.push(lines[i]);
        i += 1;
      }
      if (paragraphLines.length) addParagraph(container, paragraphLines);
      else i += 1;
    }
  }

  function renderMessages() {
    const root = $("messages");
    root.replaceChildren();
    const conversation = currentConversation();
    if (!conversation || !conversation.messages.length) {
      showOnly("welcomeScreen");
      return;
    }
    showOnly("chatScreen");
    conversation.messages.forEach((message) => {
      const article = document.createElement("article");
      article.className = "message " + message.role;
      const avatar = document.createElement("div");
      avatar.className = "message-avatar";
      avatar.textContent = message.role === "user" ? "你" : "c";
      const content = document.createElement("div");
      content.className = "message-content";
      const header = document.createElement("div");
      header.className = "message-header";
      const name = document.createElement("span");
      name.textContent = message.role === "user" ? "你" : "Chat2API";
      const time = document.createElement("span");
      time.className = "message-time";
      time.textContent = message.createdAt ? new Date(message.createdAt).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}) : "";
      header.append(name, time);
      if (message.role === "assistant" && message.recoveryStatus &&
          (message.pending || message.recoveryStatus !== "completed")) {
        const status = document.createElement("span");
        status.className = "recovery-status";
        status.textContent = recoveryStatusLabel(message.recoveryStatus);
        header.appendChild(status);
      }
      const body = document.createElement("div");
      body.className = "message-body";
      let nativeRendered = false;
      if (message.role === "user") {
        const text = messageText(message);
        if (text) {
          const textNode = document.createElement("div");
          textNode.className = "message-user-text";
          textNode.textContent = text;
          body.appendChild(textNode);
        }
        const imageIds = messageImageIds(message);
        const fileAttachments = messageFileAttachments(message);
        if (fileAttachments.length) {
          const grid = document.createElement("div");
          grid.className = "message-file-grid";
          fileAttachments.forEach((file) => {
            const link = document.createElement("a");
            link.className = "message-file-card";
            link.href = "/api/user/chat/files/" + encodeURIComponent(file.file_id) + "/download";
            link.rel = "noopener noreferrer";
            const icon = document.createElement("span");
            icon.className = "message-file-icon";
            icon.textContent = "文件";
            const details = document.createElement("span");
            details.className = "message-file-details";
            const filename = document.createElement("span");
            filename.className = "message-file-name";
            filename.textContent = file.filename;
            const meta = document.createElement("span");
            meta.className = "message-file-meta";
            meta.textContent = formatFileSize(file.size) + " · " + file.mime_type;
            details.append(filename, meta);
            link.append(icon, details);
            grid.appendChild(link);
          });
          body.appendChild(grid);
        }
        if (imageIds.length) {
          const grid = document.createElement("div");
          grid.className = "message-image-grid";
          imageIds.forEach((fileId) => {
            const path = "/api/user/files/" + encodeURIComponent(fileId) + "/content";
            const link = document.createElement("a");
            link.className = "message-image-link";
            link.href = path;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            const image = document.createElement("img");
            image.className = "message-image";
            image.src = path;
            image.alt = "用户上传的图片";
            image.loading = "lazy";
            image.addEventListener("error", () => {
              const fallback = document.createElement("span");
              fallback.className = "message-image-missing";
              fallback.textContent = "图片不可用或当前账户无权访问";
              link.replaceWith(fallback);
            }, {once:true});
            link.appendChild(image);
            grid.appendChild(link);
          });
          body.appendChild(grid);
        }
      } else {
        const nativeRenderer = window.Chat2APINativeRendererV1;
        const nativeSnapshot = message.requestId ? state.nativeSnapshots.get(message.requestId) : null;
        const messageReferences = referencesForMessage(message);
        nativeRendered = Boolean(nativeRenderer && typeof nativeRenderer.renderNativeSnapshot === "function" &&
          nativeSnapshot && nativeRenderer.renderNativeSnapshot(body,nativeSnapshot,messageReferences));
        if (!nativeRendered) {
          const renderer = window.Chat2APISafeRenderer;
          if (renderer && typeof renderer.renderMarkdown === "function") {
            renderer.renderMarkdown(body,message.content,messageReferences);
          }
          else body.textContent = message.content;
        }
      }
      if (message.role === "assistant") {
        renderToolActivity(body,message,{includeReferences: !nativeRendered});
        if (!nativeRendered) renderStandaloneMessageReferences(body,message);
      }
      if (state.streaming && message.id === state.streamingMessageId) {
        const caret = document.createElement("span");
        caret.className = "streaming-caret";
        caret.setAttribute("aria-label", "正在生成");
        body.appendChild(caret);
      }
      const actions = document.createElement("div");
      actions.className = "message-actions";
      if (message.role === "assistant" && message.pending && message.requestId) {
        const recover = document.createElement("button");
        recover.type = "button";
        recover.className = "recovery-action";
        recover.textContent = "恢复回复";
        recover.addEventListener("click", () => { void recoverPendingMessage(conversation, message); });
        actions.appendChild(recover);
      }
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "复制";
      copy.addEventListener("click", async () => {
        try {
          const references = referencesForMessage(message);
          const referenceLines = references.map((item,index) =>
            "[" + (index + 1) + "] " + item.title + " — " + item.url
          );
          const copied = [
            citationTextForMessage(message),
            ...messageImageIds(message).map(() => "[图片附件]"),
            ...messageFileAttachments(message).map((file) => "[文件附件: " + file.filename + "]"),
            ...(referenceLines.length ? ["", "引用来源", ...referenceLines] : []),
          ].filter(Boolean).join("\n");
          await navigator.clipboard.writeText(copied);
          toast("消息已复制");
        }
        catch { toast("无法访问剪贴板"); }
      });
      actions.appendChild(copy);
      content.append(header, body, actions);
      article.append(avatar, content);
      root.appendChild(article);
    });
    const screen = $("chatScreen");
    screen.scrollTop = screen.scrollHeight;
  }

  function renderAll() {
    renderSidebar();
    const conversation = currentConversation();
    if (conversation && conversation.messages.length) renderMessages();
    else showOnly("welcomeScreen");
    $("exportButton").disabled = !conversation || !conversation.messages.length;
    $("exportJsonButton").disabled = !conversation || !conversation.messages.length;
  }

  function autoTitle(conversation) {
    if (!conversation || conversation.title !== "新对话") return;
    const first = conversation.messages.find((message) => message.role === "user");
    if (first) conversation.title = messageText(first).replace(/\s+/g, " ").trim().slice(0, 32) || (messageImageIds(first).length ? "图片附件" : (messageFileAttachments(first).length ? "文件附件" : "新对话"));
  }

  function selectedKeyId() {
    return String($("apiKeySelect")?.value || "");
  }

  function selectedKeyHasScope(scope) {
    const key = state.keys.find((row) => row.key_id === selectedKeyId());
    return Boolean(key && (!Array.isArray(key.scopes) || key.scopes.includes(scope)));
  }

  function resizeComposer() {
    const input = $("messageInput");
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 220) + "px";
    const conversation = currentConversation();
    const pendingForConversation = (state.pendingImages.length > 0 && state.pendingImagesConversationId === conversation?.id) ||
      (state.pendingFiles.length > 0 && state.pendingFilesConversationId === conversation?.id);
    const keyMatchesConversation = !conversation?.apiKeyId || conversation.apiKeyId === selectedKeyId();
    const unresolvedRequest = Boolean(conversation?.messages?.some((message) => message.role === "assistant" && message.pending === true));
    $("sendButton").disabled = state.streaming || state.voiceActive || unresolvedRequest || (!input.value.trim() && !pendingForConversation) ||
      !state.apiToken || !selectedKeyHasScope("chat") || !keyMatchesConversation;
    $("attachButton").disabled = state.streaming || state.voiceActive || !state.apiToken || !selectedKeyHasScope("files");
    $("fileButton").disabled = state.streaming || state.voiceActive || !state.apiToken || !selectedKeyHasScope("files");
    updateVoiceControls();
  }

  function renderPendingImages() {
    const root = $("pendingImages");
    root.replaceChildren();
    state.pendingImages.forEach((image, index) => {
      const chip = document.createElement("div");
      chip.className = "attachment-chip";
      const preview = document.createElement("img");
      preview.src = image.previewUrl;
      preview.alt = image.filename;
      const name = document.createElement("span");
      name.className = "attachment-name";
      name.textContent = image.filename;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "attachment-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "移除图片 " + image.filename);
      remove.addEventListener("click", async () => {
        const [removed] = state.pendingImages.splice(index, 1);
        if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
        if (removed?.fileId) {
          try {
            await apiJson("/api/user/files/" + encodeURIComponent(removed.fileId), {
              method: "DELETE",
            });
          } catch {
            toast("图片已移出待发送列表，但服务器临时文件未能自动清理。");
          }
        }
        if (!state.pendingImages.length) {
          state.pendingImagesConversationId = "";
          const conversation = currentConversation();
          if (conversation && !state.pendingFiles.length && !conversation.messages.some((message) => messageAttachmentIds(message).length)) {
            conversation.apiKeyId = "";
            saveHistory();
          }
        }
        renderPendingImages();
        resizeComposer();
      });
      chip.append(preview, name, remove);
      root.appendChild(chip);
    });
    root.classList.toggle("hidden", state.pendingImages.length === 0);
  }

  function renderPendingFiles() {
    const root = $("pendingFiles");
    root.replaceChildren();
    state.pendingFiles.forEach((file, index) => {
      const chip = document.createElement("div");
      chip.className = "attachment-file-chip";
      const icon = document.createElement("span");
      icon.className = "attachment-file-icon";
      icon.textContent = "文件";
      const details = document.createElement("span");
      details.className = "attachment-file-details";
      const name = document.createElement("span");
      name.className = "attachment-file-name";
      name.textContent = file.filename;
      const meta = document.createElement("span");
      meta.className = "attachment-file-meta";
      meta.textContent = formatFileSize(file.size) + (file.fileId ? " · 已上传" : "");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "attachment-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "移除文件 " + file.filename);
      remove.addEventListener("click", async () => {
        const [removed] = state.pendingFiles.splice(index, 1);
        if (removed?.fileId) {
          try {
            await apiJson("/api/user/files/" + encodeURIComponent(removed.fileId), {method:"DELETE"});
          } catch { toast("文件已移出待发送列表，但服务器临时文件未能自动清理。"); }
        }
        if (!state.pendingFiles.length) {
          state.pendingFilesConversationId = "";
          const conversation = currentConversation();
          if (conversation && !state.pendingImages.length &&
              !conversation.messages.some((message) => messageAttachmentIds(message).length)) {
            conversation.apiKeyId = "";
            saveHistory();
          }
        }
        renderPendingFiles();
        resizeComposer();
      });
      details.append(name, meta);
      chip.append(icon, details, remove);
      root.appendChild(chip);
    });
    root.classList.toggle("hidden", state.pendingFiles.length === 0);
  }

  async function uploadPendingFiles() {
    for (const file of state.pendingFiles) {
      if (file.fileId) continue;
      const dataUrl = await readFileAsDataUrl(file.file);
      const comma = dataUrl.indexOf(",");
      if (comma < 0) throw new Error("文件编码失败");
      const payload = await apiJson("/v1/files", {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + state.apiToken,
          "Content-Type": "application/json",
          "X-Chat2API-Source": "web-chat",
        },
        body: JSON.stringify({
          filename: file.filename,
          mime_type: file.mimeType,
          data_base64: dataUrl.slice(comma + 1),
          purpose: "assistants",
        }),
      });
      const fileId = String(payload.id || "");
      if (!FILE_ID_PATTERN.test(fileId)) throw new Error("服务器没有返回有效的文件 ID");
      file.fileId = fileId;
      file.file = null;
    }
  }

  async function handleDocumentFiles(fileList) {
    if (state.streaming) return;
    if (!state.apiToken) { toast("请先登录并选择 API Key。"); return; }
    if (!selectedKeyHasScope("files")) { toast("当前 API Key 没有 files 权限。"); return; }
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const conversation = currentConversation() || createConversation();
    if (!conversation) return;
    if (conversation.apiKeyId && conversation.apiKeyId !== selectedKeyId()) {
      toast("该对话已有附件，请切换回上传附件时使用的 API Key。");
      return;
    }
    if ((state.pendingImages.length && state.pendingImagesConversationId !== conversation.id) ||
        (state.pendingFiles.length && state.pendingFilesConversationId !== conversation.id)) {
      toast("请先发送或移除其他对话的待发送附件。");
      return;
    }
    let totalBytes = state.pendingImages.reduce((sum, image) => sum + image.size, 0) +
      state.pendingFiles.reduce((sum, file) => sum + file.size, 0);
    for (const file of files) {
      if (state.pendingImages.length + state.pendingFiles.length >= MAX_FILES_PER_MESSAGE) {
        toast("每条消息最多添加 " + MAX_FILES_PER_MESSAGE + " 个附件。");
        break;
      }
      const extension = String(file.name || "").split(".").pop().toLowerCase();
      const mimeType = DOCUMENT_MIME_BY_EXTENSION[extension];
      if (!mimeType) {
        toast("暂不支持该文件类型；支持 PDF、TXT、Markdown、CSV、JSON、RTF、DOCX、XLSX 和 PPTX。");
        continue;
      }
      if (!file.size || file.size > MAX_DOCUMENT_BYTES || totalBytes + file.size > MAX_TOTAL_IMAGE_BYTES) {
        toast("单个文件上限 20 MiB，单条消息附件总量上限 20 MiB。");
        continue;
      }
      state.pendingFiles.push({
        file, fileId:"", filename:String(file.name || "document").slice(0,180),
        mimeType, size:file.size,
      });
      state.pendingFilesConversationId = conversation.id;
      totalBytes += file.size;
    }
    renderPendingFiles();
    resizeComposer();
    $("messageInput").focus();
  }


  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("读取文件失败"));
      reader.onload = () => resolve(String(reader.result || ""));
      reader.readAsDataURL(file);
    });
  }

  async function uploadPendingImages() {
    for (const image of state.pendingImages) {
      if (image.fileId) continue;
      const dataUrl = await readFileAsDataUrl(image.file);
      const comma = dataUrl.indexOf(",");
      if (comma < 0) throw new Error("图片编码失败");
      const payload = await apiJson("/v1/files", {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + state.apiToken,
          "Content-Type": "application/json",
          "X-Chat2API-Source": "web-chat",
        },
        body: JSON.stringify({
          filename: image.filename,
          mime_type: image.mimeType,
          data_base64: dataUrl.slice(comma + 1),
          purpose: "vision",
        }),
      });
      const fileId = String(payload.id || "");
      if (!FILE_ID_PATTERN.test(fileId)) throw new Error("服务器没有返回有效的图片文件 ID");
      image.fileId = fileId;
      image.file = null;
    }
  }

  async function handleImageFiles(fileList) {
    if (state.streaming) return;
    if (!state.apiToken) {
      toast("请先登录并选择 API Key。");
      return;
    }
    if (!selectedKeyHasScope("files")) {
      toast("当前 API Key 没有 files 权限，请更换 Key 或在控制台调整权限。");
      return;
    }
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const conversation = currentConversation() || createConversation();
    if (!conversation) return;
    if (conversation.apiKeyId && conversation.apiKeyId !== selectedKeyId()) {
      toast("该对话已有图片附件，请切换回上传图片时使用的 API Key。");
      return;
    }
    if (state.pendingFiles.length && state.pendingFilesConversationId !== conversation.id) {
      toast("请先发送或移除其他对话的待发送附件。");
      return;
    }
    if (state.pendingImages.length && state.pendingImagesConversationId !== conversation.id) {
      toast("请先发送或移除其他对话的待发送图片。");
      return;
    }
    let totalBytes = state.pendingImages.reduce((sum, image) => sum + image.size, 0) + state.pendingFiles.reduce((sum, file) => sum + file.size, 0);
    for (const file of files) {
      if (state.pendingImages.length + state.pendingFiles.length >= MAX_FILES_PER_MESSAGE) {
        toast("每条消息最多添加 " + MAX_FILES_PER_MESSAGE + " 个附件。");
        break;
      }
      if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
        toast("暂不支持 " + (file.type || "未知格式") + "；仅支持 PNG、JPEG、WebP 和 GIF。");
        continue;
      }
      if (!file.size || file.size > MAX_IMAGE_BYTES || totalBytes + file.size > MAX_TOTAL_IMAGE_BYTES) {
        toast("单张图片上限 10 MiB，单条消息图片总量上限 20 MiB。");
        continue;
      }
      state.pendingImages.push({
        file,
        fileId: "",
        filename: String(file.name || "image").slice(0, 180),
        mimeType: file.type,
        size: file.size,
        previewUrl: URL.createObjectURL(file),
      });
      state.pendingImagesConversationId = conversation.id;
      totalBytes += file.size;
    }
    renderPendingImages();
    resizeComposer();
    $("messageInput").focus();
  }

  function appendDelta(assistant, delta) {
    if (typeof delta === "string") {
      assistant.content += delta;
      return;
    }
    if (Array.isArray(delta)) {
      for (const part of delta) {
        if (typeof part?.text === "string") assistant.content += part.text;
        else if (typeof part?.content === "string") assistant.content += part.content;
      }
    }
  }

  class StreamResponseError extends Error {}

  function normalizeToolStatus(value) {
    const status = String(value ?? "").trim().toLowerCase();
    if (["running","in_progress","pending","streaming","queued","waiting",
         "retrying","requires_action"].includes(status)) return "running";
    if (["failed","error","errored","incomplete","timeout","timed_out",
         "rejected"].includes(status)) return "failed";
    if (["cancelled","canceled","aborted"].includes(status)) return "cancelled";
    if (["completed","complete","succeeded","success","finished","done"].includes(status)) return "completed";
    // Unknown upstream states must never be presented as successful completion.
    return "unknown";
  }

  function applyToolEvent(assistant, event) {
    if (!assistant || !event || typeof event !== "object") return false;
    const toolName = safeToolName(event.tool_name);
    const callId = String(event.call_id || "").slice(0,128);
    const resultId = String(event.result_id || "").slice(0,128);
    const validId = value => /^[A-Za-z0-9_.:-]{1,128}$/.test(value);
    if (!validId(callId) && !validId(resultId)) return false;
    const rows = Array.isArray(assistant.toolEvents) ? assistant.toolEvents : [];
    let row = rows.find(item => (callId && item.callId === callId) ||
      (resultId && item.resultId === resultId));
    if (!row) {
      row = {
        toolName,callId,resultId:"",
        status:event.phase === "call" ? "running" : "unknown",
        arguments:"",output:"",
      };
      rows.push(row);
    } else if (["completed","failed","cancelled"].includes(row.status)) {
      // A terminal result cannot be reopened or overwritten by a replayed
      // call/late retry. A first result may still enrich a terminal call event
      // that carried no result ID/output (the stream sends those separately).
      if (event.phase !== "result" || (row.resultId && resultId && row.resultId !== resultId)) {
        return false;
      }
      let enriched = false;
      if (!row.resultId && resultId) {
        row.resultId = resultId;
        enriched = true;
      }
      if (!row.arguments && typeof event.arguments === "string" && event.arguments) {
        row.arguments = event.arguments.slice(0,1200);
        enriched = true;
      }
      if (!row.output && typeof event.output === "string" && event.output) {
        row.output = event.output.slice(0,1200);
        enriched = true;
      }
      const references = normalizeToolReferences(event.references);
      if (references.length) {
        const previous = Array.isArray(row.references) ? row.references : [];
        const merged = normalizeToolReferences([...previous, ...references]);
        if (!toolReferencesEqual(merged, previous)) {
          row.references = merged;
          enriched = true;
        }
        const messagePrevious = Array.isArray(assistant.references) ? assistant.references : [];
        const messageMerged = normalizeToolReferences([...messagePrevious, ...references]);
        if (!toolReferencesEqual(messageMerged, messagePrevious)) {
          assistant.references = messageMerged;
          enriched = true;
        }
      }
      if (enriched) {
        while (rows.length > 6) rows.shift();
        assistant.toolEvents = rows;
        saveHistory();
      }
      return enriched;
    }
    row.toolName = toolName;
    if (callId) row.callId = callId;
    if (resultId) row.resultId = resultId;
    const nextStatus = normalizeToolStatus(event.status);
    // Unknown metadata cannot regress a known running state or imply success.
    if (nextStatus !== "unknown") row.status = nextStatus;
    if (typeof event.arguments === "string" && event.arguments) row.arguments = event.arguments.slice(0,1200);
    if (typeof event.output === "string" && event.output) row.output = event.output.slice(0,1200);
    const references = normalizeToolReferences(event.references);
    if (references.length) {
      row.references = normalizeToolReferences([...(Array.isArray(row.references) ? row.references : []), ...references]);
      assistant.references = normalizeToolReferences([
        ...(Array.isArray(assistant.references) ? assistant.references : []),
        ...references,
      ]);
    }
    while (rows.length > 6) rows.shift();
    assistant.toolEvents = rows;
    saveHistory();
    return true;
  }

  function applyMessageReferences(assistant, value) {
    if (!assistant || assistant.role !== "assistant") return false;
    const incoming = normalizeToolReferences(value);
    if (!incoming.length) return false;
    const previous = Array.isArray(assistant.references) ? assistant.references : [];
    const merged = normalizeToolReferences([...previous, ...incoming]);
    const changed = !toolReferencesEqual(merged, previous);
    if (!changed) return false;
    assistant.references = merged;
    saveHistory();
    return true;
  }

  function renderStandaloneMessageReferences(body, message) {
    const references = referencesForMessage(message);
    if (!references.length) return;
    const eventReferences = normalizeToolReferences(
      (Array.isArray(message.toolEvents) ? message.toolEvents : [])
        .flatMap(item => Array.isArray(item.references) ? item.references : [])
    );
    const eventKeys = new Set(eventReferences.map(item =>
      item.id ? "id:" + item.id : "url:" + item.url
    ));
    const standalone = references.filter(item =>
      !eventKeys.has(item.id ? "id:" + item.id : "url:" + item.url)
    );
    if (!standalone.length) return;
    const section = document.createElement("section");
    section.className = "message-tool-events";
    const referenceBox = document.createElement("div");
    referenceBox.className = "message-tool-references";
    const heading = document.createElement("div");
    heading.className = "message-tool-event-label";
    heading.textContent = "引用来源";
    referenceBox.appendChild(heading);
    standalone.forEach(reference => {
      const link = document.createElement("a");
      link.className = "message-tool-reference";
      link.href = reference.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer nofollow";
      link.referrerPolicy = "no-referrer";
      const title = document.createElement("strong");
      const number = references.findIndex(item =>
        (item.id && reference.id) ? item.id === reference.id : item.url === reference.url
      ) + 1;
      title.textContent = "[" + number + "] " + reference.title;
      link.appendChild(title);
      if (reference.snippet) {
        const snippet = document.createElement("span");
        snippet.textContent = reference.snippet;
        link.appendChild(snippet);
      }
      referenceBox.appendChild(link);
    });
    section.appendChild(referenceBox);
    body.appendChild(section);
  }

  function toolStatusLabel(status) {
    return ({running:"执行中",completed:"已完成",failed:"失败",cancelled:"已取消"})[status] || "状态未知";
  }

  function renderToolActivity(body, message, options = {}) {
    const events = Array.isArray(message?.toolEvents) ? message.toolEvents.slice(-6) : [];
    if (!events.length) return;
    const includeReferences = options.includeReferences !== false;
    const messageReferences = includeReferences ? referencesForMessage(message) : [];
    const referenceNumbers = new Map(messageReferences.map((reference,index) => [
      reference.id ? "id:" + reference.id : "url:" + reference.url, index + 1,
    ]));
    const section = document.createElement("section");
    section.className = "message-tool-events";
    events.forEach(item => {
      const details = document.createElement("details");
      details.className = "message-tool-event";
      const itemReferences = normalizeToolReferences(item.references);
      details.open = item.status === "running" || (includeReferences && itemReferences.length > 0);
      const summary = document.createElement("summary");
      summary.textContent = String(item.toolName || "tool").slice(0,80) + " · " + toolStatusLabel(item.status);
      details.appendChild(summary);
      for (const [label,value] of [["调用参数",item.arguments],["执行结果",item.output]]) {
        if (typeof value !== "string" || !value) continue;
        const heading = document.createElement("div");
        heading.className = "message-tool-event-label";
        heading.textContent = label;
        const pre = document.createElement("pre");
        pre.className = "message-tool-event-data";
        pre.textContent = value;
        details.append(heading,pre);
      }
      const references = includeReferences ? itemReferences : [];
      if (references.length) {
        const referenceBox = document.createElement("div");
        referenceBox.className = "message-tool-references";
        const referenceLabel = document.createElement("div");
        referenceLabel.className = "message-tool-event-label";
        referenceLabel.textContent = "参考来源";
        referenceBox.appendChild(referenceLabel);
        references.forEach(reference => {
          const link = document.createElement("a");
          link.className = "message-tool-reference";
          link.href = reference.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer nofollow";
          link.referrerPolicy = "no-referrer";
          const title = document.createElement("strong");
          const referenceNumber = referenceNumbers.get(
            reference.id ? "id:" + reference.id : "url:" + reference.url
          );
          title.textContent = (referenceNumber ? "[" + referenceNumber + "] " : "") + reference.title;
          link.appendChild(title);
          if (reference.snippet) {
            const snippet = document.createElement("span");
            snippet.textContent = reference.snippet;
            link.appendChild(snippet);
          }
          referenceBox.appendChild(link);
        });
        details.appendChild(referenceBox);
      }
      section.appendChild(details);
    });
    body.appendChild(section);
  }

  function consumeSseFrame(frame, assistant) {
    const lines = String(frame || "").split(/\r?\n/);
    let eventName = "message";
    let eventId = 0;
    const dataLines = [];
    for (const line of lines) {
      if (line.startsWith("id:")) {
        const parsed = Number(line.slice(3).trim());
        if (Number.isSafeInteger(parsed) && parsed >= 0) eventId = parsed;
      } else if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (eventId && eventId > Number(assistant.lastEventId || 0)) {
      assistant.lastEventId = eventId;
    }
    const data = dataLines.join("\n");
    if (!data && eventName === "cursor") return false;
    if (!data) return false;
    if (data.trim() === "[DONE]") return true;
    let event;
    try { event = JSON.parse(data); } catch { return false; }

    if (eventName === "delivery.delta") {
      appendDelta(assistant, event.delta);
      return false;
    }
    if (eventName === "chat.references") {
      applyMessageReferences(assistant, event.references);
      return false;
    }
    if (eventName === "chat.tool.call" || eventName === "chat.tool.result") {
      applyToolEvent(assistant, {
        ...event,
        phase:eventName === "chat.tool.call" ? "call" : "result",
      });
      return false;
    }
    if (eventName === "recovery.partial.snapshot") {
      if (Array.isArray(event.references)) {
        applyMessageReferences(assistant, event.references);
      }
      if (event.partial_text_unverified === true && typeof event.partial_text === "string") {
        const candidate = event.partial_text.slice(0, MAX_MESSAGE_CHARS);
        // Worker and delivery snapshots are unverified candidates. Never let
        // a shorter replay page overwrite progress already shown to the user.
        if (candidate.length > String(assistant.content || "").length) {
          assistant.content = candidate;
        }
      }
      return false;
    }
    if (eventName === "delivery.completed") {
      assistant.replayTerminalStatus = "completed";
      return true;
    }
    if (eventName === "delivery.error" || eventName === "delivery.cancelled"
        || eventName === "delivery.stalled" || eventName === "recovery.stalled"
        || eventName === "recovery.terminal") {
      if (Array.isArray(event.references)) applyMessageReferences(assistant, event.references);
      const status = String(event.status || (
        eventName === "delivery.cancelled" ? "cancelled" :
        eventName === "delivery.error" ? "error" :
        eventName === "recovery.stalled" ? "stalled" : "unknown"
      ));
      if (status === "completed" && typeof event.response_text === "string") {
        assistant.content = event.response_text.slice(0, MAX_MESSAGE_CHARS);
      } else if (typeof event.partial_text === "string" && event.partial_text) {
        assistant.content = event.partial_text.slice(0, MAX_MESSAGE_CHARS);
      }
      assistant.replayTerminalStatus = status;
      return true;
    }
    if (eventName === "replay.reset") {
      if (Array.isArray(event.references)) applyMessageReferences(assistant, event.references);
      if (event.status === "completed" && typeof event.response_text === "string") {
        assistant.content = event.response_text.slice(0, MAX_MESSAGE_CHARS);
        assistant.replayTerminalStatus = "completed";
        return true;
      }
      if (event.status === "stalled" && typeof event.partial_text === "string") {
        assistant.content = event.partial_text.slice(0, MAX_MESSAGE_CHARS);
        assistant.replayTerminalStatus = "stalled";
        return true;
      }
      return false;
    }
    if (event?.error) {
      throw new StreamResponseError(event.error.message || "服务端返回流式错误");
    }
    const choice = Array.isArray(event?.choices) ? event.choices[0] : null;
    const eventCursor = choice?.delta?.chat2api_event_id
      ?? choice?.delta?.chat2api_tool?.chat2api_event_id
      ?? choice?.delta?.chat2api_references?.chat2api_event_id;
    if (Number.isSafeInteger(eventCursor) && eventCursor > Number(assistant.lastEventId || 0)) {
      assistant.lastEventId = eventCursor;
    }
    if (choice?.delta && Object.prototype.hasOwnProperty.call(choice.delta, "content")) {
      appendDelta(assistant, choice.delta.content);
    }
    if (choice?.delta?.chat2api_tool) applyToolEvent(assistant,choice.delta.chat2api_tool);
    if (choice?.delta?.chat2api_references) {
      applyMessageReferences(assistant, choice.delta.chat2api_references.references);
    }
    return false;
  }

  function recoveryStatusLabel(status) {
    const labels = {
      streaming: "生成中", recovering: "恢复中", recovery_pending: "等待服务端确认",
      queued: "排队中", running: "生成中", reconnecting: "重新连接中",
      completed: "已完成", completed_without_text: "已完成，但无可恢复文本",
      cancellation_requested: "正在确认停止", cancelled: "已取消", error: "请求失败",
      stalled: "请求停滞", auth_required: "需要重新登录", not_found: "请求记录不存在",
      recovery_unavailable: "暂时无法恢复，可稍后重试", recovery_timeout: "恢复超时",
      recovery_failed: "恢复失败", recovery_error: "恢复连接异常", voice_partial: "语音中断 · 部分回复",
      invalid_response: "恢复响应无效", request_mismatch: "请求身份不匹配",
      invalid_request: "请求记录无效", polling_stopped: "恢复已停止",
    };
    return labels[String(status || "")] || "请求状态：" + String(status || "unknown");
  }

  function makeRecoveryController(conversation, assistant) {
    const recoveryApi = window.Chat2APIStreamRecoveryV1;
    if (!recoveryApi || typeof recoveryApi.create !== "function") return null;
    return recoveryApi.create({
      onUpdate(update) {
        if (!update || update.request_id !== assistant.requestId || update.assistant_message_id !== assistant.id) return;
        if (Array.isArray(update.references)) applyMessageReferences(assistant, update.references);
        assistant.recoveryStatus = String(update.status || "recovering").slice(0, 40);
        if (Number.isSafeInteger(update.last_event_id)
            && update.last_event_id > Number(assistant.lastEventId || 0)) {
          assistant.lastEventId = update.last_event_id;
        }
        if (update.status === "completed" && update.terminal === true && typeof update.response_text === "string") {
          assistant.content = update.response_text.slice(0, MAX_MESSAGE_CHARS);
        } else if (update.status === "stalled" && update.terminal === true
            && typeof update.partial_text === "string" && update.partial_text) {
          assistant.content = update.partial_text.slice(0, MAX_MESSAGE_CHARS);
        } else if (update.partial_text_unverified === true
            && typeof update.partial_text === "string" && update.partial_text.length > assistant.content.length) {
          // Show durable Worker/delivery progress during the restart grace,
          // but keep it explicitly provisional until terminal reconciliation.
          assistant.content = update.partial_text.slice(0, MAX_MESSAGE_CHARS);
        }
        saveHistory();
        if (currentConversation()?.id === conversation.id) renderMessages();
      },
      onEvent(item) {
        if (!item || item.request_id !== assistant.requestId
            || item.assistant_message_id !== assistant.id) return;
        const idValue = Number(item.id || 0);
        if (Number.isSafeInteger(idValue) && idValue > Number(assistant.lastEventId || 0)) {
          assistant.lastEventId = idValue;
        }
        const frame = [
          "id: " + String(item.id || 0),
          "event: " + String(item.event || "message"),
          "data: " + JSON.stringify(item.data || {}),
        ].join("\n");
        consumeSseFrame(frame, assistant);
        conversation.updatedAt = new Date().toISOString();
        saveHistory();
        if (currentConversation()?.id === conversation.id) renderMessages();
      },
    });
  }

  async function recoverPendingMessage(conversation, assistant, existingController = null) {
    if (!assistant || assistant.pending !== true || !assistant.requestId) return {status:"not_pending"};
    const record = {request_id:String(assistant.requestId),assistant_message_id:String(assistant.id || "")};
    const recoveryKey = record.request_id + ":" + record.assistant_message_id;
    if (state.recoveryInFlight.has(recoveryKey)) return {status:"already_recovering"};
    const controller = existingController || makeRecoveryController(conversation, assistant);
    if (!controller) {
      // Keep the request recoverable in the UI. A temporarily missing asset or
      // unavailable recovery transport must not permanently hide the retry action.
      assistant.pending = true;
      assistant.recoveryStatus = "recovery_unavailable";
      saveHistory();
      if (currentConversation()?.id === conversation.id) renderMessages();
      return {status:"recovery_unavailable"};
    }
    state.recoveryInFlight.add(recoveryKey);
    assistant.recoveryStatus = "recovering";
    saveHistory();
    if (currentConversation()?.id === conversation.id) renderMessages();
    try {
      let result = null;
      if (typeof controller.replay === "function") {
        const replay = await controller.replay(record, Number(assistant.lastEventId || 0));
        if (replay?.terminal === true) {
          result = replay;
          // A delivery.completed journal event is a terminal marker, not
          // necessarily the complete answer body. Fetch the owner-scoped
          // snapshot before closing the turn so a compacted/replayed stream
          // cannot leave an empty assistant message marked as completed.
          if (replay.status === "completed" && typeof replay.response_text !== "string") {
            const snapshot = await controller.resume(record);
            if (snapshot?.terminal === true) result = snapshot;
          }
        }
      }
      if (!result) result = await controller.resume(record);
      const status = String(result?.status || "recovery_failed");
      if (result?.terminal === true) {
        if (Array.isArray(result.references)) applyMessageReferences(assistant, result.references);
        assistant.pending = false;
        assistant.recoveryStatus = status;
        if (status === "completed" && typeof result.response_text === "string") assistant.content = result.response_text.slice(0, MAX_MESSAGE_CHARS);
        else if (status === "stalled" && typeof result.partial_text === "string" && result.partial_text) assistant.content = result.partial_text.slice(0, MAX_MESSAGE_CHARS);
      } else if (["recovery_timeout", "polling_stopped", "auth_required", "recovery_unavailable"].includes(status)) {
        // An unknown Broker lease is not a terminal result. A Worker outbox may
        // still reconcile it later, so keep the turn pending and allow a retry.
        assistant.pending = true;
        assistant.recoveryStatus = status;
      } else {
        assistant.pending = false;
        assistant.recoveryStatus = status;
        if (status === "not_found" && !assistant.content) assistant.content = "请求未被服务端接收，或请求记录已过期，无法恢复。";
      }
      return result;
    } catch (error) {
      assistant.pending = !(error && error.name === "TypeError");
      assistant.recoveryStatus = assistant.pending ? "recovery_error" : "invalid_request";
      return {status:assistant.recoveryStatus};
    } finally {
      state.recoveryInFlight.delete(recoveryKey);
      conversation.updatedAt = new Date().toISOString();
      saveHistory();
      if (currentConversation()?.id === conversation.id) renderMessages();
      resizeComposer();
    }
  }

  function assistantMessageForRequest(requestId) {
    for (const conversation of state.conversations) {
      const message = (conversation.messages || []).find(item => item.role === "assistant" && item.requestId === requestId);
      if (message) return {conversation,message};
    }
    return null;
  }
  function applyNativeSnapshot(requestId,snapshot) {
    const renderer = window.Chat2APINativeRendererV1;
    if (!renderer || typeof renderer.validateSnapshot !== "function" || !renderer.validateSnapshot(snapshot)) return false;
    state.nativeSnapshots.set(requestId,snapshot);
    while (state.nativeSnapshots.size > 24) state.nativeSnapshots.delete(state.nativeSnapshots.keys().next().value);
    const found = assistantMessageForRequest(requestId);
    if (found) found.message.nativeFinality = snapshot.finality;
    if (found && currentConversation()?.id === found.conversation.id) renderMessages();
    return true;
  }
  async function refreshNativeSnapshot(requestId) {
    try {
      const response = await fetch("/api/user/chat/requests/" + encodeURIComponent(requestId) + "/native-content",
        {method:"GET",credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"}});
      if (response.status === 404) {
        const previous = state.nativeSnapshots.get(requestId);
        if (previous && previous.finality !== "verified-final") {
          state.nativeSnapshots.delete(requestId);
          const found = assistantMessageForRequest(requestId);
          if (found && currentConversation()?.id === found.conversation.id) renderMessages();
        }
        return false;
      }
      if (!response.ok) return false;
      return applyNativeSnapshot(requestId,await response.json());
    } catch (_) { return false; }
  }
  function consumeNativeSseFrame(frame,requestId) {
    const lines = frame.split(/\r?\n/);
    let eventName = "message";
    const data = [];
    for (const line of lines) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (eventName === "native.end") return true;
    if (eventName !== "native.snapshot" || !data.length) return false;
    try { applyNativeSnapshot(requestId,JSON.parse(data.join("\n"))); } catch (_) {}
    return false;
  }
  async function streamNativeContent(requestId) {
    if (!requestId || state.nativeStreams.has(requestId)) return;
    const controller = new AbortController();
    state.nativeStreams.set(requestId,controller);
    let reader = null, ended = false;
    try {
      const response = await fetch("/api/user/chat/requests/" + encodeURIComponent(requestId) + "/native-content/events",
        {method:"GET",credentials:"same-origin",cache:"no-store",headers:{Accept:"text/event-stream"},signal:controller.signal});
      if (!response.ok || !response.body) return;
      reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (!ended) {
        const part = await reader.read();
        if (part.done) break;
        buffer += decoder.decode(part.value,{stream:true});
        let boundary;
        while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
          const frame = buffer.slice(0,boundary.index);
          buffer = buffer.slice(boundary.index + boundary[0].length);
          if (consumeNativeSseFrame(frame,requestId)) { ended = true; break; }
        }
      }
    } catch (_) {
      // A presentation-sidecar failure never changes the authoritative chat terminal.
    } finally {
      if (reader) {
        if (!ended) void reader.cancel().catch(() => {});
        try { reader.releaseLock(); } catch (_) {}
      }
      state.nativeStreams.delete(requestId);
      if (!controller.signal.aborted) await refreshNativeSnapshot(requestId);
    }
  }
  async function hydrateNativeSnapshots() {
    const candidates = [], seen = new Set();
    for (const conversation of state.conversations) {
      for (const message of conversation.messages || []) {
        if (message.role !== "assistant" || !message.requestId || seen.has(message.requestId)) continue;
        seen.add(message.requestId);
        candidates.push({requestId:message.requestId,pending:message.pending === true});
      }
    }
    await Promise.all(candidates.slice(-24).map(async item => {
      if (item.pending) void streamNativeContent(item.requestId);
      await refreshNativeSnapshot(item.requestId);
    }));
  }
  function resumePendingMessages() {
    const pending = [];
    state.conversations.forEach((conversation) => {
      (conversation.messages || []).forEach((message) => {
        if (message.role === "assistant" && message.pending === true && message.requestId) {
          pending.push({conversation,message});
          void streamNativeContent(message.requestId);
        }
      });
    });
    pending.forEach(({conversation,message}) => { void recoverPendingMessage(conversation,message); });
  }
  async function sendMessage(text) {
    if (state.voiceActive) { toast("请先结束实时语音，再发送普通聊天消息。"); return; }
    if (!selectedKeyHasScope("chat")) { toast("当前 API Key 没有 chat 权限。"); return; }
    const clean = String(text || "").trim();
    if (state.streaming) return;
    const conversation = currentConversation() || createConversation();
    if (conversation.messages.some((message) => message.role === "assistant" && message.pending === true)) {
      toast("上一条回复仍在恢复；请先完成恢复后再发送。");
      return;
    }
    const recoveryApi = window.Chat2APIStreamRecoveryV1;
    if (!recoveryApi || typeof recoveryApi.createRequestId !== "function") {
      toast("请求恢复模块尚未加载，请刷新页面后重试。");
      return;
    }
    const requestId = recoveryApi.createRequestId();
    const pending = state.pendingImagesConversationId === conversation?.id ? state.pendingImages : [];
    const pendingFiles = state.pendingFilesConversationId === conversation?.id ? state.pendingFiles : [];
    if (!clean && pendingFiles.length) { toast("添加文档时请同时输入要处理的问题或指令。"); return; }
    if (!clean && !pending.length && !pendingFiles.length) return;
    if (!state.apiToken) {
      toast("请先选择一个可用的 API Key。");
      showOnly("keyRequired");
      return;
    }
    const keyId = selectedKeyId();
    if (conversation.apiKeyId && conversation.apiKeyId !== keyId) {
      toast("该对话包含由其他 API Key 上传的附件，请切换回原 API Key。");
      resizeComposer();
      return;
    }
    if ((pending.length || pendingFiles.length) && !selectedKeyHasScope("files")) {
      toast("当前 API Key 没有 files 权限，无法发送附件。");
      return;
    }
    conversation.model = state.selectedModel;
    state.streaming = true;
    state.abortController = new AbortController();
    $("sendButton").disabled = true;
    $("attachButton").disabled = true;
    $("fileButton").disabled = true;
    $("newChatButton").disabled = true;
    $("apiKeySelect").disabled = true;
    $("modelSelect").disabled = true;
    $("messageInput").disabled = true;
    setConnection((pending.length || pendingFiles.length) ? "正在上传附件…" : "正在连接…");

    let userMessage = null;
    let assistant = null;
    let requestSubmitted = false;
    try {
      try {
        const session = await apiJson("/api/user/me");
        if (String(session.user?.user_id || "") !== String(state.user?.user_id || "")) {
          throw new Error("当前登录账户已变化");
        }
      } catch {
        state.apiToken = "";
        state.user = null;
        showOnly("authRequired");
        setConnection("登录已失效", "error");
        throw new Error("用户登录状态已失效，请重新登录后再继续聊天");
      }

      if (pending.length || pendingFiles.length) {
        conversation.apiKeyId = keyId;
        saveHistory();
        await uploadPendingImages();
        await uploadPendingFiles();
      }
      const imageParts = pending.map((image) => ({
        type: "image_url",
        image_url: {url:image.fileId},
      }));
      const fileParts = pendingFiles.map((file) => ({
        type: "file", file_id:file.fileId, filename:file.filename, mime_type:file.mimeType, size:file.size,
      }));
      const userParts = [...(clean ? [{type:"text",text:clean}] : []), ...imageParts, ...fileParts];
      const userContent = userParts.length ? userParts : clean;
      userMessage = {id:id(),role:"user",content:userContent,createdAt:new Date().toISOString()};
      conversation.messages.push(userMessage);
      autoTitle(conversation);
      assistant = {id:id(),role:"assistant",content:"",createdAt:new Date().toISOString(),requestId,pending:true,recoveryStatus:"streaming",toolEvents:[],lastEventId:0};
      conversation.messages.push(assistant);
      state.recoveryController = makeRecoveryController(conversation, assistant);
      state.activeRequestRecord = {request_id:requestId, assistant_message_id:assistant.id};
      conversation.updatedAt = new Date().toISOString();
      saveHistory();
      state.pendingImages.forEach((image) => {
        if (image.previewUrl) URL.revokeObjectURL(image.previewUrl);
      });
      state.pendingImages = [];
      state.pendingImagesConversationId = "";
      state.pendingFiles = [];
      state.pendingFilesConversationId = "";
      $("messageInput").value = "";
      renderPendingImages();
      renderPendingFiles();
      state.streamingMessageId = assistant.id;
      $("stopButton").classList.remove("hidden");
      setConnection("已连接", "ready");
      resizeComposer();
      renderAll();

      const messages = conversation.messages
        .filter((message) => message.id !== assistant.id)
        .map((message) => ({role:message.role,content:message.content}));
      requestSubmitted = true;
      const response = await fetch("/api/user/chat/completions", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Authorization": "Bearer " + state.apiToken,
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
          "X-Chat2API-Request-ID": requestId,
        },
        body: JSON.stringify({
          model: state.selectedModel,
          messages,
          stream: true,
        }),
        signal: state.abortController.signal,
      });
      if (!response.ok) {
        let payload = {};
        try { payload = await response.json(); } catch {}
        if (response.status === 401 || response.status === 403) {
          state.apiToken = "";
          showOnly("keyRequired");
          setConnection("API Key 已失效或权限不足", "error");
        }
        throw new Error(payload?.error?.message || payload.detail || "请求失败，HTTP " + response.status);
      }
      if (!response.body) throw new Error("当前浏览器不支持流式响应读取");

      // Observe native rich content for this same request ID; never dispatch twice.
      void streamNativeContent(requestId);

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let done = false;
      let lastSavedLength = 0;
      try {
        while (!done) {
          const part = await reader.read();
          if (part.done) break;
          buffer += decoder.decode(part.value, {stream:true});
          let boundary;
          while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
            const frame = buffer.slice(0, boundary.index);
            buffer = buffer.slice(boundary.index + boundary[0].length);
            if (consumeSseFrame(frame, assistant)) {
              done = true;
              break;
            }
          }
          if (assistant.content || assistant.toolEvents?.length) {
            conversation.updatedAt = new Date().toISOString();
            if (assistant.content.length - lastSavedLength >= 1000) {
              saveHistory();
              lastSavedLength = assistant.content.length;
            }
            renderMessages();
          }
        }
      } finally {
        // After [DONE], let the server finish closing the HTTP response.
        // If processing failed, stop reading without masking the original error.
        if (!done) void reader.cancel().catch(() => {});
        reader.releaseLock();
      }
      if (done) {
        assistant.pending = false;
        assistant.recoveryStatus = "completed";
        if (!assistant.content.trim() && !assistant.toolEvents?.length) {
          conversation.messages = conversation.messages.filter((message) => message.id !== assistant.id);
          toast("模型没有返回文本内容。");
        }
      } else {
        assistant.pending = true;
        assistant.recoveryStatus = "recovery_pending";
        saveHistory();
        const recoveryController = state.recoveryController;
        void recoverPendingMessage(conversation, assistant, recoveryController).then((result) => {
          if (result?.status === "completed") toast("连接中断，已从服务端恢复最终回复。");
          else toast("连接已结束但未收到流结束标记；请求状态：" + recoveryStatusLabel(result?.status));
        });
      }
    } catch (error) {
      if (error instanceof StreamResponseError) {
        assistant.pending = false;
        assistant.recoveryStatus = "error";
        // Do not leave an empty assistant placeholder when the server failed
        // before delivering any content; preserve the message when partial
        // content was already streamed.
        if (!assistant.content && !assistant.toolEvents?.length) {
          conversation.messages = conversation.messages.filter((message) => message.id !== assistant.id);
        }
        toast("请求失败：" + error.message);
        return;
      }
      if (assistant && requestSubmitted && assistant.requestId) {
        assistant.pending = true;
        assistant.recoveryStatus = error.name === "AbortError" ? "cancellation_requested" : "recovery_pending";
        saveHistory();
        const recoveryController = state.recoveryController;
        void recoverPendingMessage(conversation, assistant, recoveryController).then((result) => {
          if (result?.status === "completed") toast("连接中断，已恢复服务端最终回复。");
          else if (result?.status === "cancelled") toast("请求已取消。");
          else if (result?.status === "completed_without_text") toast("请求已完成，但服务端没有可恢复的文本。");
          else toast((error.name === "AbortError" ? "停止状态：" : "请求失败，恢复状态：") + recoveryStatusLabel(result?.status));
        });
      } else {
        if (assistant && !assistant.content.trim() && !assistant.toolEvents?.length) conversation.messages = conversation.messages.filter((message) => message.id !== assistant.id);
        toast("请求失败：" + error.message);
      }
    } finally {
      state.streaming = false;
      state.streamingMessageId = "";
      state.abortController = null;
      state.recoveryController = null;
      state.activeRequestRecord = null;
      conversation.updatedAt = new Date().toISOString();
      saveHistory();
      if (state.apiToken) renderAll();
      else showOnly(state.user ? "keyRequired" : "authRequired");
      $("stopButton").classList.add("hidden");
      $("newChatButton").disabled = false;
      $("apiKeySelect").disabled = false;
      $("modelSelect").disabled = false;
      $("messageInput").disabled = false;
      renderPendingImages();
      renderPendingFiles();
      resizeComposer();
      $("messageInput").focus();
    }
  }

  async function selectApiKey(keyId) {
    state.apiToken = "";
    const key = state.keys.find((row) => row.key_id === keyId);
    if (!key || !key.enabled || key.expired || key.revoked_at || !key.secret_recoverable) {
      setConnection("需要可用 Key", "error");
      showOnly("keyRequired");
      resizeComposer();
      return false;
    }
    setConnection("验证 API Key…");
    try {
      const payload = await apiJson("/api/user/keys/" + encodeURIComponent(keyId) + "/secret");
      state.apiToken = String(payload.token || "");
      if (!state.apiToken) throw new Error("API Key 密钥为空");
      try { localStorage.setItem("chat2api.webchat.key." + state.user.user_id, keyId); } catch {}
      await loadModels();
      const conversation = currentConversation();
      if (conversation?.apiKeyId && conversation.apiKeyId !== keyId) {
        setConnection("此对话需要原 API Key", "error");
      } else {
        setConnection("已连接", "ready");
      }
      resizeComposer();
      renderAll();
      return true;
    } catch (error) {
      setConnection("Key 不可用", "error");
      toast("无法读取所选 API Key：" + error.message + "。请创建一个新 Key。");
      showOnly("keyRequired");
      resizeComposer();
      return false;
    }
  }

  async function loadKeys() {
    const payload = await apiJson("/api/user/keys");
    state.keys = Array.isArray(payload.data) ? payload.data : [];
    const usable = state.keys.filter((key) => key.enabled && !key.expired && !key.revoked_at && key.secret_recoverable &&
      (!Array.isArray(key.scopes) || key.scopes.includes("chat") || key.scopes.includes("audio")));
    const select = $("apiKeySelect");
    select.replaceChildren();
    if (!usable.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "无可用 API Key";
      select.appendChild(option);
      $("keyRequired").classList.remove("hidden");
      showOnly("keyRequired");
      setConnection("需要 API Key", "error");
      return false;
    }
    usable.forEach((key) => {
      const option = document.createElement("option");
      option.value = key.key_id;
      option.textContent = (key.name || "API Key") + " · " + (key.prefix || key.key_id);
      select.appendChild(option);
    });
    let preferred = "";
    try { preferred = localStorage.getItem("chat2api.webchat.key." + state.user.user_id) || ""; } catch {}
    const conversationKeyId = currentConversation()?.apiKeyId || "";
    const selected = usable.find((key) => key.key_id === conversationKeyId) ||
      usable.find((key) => key.key_id === preferred) || usable[0];
    select.value = selected.key_id;
    return selectApiKey(selected.key_id);
  }

  async function loadModels() {
    const select = $("modelSelect");
    let rows = [];
    try {
      const payload = await apiJson("/v1/models", {headers:{"Authorization":"Bearer " + state.apiToken}});
      rows = Array.isArray(payload.data) ? payload.data : [];
    } catch {
      try {
        const payload = await apiJson("/api/user/models");
        rows = Array.isArray(payload.data) ? payload.data.map((row) => ({id:row.model_id || row.id})) : [];
      } catch {}
    }
    const ids = [...new Set(rows.map((row) => String(row.id || row.model || "")).filter(Boolean))];
    if (!ids.includes("gpt-5.6-sol") && !ids.length) ids.push("gpt-5.6-sol");
    select.replaceChildren();
    ids.forEach((modelId) => {
      const option = document.createElement("option");
      option.value = modelId;
      option.textContent = modelId;
      select.appendChild(option);
    });
    const conversation = currentConversation();
    const preferred = conversation?.model || state.selectedModel;
    state.selectedModel = ids.includes(preferred) ? preferred : (ids[0] || "gpt-5.6-sol");
    select.value = state.selectedModel;
    if (!rows.length) setConnection("已连接 · 模型列表不可用", "ready");
  }

  function exportConversation() {
    const conversation = currentConversation();
    if (!conversation || !conversation.messages.length) return;
    const lines = ["# " + (conversation.title || "Chat2API 对话"), "", "模型：" + conversation.model, ""];
    conversation.messages.forEach((message) => {
      lines.push(message.role === "user" ? "## 用户" : "## Chat2API", "");
      const text = citationTextForMessage(message);
      if (text) lines.push(text, "");
      const references = referencesForMessage(message);
      if (references.length) {
        lines.push("引用来源：", ...references.map((item,index) =>
          "[" + (index + 1) + "] " + item.title + " — " + item.url
        ), "");
      }
      messageImageIds(message).forEach((fileId) => lines.push("[图片附件: " + fileId + "]", ""));
      messageFileAttachments(message).forEach((file) => lines.push("[文件附件: " + file.filename + "]", ""));
    });
    const blob = new Blob([lines.join("\n")], {type:"text/markdown;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = (conversation.title || "chat2api-chat").replace(/[\\/:*?"<>|]/g, "_").slice(0, 60) + ".md";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  function exportConversationJson() {
    const conversation = currentConversation();
    if (!conversation || !conversation.messages.length) return;
    const payload = {
      format: "chat2api.web-chat-conversation",
      version: 1,
      exportedAt: new Date().toISOString(),
      conversation: serverConversationPayload(conversation),
    };
    const blob = new Blob([JSON.stringify(payload)], {
      type: "application/json;charset=utf-8",
    });
    if (blob.size > 520 * 1024) {
      toast("当前会话 JSON 超过 520 KiB 导出限制，请先精简会话内容。");
      return;
    }
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "chat2api-conversation-" +
      conversation.id.replace(/[^A-Za-z0-9_-]/g, "_") + ".json";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function importConversationJson(file) {
    if (!file) return;
    if (file.size > 520 * 1024) throw new Error("JSON 会话文件超过 520 KiB 限制");
    let payload;
    try {
      payload = JSON.parse(await file.text());
    } catch {
      throw new Error("JSON 会话文件格式无效");
    }
    const response = await fetch("/api/user/chat/conversations/import", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type":"application/json", "Accept":"application/json"},
      body: JSON.stringify(payload),
    });
    let result = {};
    try { result = await response.json(); } catch {}
    if (!response.ok || !result.conversation) {
      throw new Error(result.detail || "导入失败，HTTP " + response.status);
    }
    const imported = normalizeConversationRow(result.conversation);
    if (!imported) throw new Error("服务端返回的导入会话无效");
    imported.serverRevision = Number(result.conversation.revision || 0);
    state.conversations = [imported, ...state.conversations.filter((row) => row.id !== imported.id)]
      .slice(0, MAX_CONVERSATIONS);
    state.activeConversationId = imported.id;
    state.searchResults = null;
    state.searchQuery = "";
    $("conversationSearch").value = "";
    state.remoteSyncFingerprints.set(imported.id, JSON.stringify(serverConversationPayload(imported)));
    persistHistorySnapshot();
    renderAll();
    toast("会话已导入。附件按原 file ID 引用，需在当前账户中仍可访问。");
  }

  async function bootstrap() {
    try {
      const payload = await apiJson("/api/user/me");
      state.user = payload.user;
    } catch {
      showOnly("authRequired");
      setConnection("未登录", "error");
      $("composerArea").classList.add("hidden");
      return;
    }
    $("accountName").textContent = state.user.display_name || state.user.email || "Chat2API 用户";
    $("accountAvatar").textContent = String(state.user.display_name || state.user.email || "U").trim().slice(0,1).toUpperCase();
    state.storageKey = historyKey(state.user.user_id);
    state.conversations = loadHistory();
    if (state.conversations.length) state.activeConversationId = state.conversations[0].id;
    try {
      await syncServerConversations();
    } catch (error) {
      state.remoteSyncReady = false;
      toast("暂时无法同步服务端会话，当前先显示本地历史：" + error.message);
    }
    renderAll();
    const ready = await loadKeys();
    resumePendingMessages();
    void hydrateNativeSnapshots();
    if (!ready) return;
    if (currentConversation()) state.selectedModel = currentConversation().model || state.selectedModel;
    renderAll();
  }

  if (window.Chat2APIImageDropV1) {
    state.destroyImageDrop = window.Chat2APIImageDropV1.install(
      $("composerArea"),
      document,
      {
        onFiles: (files) => handleImageFiles(files),
        onActive: (active) => $("composerArea").classList.toggle("drag-over", active),
      },
    );
  }

  $("voiceStartButton").addEventListener("click", () => { void startVoiceCall(); });
  $("voiceStopButton").addEventListener("click", () => { void stopVoiceCall(); });
  $("voiceCameraButton").addEventListener("click", () => { void toggleVoiceCamera(); });
  $("voiceClearButton").addEventListener("click", () => {
    $("voiceTranscript").replaceChildren();
    state.voiceAssistantLine = null;
  });

  $("newChatButton").addEventListener("click", createConversation);
  $("conversationSearch").addEventListener("input", onConversationSearchInput);
  $("exportJsonButton").addEventListener("click", exportConversationJson);
  $("importButton").addEventListener("click", () => $("conversationImportInput").click());
  $("conversationImportInput").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    try {
      await importConversationJson(file);
    } catch (error) {
      toast("导入失败：" + error.message);
    } finally {
      event.target.value = "";
    }
  });
  $("apiKeySelect").addEventListener("change", (event) => {
    const conversation = currentConversation();
    if ((state.pendingImages.length || state.pendingFiles.length) && conversation?.apiKeyId && conversation.apiKeyId !== event.target.value) {
      event.target.value = conversation.apiKeyId;
      toast("待发送附件已绑定当前 API Key；请先移除附件再切换 Key。");
      return;
    }
    if (state.voiceActive) {
      void stopVoiceCall().then(() => selectApiKey(event.target.value));
      return;
    }
    selectApiKey(event.target.value);
  });
  $("modelSelect").addEventListener("change", (event) => {
    state.selectedModel = event.target.value;
    const conversation = currentConversation();
    if (conversation) {
      conversation.model = state.selectedModel;
      conversation.updatedAt = new Date().toISOString();
      saveHistory();
    }
  });
  $("sendButton").addEventListener("click", () => sendMessage($("messageInput").value));
  $("attachButton").addEventListener("click", () => $("imageInput").click());
  $("fileButton").addEventListener("click", () => $("fileInput").click());
  $("fileInput").addEventListener("change", async (event) => {
    await handleDocumentFiles(event.target.files);
    event.target.value = "";
  });
  $("imageInput").addEventListener("change", async (event) => {
    await handleImageFiles(event.target.files);
    event.target.value = "";
  });
  $("stopButton").addEventListener("click", () => {
    const controller = state.recoveryController;
    const record = state.activeRequestRecord;
    const abortGenerationTransport = () => {
      if (state.abortController) state.abortController.abort();
    };
    if (controller && record) {
      // Complete the same-origin cancel handshake before aborting the SSE POST.
      // This lets the server finalize a queued cancellation or deliver chat.cancel
      // after the initial Worker dispatch, rather than racing the two requests.
      void controller.cancel(record).then((result) => {
        if (result?.queued && result?.terminal) toast("请求已在 Worker 分配前取消。");
        else if (result?.queued && result?.cancel_requested) toast("已取消等待 Worker 分配的请求。");
        else if (result?.cancel_requested) toast("已向 Worker 发送停止请求。");
        else if (result?.terminal) toast("请求已进入终态。");
        else toast("无法确认停止状态，将继续核对服务端请求。");
        abortGenerationTransport();
      }).catch(() => {
        toast("停止请求未确认；已保留请求恢复记录。");
        abortGenerationTransport();
      });
      return;
    }
    abortGenerationTransport();
  });
  $("messageInput").addEventListener("input", resizeComposer);
  $("messageInput").addEventListener("paste", (event) => {
    const images = Array.from(event.clipboardData?.items || [])
      .filter((item) => item.kind === "file" && ALLOWED_IMAGE_TYPES.has(item.type))
      .map((item) => item.getAsFile())
      .filter(Boolean);
    if (images.length) {
      event.preventDefault();
      handleImageFiles(images);
    }
  });
  $("messageInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!state.streaming) sendMessage($("messageInput").value);
    }
  });
  $("exportButton").addEventListener("click", exportConversation);
  $("themeToggle").addEventListener("click", () => {
    const root = document.documentElement;
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("chat2api.webchat.theme", next); } catch {}
  });
  $("openSidebar").addEventListener("click", () => document.body.classList.add("sidebar-open"));
  $("closeSidebar").addEventListener("click", () => document.body.classList.remove("sidebar-open"));
  $("messages").addEventListener("click", (event) => {
    if (event.target.closest("a")) return;
  });
  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      $("messageInput").value = button.dataset.prompt || "";
      resizeComposer();
      $("messageInput").focus();
    });
  });

  try {
    const theme = localStorage.getItem("chat2api.webchat.theme");
    if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  } catch {}
  window.addEventListener("beforeunload", () => {
    if (state.voiceAuthTimer !== null) window.clearInterval(state.voiceAuthTimer);
    if (state.voiceClient) void state.voiceClient.stop();
    if (state.searchTimer) window.clearTimeout(state.searchTimer);
    if (state.searchController) state.searchController.abort();
    state.nativeStreams.forEach(controller => controller.abort());
    if (typeof state.destroyImageDrop === "function") state.destroyImageDrop();
    state.pendingImages.forEach((image) => {
      if (image.previewUrl) URL.revokeObjectURL(image.previewUrl);
    });
  });
  renderPendingImages();
  renderPendingFiles();
  resizeComposer();
  bootstrap().catch((error) => {
    setConnection("初始化失败", "error");
    toast("聊天客户端初始化失败：" + error.message);
  });
})();
