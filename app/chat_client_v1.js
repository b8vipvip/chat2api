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
    pendingImages: [],
    pendingImagesConversationId: "",
    toastTimer: null,
  };
  const MAX_CONVERSATIONS = 15;
  const MAX_MESSAGES = 30;
  const MAX_MESSAGE_CHARS = 8000;
  const MAX_IMAGES_PER_MESSAGE = 4;
  const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
  const MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024;
  const ALLOWED_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
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

  function normalizeMessageContent(content, role) {
    if (typeof content === "string") return content.slice(0, MAX_MESSAGE_CHARS);
    if (role !== "user" || !Array.isArray(content)) return "";
    const parts = [];
    let imageCount = 0;
    for (const part of content) {
      if (part?.type === "text" && typeof part.text === "string") {
        parts.push({type:"text",text:part.text.slice(0,MAX_MESSAGE_CHARS)});
      } else if (part?.type === "image_url" && imageCount < MAX_IMAGES_PER_MESSAGE) {
        const fileId = imageFileId(part);
        if (fileId) {
          parts.push({type:"image_url",image_url:{url:fileId}});
          imageCount += 1;
        }
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

  function loadHistory() {
    try {
      const raw = localStorage.getItem(state.storageKey);
      const rows = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(rows)) return [];
      return rows.filter((row) => row && typeof row === "object" && typeof row.id === "string")
        .slice(0, MAX_CONVERSATIONS)
        .map((row) => {
          const messages = Array.isArray(row.messages) ? row.messages.slice(-MAX_MESSAGES).map((message) => {
            if (!message || (message.role !== "user" && message.role !== "assistant")) return null;
            const content = normalizeMessageContent(message.content, message.role);
            if (!content || (message.role === "assistant" && !String(content).trim())) return null;
            return {
              id: String(message.id || id()),
              role: message.role,
              content,
              createdAt: String(message.createdAt || ""),
              requestId: typeof message.requestId === "string" ? message.requestId.slice(0, 140) : "",
              pending: message.pending === true,
              recoveryStatus: typeof message.recoveryStatus === "string" ? message.recoveryStatus.slice(0, 40) : "",
            };
          }).filter(Boolean) : [];
          const hasSavedImages = messages.some((message) => messageImageIds(message).length);
          return {
            id: row.id,
            title: String(row.title || "新对话").slice(0, 80),
            model: String(row.model || state.selectedModel).slice(0, 120),
            apiKeyId: hasSavedImages && typeof row.apiKeyId === "string" ? row.apiKeyId.slice(0, 120) : "",
            createdAt: String(row.createdAt || new Date().toISOString()),
            updatedAt: String(row.updatedAt || row.createdAt || new Date().toISOString()),
            messages,
          };
        });
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
        content: normalizeMessageContent(message.content, message.role),
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
    if (state.pendingImages.length) {
      toast("请先发送或移除待发送图片，再新建对话。");
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
    if (state.streaming) {
      toast("当前回复仍在生成；请先停止生成再切换对话。");
      return;
    }
    if (state.pendingImages.length && state.pendingImagesConversationId !== conversationId) {
      toast("请先发送或移除待发送图片，再切换对话。");
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
    if (state.pendingImages.length && state.pendingImagesConversationId === conversationId) {
      toast("请先移除待发送图片，再删除对话。");
      return;
    }
    if (!window.confirm("删除这段本地对话？本地记录会删除，且仅清理未被其他本地对话引用的图片附件。")) return;
    const remainingFileIds = new Set(
      state.conversations
        .filter((row) => row.id !== conversationId)
        .flatMap((row) => row.messages.flatMap(messageImageIds))
    );
    const orphanedFileIds = [...new Set(conversation.messages.flatMap(messageImageIds))]
      .filter((fileId) => !remainingFileIds.has(fileId));
    state.conversations = state.conversations.filter((row) => row.id !== conversationId);
    if (state.activeConversationId === conversationId) {
      state.activeConversationId = state.conversations[0]?.id || "";
    }
    saveHistory();
    renderAll();
    if (orphanedFileIds.length) {
      Promise.allSettled(orphanedFileIds.map((fileId) =>
        apiJson("/api/user/files/" + encodeURIComponent(fileId), {method:"DELETE"})
      )).then((results) => {
        const failed = results.filter((result) => result.status === "rejected").length;
        if (failed) toast("本地对话已删除；" + failed + " 个图片文件未能自动清理。");
      });
    }
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
        conversation.messages.some((message) => messageText(message).toLocaleLowerCase().includes(query));
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
      if (message.role === "user") {
        const text = messageText(message);
        if (text) {
          const textNode = document.createElement("div");
          textNode.className = "message-user-text";
          textNode.textContent = text;
          body.appendChild(textNode);
        }
        const imageIds = messageImageIds(message);
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
        const renderer = window.Chat2APISafeRenderer;
        if (renderer && typeof renderer.renderMarkdown === "function") {
          renderer.renderMarkdown(body, message.content);
        } else {
          body.textContent = message.content;
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
          const copied = [messageText(message), ...messageImageIds(message).map(() => "[图片附件]")].filter(Boolean).join("\n");
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
  }

  function autoTitle(conversation) {
    if (!conversation || conversation.title !== "新对话") return;
    const first = conversation.messages.find((message) => message.role === "user");
    if (first) conversation.title = messageText(first).replace(/\s+/g, " ").trim().slice(0, 32) || (messageImageIds(first).length ? "图片附件" : "新对话");
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
    const pendingForConversation = state.pendingImages.length > 0 &&
      state.pendingImagesConversationId === conversation?.id;
    const keyMatchesConversation = !conversation?.apiKeyId || conversation.apiKeyId === selectedKeyId();
    const unresolvedRequest = Boolean(conversation?.messages?.some((message) => message.role === "assistant" && message.pending === true));
    $("sendButton").disabled = state.streaming || unresolvedRequest || (!input.value.trim() && !pendingForConversation) ||
      !state.apiToken || !keyMatchesConversation;
    $("attachButton").disabled = state.streaming || !state.apiToken || !selectedKeyHasScope("files");
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
          if (conversation && !conversation.messages.some((message) => messageImageIds(message).length)) {
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

  function readImageAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("读取图片失败"));
      reader.onload = () => resolve(String(reader.result || ""));
      reader.readAsDataURL(file);
    });
  }

  async function uploadPendingImages() {
    for (const image of state.pendingImages) {
      if (image.fileId) continue;
      const dataUrl = await readImageAsDataUrl(image.file);
      const comma = dataUrl.indexOf(",");
      if (comma < 0) throw new Error("图片编码失败");
      const payload = await apiJson("/v1/files", {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + state.apiToken,
          "Content-Type": "application/json",
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
    if (state.pendingImages.length && state.pendingImagesConversationId !== conversation.id) {
      toast("请先发送或移除其他对话的待发送图片。");
      return;
    }
    let totalBytes = state.pendingImages.reduce((sum, image) => sum + image.size, 0);
    for (const file of files) {
      if (state.pendingImages.length >= MAX_IMAGES_PER_MESSAGE) {
        toast("每条消息最多添加 " + MAX_IMAGES_PER_MESSAGE + " 张图片。");
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

  function recoveryStatusLabel(status) {
    const labels = {
      streaming: "生成中", recovering: "恢复中", recovery_pending: "等待服务端确认",
      queued: "排队中", running: "生成中", reconnecting: "重新连接中",
      completed: "已完成", completed_without_text: "已完成，但无可恢复文本",
      cancellation_requested: "正在确认停止", cancelled: "已取消", error: "请求失败",
      stalled: "请求停滞", auth_required: "需要重新登录", not_found: "请求记录不存在",
      recovery_unavailable: "暂不可恢复", recovery_timeout: "恢复超时",
      recovery_failed: "恢复失败", recovery_error: "恢复连接异常",
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
        assistant.recoveryStatus = String(update.status || "recovering").slice(0, 40);
        if (update.status === "completed" && update.terminal === true && typeof update.response_text === "string") {
          assistant.content = update.response_text.slice(0, MAX_MESSAGE_CHARS);
        }
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
      assistant.pending = false;
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
      const result = await controller.resume(record);
      const status = String(result?.status || "recovery_failed");
      if (result?.terminal === true) {
        assistant.pending = false;
        assistant.recoveryStatus = status;
        if (status === "completed" && typeof result.response_text === "string") assistant.content = result.response_text.slice(0, MAX_MESSAGE_CHARS);
      } else if (["recovery_timeout", "polling_stopped", "auth_required"].includes(status)) {
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

  function resumePendingMessages() {
    const pending = [];
    state.conversations.forEach((conversation) => {
      (conversation.messages || []).forEach((message) => {
        if (message.role === "assistant" && message.pending === true && message.requestId) pending.push({conversation,message});
      });
    });
    pending.forEach(({conversation,message}) => { void recoverPendingMessage(conversation,message); });
  }
  async function sendMessage(text) {
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
    const pending = state.pendingImagesConversationId === conversation?.id ? state.pendingImages : []
    if (!clean && !pending.length) return;
    if (!state.apiToken) {
      toast("请先选择一个可用的 API Key。");
      showOnly("keyRequired");
      return;
    }
    const keyId = selectedKeyId();
    if (conversation.apiKeyId && conversation.apiKeyId !== keyId) {
      toast("该对话包含由其他 API Key 上传的图片，请切换回原 API Key。");
      resizeComposer();
      return;
    }
    if (pending.length && !selectedKeyHasScope("files")) {
      toast("当前 API Key 没有 files 权限，无法发送图片。");
      return;
    }
    conversation.model = state.selectedModel;
    state.streaming = true;
    state.abortController = new AbortController();
    $("sendButton").disabled = true;
    $("attachButton").disabled = true;
    $("newChatButton").disabled = true;
    $("apiKeySelect").disabled = true;
    $("modelSelect").disabled = true;
    $("messageInput").disabled = true;
    setConnection(pending.length ? "正在上传图片…" : "正在连接…");

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

      if (pending.length) {
        conversation.apiKeyId = keyId;
        saveHistory();
        await uploadPendingImages();
      }
      const imageParts = pending.map((image) => ({
        type: "image_url",
        image_url: {url:image.fileId},
      }));
      const userContent = imageParts.length
        ? [...(clean ? [{type:"text",text:clean}] : []), ...imageParts]
        : clean;
      userMessage = {id:id(),role:"user",content:userContent,createdAt:new Date().toISOString()};
      conversation.messages.push(userMessage);
      autoTitle(conversation);
      assistant = {id:id(),role:"assistant",content:"",createdAt:new Date().toISOString(),requestId,pending:true,recoveryStatus:"streaming"};
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
      $("messageInput").value = "";
      renderPendingImages();
      state.streamingMessageId = assistant.id;
      $("stopButton").classList.remove("hidden");
      setConnection("已连接", "ready");
      resizeComposer();
      renderAll();

      const messages = conversation.messages
        .filter((message) => message.id !== assistant.id)
        .map((message) => ({role:message.role,content:message.content}));
      requestSubmitted = true;
      const response = await fetch("/v1/chat/completions", {
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
      if (done) {
        assistant.pending = false;
        assistant.recoveryStatus = "completed";
        if (!assistant.content.trim()) {
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
        if (assistant && !assistant.content.trim()) conversation.messages = conversation.messages.filter((message) => message.id !== assistant.id);
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
      const text = messageText(message);
      if (text) lines.push(text, "");
      messageImageIds(message).forEach((fileId) => lines.push("[图片附件: " + fileId + "]", ""));
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
    resumePendingMessages();
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

  $("newChatButton").addEventListener("click", createConversation);
  $("conversationSearch").addEventListener("input", renderSidebar);
  $("apiKeySelect").addEventListener("change", (event) => {
    const conversation = currentConversation();
    if (state.pendingImages.length && conversation?.apiKeyId && conversation.apiKeyId !== event.target.value) {
      event.target.value = conversation.apiKeyId;
      toast("待发送图片已绑定当前 API Key；请先移除图片再切换 Key。");
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
  $("imageInput").addEventListener("change", async (event) => {
    await handleImageFiles(event.target.files);
    event.target.value = "";
  });
  $("stopButton").addEventListener("click", () => {
    const controller = state.recoveryController;
    const record = state.activeRequestRecord;
    if (controller && record) {
      void controller.cancel(record).then((result) => {
        if (result?.cancel_requested) toast("已向 Worker 发送停止请求。");
        else if (result?.terminal) toast("请求已进入终态。");
        else toast("无法确认停止状态，将继续核对服务端请求。");
      }).catch(() => toast("停止请求未确认；已保留请求恢复记录。"));
    }
    if (state.abortController) state.abortController.abort();
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
    if (typeof state.destroyImageDrop === "function") state.destroyImageDrop();
    state.pendingImages.forEach((image) => {
      if (image.previewUrl) URL.revokeObjectURL(image.previewUrl);
    });
  });
  renderPendingImages();
  resizeComposer();
  bootstrap().catch((error) => {
    setConnection("初始化失败", "error");
    toast("聊天客户端初始化失败：" + error.message);
  });
})();
