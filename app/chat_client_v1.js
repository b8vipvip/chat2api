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
    toastTimer: null,
  };
  const MAX_CONVERSATIONS = 15;
  const MAX_MESSAGES = 30;
  const MAX_MESSAGE_CHARS = 8000;

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

  function loadHistory() {
    try {
      const raw = localStorage.getItem(state.storageKey);
      const rows = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(rows)) return [];
      return rows.filter((row) => row && typeof row === "object" && typeof row.id === "string")
        .slice(0, MAX_CONVERSATIONS)
        .map((row) => ({
          id: row.id,
          title: String(row.title || "新对话").slice(0, 80),
          model: String(row.model || state.selectedModel).slice(0, 120),
          createdAt: String(row.createdAt || new Date().toISOString()),
          updatedAt: String(row.updatedAt || row.createdAt || new Date().toISOString()),
          messages: Array.isArray(row.messages) ? row.messages.slice(-MAX_MESSAGES).filter((message) =>
            message && (message.role === "user" || message.role === "assistant") &&
            typeof message.content === "string" && (message.role === "user" || message.content.trim())
          ).map((message) => ({
            id: String(message.id || id()),
            role: message.role,
            content: message.content.slice(0, MAX_MESSAGE_CHARS),
            createdAt: String(message.createdAt || ""),
          })) : [],
        }));
    } catch {
      toast("本地聊天历史无法读取，已使用空白历史。");
      return [];
    }
  }

  function saveHistory() {
    if (!state.storageKey) return;
    state.conversations.sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
    state.conversations = state.conversations.slice(0, MAX_CONVERSATIONS);
    const payload = state.conversations.map((conversation) => ({
      ...conversation,
      messages: (conversation.messages || []).slice(-MAX_MESSAGES).map((message) => ({
        ...message,
        content: String(message.content || "").slice(0, MAX_MESSAGE_CHARS),
      })),
    }));
    try {
      localStorage.setItem(state.storageKey, JSON.stringify(payload));
    } catch {
      toast("浏览器本地存储空间不足；请导出并删除部分旧对话。");
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
    const conversation = {
      id: id(),
      title: "新对话",
      model: state.selectedModel,
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
    if (state.streaming) {
      toast("当前回复仍在生成；请先停止生成再切换对话。");
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
    const conversation = state.conversations.find((row) => row.id === conversationId);
    if (!conversation) return;
    if (state.streaming && conversationId === state.activeConversationId) {
      toast("当前对话正在生成；请先停止生成再删除。");
      return;
    }
    if (!window.confirm("删除这段本地对话？此操作不可撤销。")) return;
    state.conversations = state.conversations.filter((row) => row.id !== conversationId);
    if (state.activeConversationId === conversationId) {
      state.activeConversationId = state.conversations[0]?.id || "";
    }
    saveHistory();
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

  function renderSidebar() {
    const root = $("conversationList");
    root.replaceChildren();
    const query = $("conversationSearch").value.trim().toLocaleLowerCase();
    const rows = state.conversations.filter((conversation) => {
      if (!query) return true;
      return conversation.title.toLocaleLowerCase().includes(query) ||
        conversation.messages.some((message) => message.content.toLocaleLowerCase().includes(query));
    });
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
      open.addEventListener("click", () => setActiveConversation(conversation.id));
      open.addEventListener("dblclick", () => renameConversation(conversation.id));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "conversation-delete";
      remove.textContent = "×";
      remove.title = "删除对话";
      remove.setAttribute("aria-label", "删除对话");
      remove.addEventListener("click", () => deleteConversation(conversation.id));
      item.append(open, remove);
      root.appendChild(item);
    });
  }

  // W-03: the allowlisted renderer owns all assistant Markdown output.

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
      const body = document.createElement("div");
      body.className = "message-body";
      if (message.role === "user") {
        body.textContent = message.content;
      } else {
        if (window.Chat2APISafeRenderer) {
          window.Chat2APISafeRenderer.renderMarkdown(body, message.content);
        } else {
          // Fail closed if the independently served renderer did not load.
          body.textContent = String(message.content || "");
        }
      }
      if (state.streaming && message.id === state.streamingMessageId) {
        const caret = document.createElement("span");
        caret.className = "streaming-caret";
        caret.setAttribute("aria-label", "正在生成");
        body.appendChild(caret);
      }
      const actions = document.createElement("div");
      actions.className = "message-actions";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "复制";
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(message.content); toast("消息已复制"); }
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
  }

  function autoTitle(conversation) {
    if (!conversation || conversation.title !== "新对话") return;
    const first = conversation.messages.find((message) => message.role === "user");
    if (first) conversation.title = first.content.replace(/\s+/g, " ").trim().slice(0, 32) || "新对话";
  }

  function resizeComposer() {
    const input = $("messageInput");
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 220) + "px";
    $("sendButton").disabled = state.streaming || !input.value.trim() || !state.apiToken;
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

  function consumeSseFrame(frame, assistant) {
    const data = frame.split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return false;
    if (data.trim() === "[DONE]") return true;
    let event;
    try { event = JSON.parse(data); } catch { return false; }
    const choice = Array.isArray(event.choices) ? event.choices[0] : null;
    if (choice?.delta && Object.prototype.hasOwnProperty.call(choice.delta, "content")) {
      appendDelta(assistant, choice.delta.content);
    }
    return false;
  }

  async function sendMessage(text) {
    const clean = String(text || "").trim();
    if (!clean || state.streaming) return;
    if (!state.apiToken) {
      toast("请先选择一个可用的 API Key。");
      showOnly("keyRequired");
      return;
    }
    const conversation = currentConversation() || createConversation();
    conversation.model = state.selectedModel;
    const userMessage = {id:id(),role:"user",content:clean,createdAt:new Date().toISOString()};
    conversation.messages.push(userMessage);
    autoTitle(conversation);
    const assistant = {id:id(),role:"assistant",content:"",createdAt:new Date().toISOString()};
    conversation.messages.push(assistant);
    conversation.updatedAt = new Date().toISOString();
    saveHistory();
    state.streaming = true;
    state.streamingMessageId = assistant.id;
    state.abortController = new AbortController();
    $("messageInput").value = "";
    resizeComposer();
    $("stopButton").classList.remove("hidden");
    $("sendButton").disabled = true;
    $("newChatButton").disabled = true;
    $("apiKeySelect").disabled = true;
    $("modelSelect").disabled = true;
    renderAll();

    try {
      const messages = conversation.messages
        .filter((message) => message.id !== assistant.id)
        .map((message) => ({role:message.role,content:message.content}));
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
      const response = await fetch("/v1/chat/completions", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Authorization": "Bearer " + state.apiToken,
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
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

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let done = false;
      let lastSavedLength = 0;
      while (!done) {
        const part = await reader.read();
        if (part.done) break;
        buffer += decoder.decode(part.value, {stream:true});
        buffer = buffer.replace(/\r\n/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (consumeSseFrame(frame, assistant)) {
            done = true;
            break;
          }
        }
        if (assistant.content) {
          conversation.updatedAt = new Date().toISOString();
          if (assistant.content.length - lastSavedLength >= 1000) {
            saveHistory();
            lastSavedLength = assistant.content.length;
          }
          renderMessages();
        }
      }
      if (buffer.trim() && consumeSseFrame(buffer, assistant)) done = true;
      if (!assistant.content.trim()) {
        conversation.messages = conversation.messages.filter((message) => message.id !== assistant.id);
        toast("模型没有返回文本内容。");
      } else if (!done) {
        toast("连接已结束，但未收到流结束标记；请检查请求记录确认是否完整。");
      }
    } catch (error) {
      if (error.name === "AbortError") {
        toast(assistant.content ? "已停止生成，已保留当前回复。" : "已停止生成。");
        if (!assistant.content) conversation.messages = conversation.messages.filter((message) => message.id !== assistant.id);
      } else {
        if (!assistant.content) conversation.messages = conversation.messages.filter((message) => message.id !== assistant.id);
        toast("请求失败：" + error.message);
      }
    } finally {
      state.streaming = false;
      state.streamingMessageId = "";
      state.abortController = null;
      conversation.updatedAt = new Date().toISOString();
      saveHistory();
      if (state.apiToken) renderAll();
      else showOnly(state.user ? "keyRequired" : "authRequired");
      $("stopButton").classList.add("hidden");
      $("newChatButton").disabled = false;
      $("apiKeySelect").disabled = false;
      $("modelSelect").disabled = false;
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
      setConnection("已连接", "ready");
      resizeComposer();
      await loadModels();
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
    const usable = state.keys.filter((key) => key.enabled && !key.expired && !key.revoked_at && key.secret_recoverable && (!Array.isArray(key.scopes) || key.scopes.includes("chat")));
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
    const selected = usable.find((key) => key.key_id === preferred) || usable[0];
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
      lines.push(message.role === "user" ? "## 用户" : "## Chat2API", "", message.content, "");
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
    renderAll();
    const ready = await loadKeys();
    if (!ready) return;
    if (currentConversation()) state.selectedModel = currentConversation().model || state.selectedModel;
    renderAll();
  }

  $("newChatButton").addEventListener("click", createConversation);
  $("conversationSearch").addEventListener("input", renderSidebar);
  $("apiKeySelect").addEventListener("change", (event) => selectApiKey(event.target.value));
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
  $("stopButton").addEventListener("click", () => {
    if (state.abortController) state.abortController.abort();
  });
  $("messageInput").addEventListener("input", resizeComposer);
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
  resizeComposer();
  bootstrap().catch((error) => {
    setConnection("初始化失败", "error");
    toast("聊天客户端初始化失败：" + error.message);
  });
})();
