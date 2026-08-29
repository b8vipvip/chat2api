(() => {
  const root = document.getElementById("view-playground");
  if (!root || document.getElementById("playgroundChatPanel")) return;

  const STORAGE_KEY = "chat2api.playground.chat.v1";
  const MAX_HISTORY_MESSAGES = 40;
  let sending = false;
  let messages = [];
  let lastRequest = null;

  const style = document.createElement("style");
  style.dataset.chat2apiPlaygroundChat = "1";
  style.textContent = `
    .pgChatPanel{margin-bottom:14px;overflow:hidden}
    .pgChatHead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px}
    .pgChatHead h2{margin:0 0 4px}
    .pgChatToolbar{display:grid;grid-template-columns:minmax(180px,.8fr) 150px minmax(260px,1.2fr) minmax(220px,1fr);gap:9px;margin-bottom:12px}
    .pgChatToolbar label{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}
    .pgChatKeyBox{display:grid;grid-template-columns:1fr;gap:7px}
    .pgChatMessages{height:min(52vh,560px);min-height:360px;overflow:auto;border:1px solid var(--line);border-radius:13px;background:#08111f;padding:18px;display:flex;flex-direction:column;gap:14px}
    .pgChatEmpty{margin:auto;color:var(--muted);text-align:center;max-width:520px}
    .pgChatRow{display:flex;flex-direction:column;max-width:min(86%,920px);gap:5px}
    .pgChatRow.user{align-self:flex-end;align-items:flex-end}.pgChatRow.assistant{align-self:flex-start;align-items:flex-start}
    .pgChatRole{font-size:11px;color:var(--muted)}
    .pgChatBubble{border:1px solid var(--line);border-radius:14px;padding:11px 13px;white-space:pre-wrap;word-break:break-word;line-height:1.65;background:#111d31;min-width:52px}
    .pgChatRow.user .pgChatBubble{background:#17375e;border-color:#28517f}
    .pgChatBubble.pending{color:var(--muted)}.pgChatBubble.error{border-color:#70323c;background:#351b22;color:#ffd5d9}
    .pgChatMeta{font-size:11px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}
    .pgChatAttachment{font-size:11px;color:#adc7eb;border:1px solid #294363;background:#0d1b2e;border-radius:999px;padding:2px 8px}
    .pgChatComposer{display:grid;grid-template-columns:1fr auto;gap:9px;margin-top:10px;align-items:end}
    .pgChatComposer textarea{width:100%;min-height:76px;max-height:220px;resize:vertical;line-height:1.55}
    .pgChatComposer button{height:42px;min-width:94px}
    .pgChatAttachHint{font-size:11px;color:var(--muted);margin-top:6px;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
    .pgChatBadge{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:2px 8px;background:#0d1728}
    @media(max-width:1180px){.pgChatToolbar{grid-template-columns:1fr 1fr}.pgChatMessages{height:480px}}
    @media(max-width:760px){.pgChatToolbar{grid-template-columns:1fr}.pgChatMessages{height:420px;min-height:300px;padding:12px}.pgChatRow{max-width:94%}.pgChatComposer{grid-template-columns:1fr}.pgChatComposer button{width:100%}}
  `;
  document.head.appendChild(style);

  const panel = document.createElement("div");
  panel.id = "playgroundChatPanel";
  panel.className = "panel pgChatPanel";
  panel.innerHTML = `
    <div class="pgChatHead">
      <div><h2>聊天对话</h2><div class="muted">直接通过当前 chat2api API 与模型进行多轮对话。自动测试仍使用随机提示词；这里严格发送你输入的原文。</div></div>
      <button class="action" id="pgChatNew">新建对话</button>
    </div>
    <div class="pgChatToolbar">
      <div><label>模型</label><select id="pgChatModel"><option value="gpt-5.6-sol">gpt-5.6-sol</option><option value="gpt-5.5-mini">gpt-5.5-mini</option></select></div>
      <div><label>推理强度</label><select id="pgChatReasoning"><option value="low">低</option><option value="medium" selected>中</option><option value="high">高</option></select></div>
      <div><label>业务 API Key</label><div class="pgChatKeyBox"><select id="pgChatKey"><option value="">正在读取可用业务 Key…</option></select><input id="pgChatKeyInput" type="password" autocomplete="off" placeholder="粘贴业务 API Key（可选，优先使用）"></div></div>
      <div><label>本轮附件（最多 4 个）</label><input id="pgChatFiles" type="file" multiple></div>
    </div>
    <div class="pgChatMessages" id="pgChatMessages"><div class="pgChatEmpty">输入消息即可开始。支持连续追问；后续请求会携带本窗口中的文本对话历史。</div></div>
    <div class="pgChatComposer"><textarea id="pgChatInput" placeholder="输入测试消息。Enter 发送，Shift+Enter 换行。"></textarea><button class="action good" id="pgChatSend">发送</button></div>
    <div class="pgChatAttachHint"><span>附件只绑定当前一轮请求；模型回答会保留在后续文本上下文中。每一轮都会写入下方测试记录。</span><span id="pgChatLast">尚未发送</span></div>
  `;
  root.insertBefore(panel, root.firstElementChild);

  const $chat = id => document.getElementById(id);
  const escHtml = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));

  function saveHistory() {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-MAX_HISTORY_MESSAGES))); } catch (_) {}
  }

  function loadHistory() {
    try {
      const value = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
      if (Array.isArray(value)) {
        messages = value
          .filter(item => item && ["user", "assistant"].includes(item.role) && typeof item.content === "string")
          .map(item => ({
            ...item,
            attachment_names: Array.isArray(item.attachment_names) ? item.attachment_names.map(String).slice(0, 4) : [],
            include_in_context: item.include_in_context !== false,
          }))
          .slice(-MAX_HISTORY_MESSAGES);
      }
    } catch (_) { messages = []; }
  }

  function renderMessages() {
    const box = $chat("pgChatMessages");
    if (!messages.length) {
      box.innerHTML = '<div class="pgChatEmpty">输入消息即可开始。支持连续追问；后续请求会携带本窗口中的文本对话历史。</div>';
      return;
    }
    box.innerHTML = messages.map((item, index) => {
      const attachments = (item.attachment_names || []).map(name => `<span class="pgChatAttachment">附件 · ${escHtml(name)}</span>`).join("");
      const errorClass = item.error ? " error" : "";
      return `
      <div class="pgChatRow ${item.role}" data-chat-index="${index}">
        <div class="pgChatRole">${item.role === "user" ? "你" : "模型"}</div>
        <div class="pgChatBubble${errorClass}">${escHtml(item.content)}</div>
        ${(attachments || item.meta) ? `<div class="pgChatMeta">${attachments}${item.meta ? `<span>${escHtml(item.meta.request_id || "")}</span><span>${escHtml(item.meta.model || "")}</span><span>${item.meta.total_ms != null ? `${Math.round(item.meta.total_ms)} ms` : ""}</span>` : ""}</div>` : ""}
      </div>`;
    }).join("");
    box.scrollTop = box.scrollHeight;
  }

  function addPendingAssistant() {
    const box = $chat("pgChatMessages");
    const row = document.createElement("div");
    row.className = "pgChatRow assistant";
    row.id = "pgChatPending";
    row.innerHTML = '<div class="pgChatRole">模型</div><div class="pgChatBubble pending">正在生成…</div><div class="pgChatMeta" id="pgChatPendingMeta"></div>';
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
    return row.querySelector(".pgChatBubble");
  }

  async function syncModels() {
    const source = document.getElementById("testModel");
    const target = $chat("pgChatModel");
    if (!source || !target || !source.options.length) return;
    const current = target.value;
    const options = [...source.options].map(option => `<option value="${escHtml(option.value)}">${escHtml(option.textContent || option.value)}</option>`).join("");
    if (options) target.innerHTML = options;
    if ([...target.options].some(option => option.value === current)) target.value = current;
    if (!target.value && target.options.length) target.selectedIndex = 0;
    updateReasoningState();
  }

  async function syncKeys() {
    if (typeof api !== "function") return;
    const target = $chat("pgChatKey");
    const current = target.value;
    try {
      const data = await api("/api/admin/keys");
      const options = ['<option value="">请选择可用业务 API Key</option>'];
      let firstUsable = "";
      for (const item of data.data || []) {
        if (!item.managed) continue;
        const usable = item.enabled && !item.expired && !item.revoked_at && item.secret_recoverable;
        const state = item.revoked_at ? "已撤销" : item.expired ? "已过期" : !item.enabled ? "已停用" : !item.secret_recoverable ? "旧 Key 无法恢复" : "可用";
        if (usable && !firstUsable) firstUsable = String(item.key_id || "");
        options.push(`<option value="${escHtml(item.key_id)}" data-key-name="${escHtml(item.name || item.key_id)}" ${usable ? "" : "disabled"}>${escHtml(item.name)} · ${escHtml(item.prefix)} · ${escHtml(state)}</option>`);
      }
      target.innerHTML = options.join("");
      if (current && [...target.options].some(option => option.value === current && !option.disabled)) target.value = current;
      else if (firstUsable) target.value = firstUsable;
    } catch (_) {}
  }

  function updateReasoningState() {
    const mini = $chat("pgChatModel").value === "gpt-5.5-mini";
    $chat("pgChatReasoning").disabled = mini;
    $chat("pgChatReasoning").title = mini ? "gpt-5.5-mini 使用自动推理强度" : "";
  }

  async function resolveCredential() {
    const pasted = $chat("pgChatKeyInput").value.trim();
    if (pasted) return {token: pasted, label: "手动粘贴", key_id: null};
    const selected = $chat("pgChatKey").value;
    if (!selected) throw new Error("请选择一个可用业务 API Key，或手动粘贴业务 API Key");
    const option = $chat("pgChatKey").selectedOptions?.[0];
    const data = await api(`/api/admin/keys/${encodeURIComponent(selected)}/secret`);
    if (!data.token) throw new Error("无法读取所选业务 API Key");
    return {token: data.token, label: option?.dataset?.keyName || selected, key_id: selected};
  }

  async function uploadFiles(files, token) {
    const uploaded = [];
    for (const file of files.slice(0, 4)) {
      const dataBase64 = await fileToBase64(file);
      const value = await api("/v1/files", {
        method: "POST",
        key: token,
        body: {filename: file.name, mime_type: file.type || "application/octet-stream", data_base64: dataBase64, purpose: "chat2api"},
      });
      uploaded.push({file_id: value.id, filename: file.name});
    }
    return uploaded;
  }

  async function cleanupFiles(uploaded, token) {
    for (const item of uploaded) {
      try { await api(`/v1/files/${encodeURIComponent(item.file_id)}`, {method: "DELETE", key: token}); } catch (_) {}
    }
  }

  function normalizedHistory() {
    return messages
      .filter(item => item.include_in_context !== false)
      .slice(-MAX_HISTORY_MESSAGES)
      .map(item => ({role: item.role, content: item.content}));
  }

  async function recordChatTurn({requestId, model, credential, text, statusValue, totalMs, firstTokenMs, responseChars, attachmentsCount, error}) {
    try {
      await api("/api/admin/playground/chat-records", {
        method: "POST",
        body: {
          request_id: requestId,
          model,
          api_key_id: credential?.key_id || null,
          api_key_name: credential?.label || ($chat("pgChatKey").selectedOptions?.[0]?.dataset?.keyName || "未解析"),
          status: statusValue,
          duration_ms: totalMs,
          first_token_ms: firstTokenMs,
          prompt: text,
          response_chars: responseChars,
          attachments_count: attachmentsCount,
          error: error || null,
        },
      });
      if (typeof loadTests === "function") await loadTests();
    } catch (recordError) {
      console.warn("Playground chat record persistence failed", recordError);
    }
  }

  async function sendMessage() {
    if (sending) return;
    const input = $chat("pgChatInput");
    const text = input.value.trim();
    if (!text) return;
    if (typeof api !== "function" || typeof fileToBase64 !== "function") return status("测试场脚本尚未初始化，请刷新页面后重试", "bad");

    sending = true;
    $chat("pgChatSend").disabled = true;
    $chat("pgChatSend").textContent = "生成中";
    input.disabled = true;
    let uploaded = [];
    let credential = null;
    let pendingBubble = null;
    const userFiles = [...$chat("pgChatFiles").files].slice(0, 4);
    const userIndex = messages.length;
    messages.push({role: "user", content: text, attachment_names: userFiles.map(file => file.name), include_in_context: true});
    input.value = "";
    renderMessages();
    pendingBubble = addPendingAssistant();

    const requestId = "req_" + (globalThis.crypto?.randomUUID ? crypto.randomUUID().replaceAll("-", "") : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`);
    const model = $chat("pgChatModel").value || "gpt-5.6-sol";
    const started = performance.now();
    let assistantText = "";
    let firstTokenMs = null;
    let diagnostics = null;
    let rawError = "";

    try {
      credential = await resolveCredential();
      uploaded = await uploadFiles(userFiles, credential.token);
      const body = {
        model,
        messages: normalizedHistory(),
        prompt_mode: "full",
        attachments: uploaded.map(item => ({file_id: item.file_id})),
        stream: true,
        timeout: 300,
      };
      if (model !== "gpt-5.5-mini") body.reasoning_effort = $chat("pgChatReasoning").value || "medium";

      const response = await fetch("/v1/chat/completions", {
        method: "POST",
        headers: {...headers(credential.token), "Content-Type": "application/json", "X-Chat2API-Request-ID": requestId},
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true}).replace(/\r\n/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          for (const line of block.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            const payload = line.slice(6);
            if (!payload || payload === "[DONE]") continue;
            let event;
            try { event = JSON.parse(payload); } catch (_) { continue; }
            if (event.error) {
              rawError = String(event.error.message || "stream error");
              if (event.chat2api) diagnostics = event.chat2api;
              continue;
            }
            const content = String(event.choices?.[0]?.delta?.content || "");
            if (content) {
              if (firstTokenMs === null) firstTokenMs = performance.now() - started;
              assistantText += content;
              pendingBubble.className = "pgChatBubble";
              pendingBubble.textContent = assistantText;
              const box = $chat("pgChatMessages");
              box.scrollTop = box.scrollHeight;
            }
            if (event.chat2api) diagnostics = event.chat2api;
          }
        }
      }
      if (rawError) throw new Error(rawError);
      if (!assistantText.trim()) throw new Error("请求完成，但没有捕获到模型文本输出");

      const totalMs = performance.now() - started;
      document.getElementById("pgChatPending")?.remove();
      messages.push({
        role: "assistant",
        content: assistantText,
        include_in_context: true,
        meta: {request_id: requestId, model, total_ms: totalMs, first_token_ms: firstTokenMs},
      });
      lastRequest = {request_id: requestId, model, total_ms: totalMs, first_token_ms: firstTokenMs, diagnostics};
      saveHistory();
      renderMessages();
      $chat("pgChatLast").innerHTML = `<span class="pgChatBadge">${escHtml(requestId)}</span> <span class="pgChatBadge">${Math.round(totalMs)} ms</span>`;
      status(`聊天请求完成：${requestId}`, "ok");
      $chat("pgChatFiles").value = "";
      await recordChatTurn({
        requestId, model, credential, text, statusValue: "passed", totalMs, firstTokenMs,
        responseChars: assistantText.length, attachmentsCount: userFiles.length, error: null,
      });
    } catch (error) {
      const totalMs = performance.now() - started;
      const errorText = String(error?.message || error);
      const failedStatus = /(stalled|watchdog|timed out|timeout|no observable|no response progress)/i.test(errorText) ? "stalled" : "failed";
      document.getElementById("pgChatPending")?.remove();
      if (messages[userIndex]) messages[userIndex].include_in_context = false;
      messages.push({
        role: "assistant",
        content: `请求失败：${errorText}`,
        include_in_context: false,
        error: true,
        meta: {request_id: requestId, model, total_ms: totalMs},
      });
      lastRequest = {request_id: requestId, model, total_ms: totalMs, error: errorText, diagnostics};
      saveHistory();
      renderMessages();
      status(`聊天请求失败：${errorText}`, "bad");
      await recordChatTurn({
        requestId, model, credential, text, statusValue: failedStatus, totalMs, firstTokenMs,
        responseChars: assistantText.length, attachmentsCount: userFiles.length, error: errorText,
      });
    } finally {
      if (credential && uploaded.length) await cleanupFiles(uploaded, credential.token);
      sending = false;
      $chat("pgChatSend").disabled = false;
      $chat("pgChatSend").textContent = "发送";
      input.disabled = false;
      input.focus();
    }
  }

  async function newConversation() {
    if (sending && !confirm("当前请求仍在生成。只清空本地显示不会取消服务器请求，仍要继续吗？")) return;
    messages = [];
    lastRequest = null;
    saveHistory();
    renderMessages();
    $chat("pgChatFiles").value = "";
    $chat("pgChatLast").textContent = "新对话已创建";
    $chat("pgChatInput").focus();
  }

  $chat("pgChatSend").addEventListener("click", sendMessage);
  $chat("pgChatNew").addEventListener("click", newConversation);
  $chat("pgChatInput").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  $chat("pgChatModel").addEventListener("change", updateReasoningState);
  $chat("pgChatModel").addEventListener("focus", syncModels);
  $chat("pgChatKey").addEventListener("focus", syncKeys);

  loadHistory();
  renderMessages();
  updateReasoningState();
  setTimeout(syncModels, 100);
  setTimeout(syncKeys, 180);
  setTimeout(syncModels, 900);
})();