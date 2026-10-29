(() => {
  const KEY = "__CHAT2API_WORKER_LIMITS_CLIPBOARD_V121__";
  if (globalThis[KEY]) return;

  const SETTINGS_HELP = "并发=Worker 同时执行请求的硬上限；满载时自动增加的备用窗口不增加并发名额。";
  const state = {
    version: 241,
    renderer: "canonical-worker-list-v152",
    login: {workerId:"", ticket:"", sourceWidth:1920, sourceHeight:1080},
    dragging: false,
    lastMoveAt: 0,
  };
  globalThis[KEY] = state;

  async function jsonRequest(path, options = {}) {
    const {headers = {}, body, ...rest} = options;
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...rest,
      headers: {"Content-Type":"application/json", ...headers},
      body: body === undefined ? undefined : (typeof body === "string" ? body : JSON.stringify(body)),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
    return payload;
  }

  function status(text, tone = "") {
    const node = document.getElementById("linuxLoginStatus");
    if (!node) return;
    node.textContent = text;
    node.style.color = tone === "bad" ? "#fca5a5" : tone === "ok" ? "#86efac" : "#94a3b8";
  }

  // Worker-list HTML is owned exclusively by admin_extension_columns.js. v121
  // keeps only edit/save and remote-login clipboard behavior; it never fetches
  // /api/admin/extensions and never rewrites Worker rows after first paint.
  async function renderLimits() {
    document.documentElement.dataset.chat2apiWorkerLimitsRevision = "121-behavior-only";
    return null;
  }


  // v241: one inline editor in whichever Worker row the user clicked.
  // The canonical Windows and Linux renderers supply the SAME minimal markup.
  // This module alone handles edit/save; it never owns a second window quota.
  function closeLimitPopovers(except = null) {
    document.querySelectorAll('[data-v121-inline-editing="1"]').forEach(editor => {
      if (editor !== except && !editor.querySelector('[data-v121-save-limits]')?.disabled)
        restoreLimitEditor(editor);
    });
  }

  function restoreLimitEditor(editor) {
    editor.querySelector('[data-v121-inline-input]')?.remove();
    editor.querySelector('[data-v121-save-limits]')?.remove();
    editor.querySelector('[data-v121-limit-note]')?.remove();
    const summary = editor.querySelector('[data-v121-limit-summary]');
    const edit = editor.querySelector('[data-v121-edit-limits]');
    if (summary) summary.hidden = false;
    if (edit) edit.hidden = false;
    delete editor.dataset.v121InlineEditing;
  }

  function toggleLimitPopover(button) {
    const editor = button.closest('[data-v121-worker-limits]');
    if (!editor || editor.dataset.v121InlineEditing === '1') return;
    closeLimitPopovers(editor);
    const summary = editor.querySelector('[data-v121-limit-summary]');
    const value = Number(summary?.textContent || 3);
    const input = document.createElement('input');
    input.type = 'number';
    input.min = '1'; input.max = '32'; input.step = '1';
    input.value = String(Number.isInteger(value) && value >= 1 && value <= 32 ? value : 3);
    input.dataset.v121InlineInput = '1';
    input.setAttribute('aria-label', '最大并发请求数');
    input.style.cssText = 'width:65px;box-sizing:border-box;padding:5px 6px;border:1px solid #475569;border-radius:6px;background:#0b1220;color:inherit';
    const save = document.createElement('button');
    save.type = 'button';
    save.className = 'action good';
    save.dataset.v121SaveLimits = '1';
    save.textContent = '保存';
    const note = document.createElement('span');
    note.dataset.v121LimitNote = '1';
    note.setAttribute('role', 'status');
    note.style.cssText = 'font-size:11px;color:#fca5a5;white-space:normal;max-width:160px';
    if (summary) summary.hidden = true;
    button.hidden = true;
    editor.dataset.v121InlineEditing = '1';
    editor.append(input,save,note);
    input.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); restoreLimitEditor(editor); }
      if (event.key === 'Enter') { event.preventDefault(); if (!save.disabled) void saveLimits(save); }
    });
    input.focus();
  }

  async function saveLimits(button) {
    const editor = button.closest('[data-v121-worker-limits]');
    if (!editor || button.disabled || editor.dataset.v121InlineEditing !== '1') return;
    const clientId = String(editor.dataset.v121WorkerLimits || '');
    const raw = String(editor.querySelector('[data-v121-inline-input]')?.value || '').trim();
    const note = editor.querySelector('[data-v121-limit-note]');
    if (!clientId || !/^(?:[1-9]|[12][0-9]|3[0-2])$/.test(raw)) {
      if (note) note.textContent = '输入 1～32 的整数';
      return;
    }
    const concurrency = Number(raw);
    button.disabled = true;
    button.textContent = '保存中…';
    if (note) note.textContent = '';
    try {
      const saved = await jsonRequest(`/api/admin/extensions/${encodeURIComponent(clientId)}/capacity-v57`, {
        method:'PUT', body:{max_concurrency:concurrency},
      });
      if (saved?.ok !== true || Number(saved?.max_concurrency) !== concurrency)
        throw new Error('服务端未确认保存值');
      // Update both the hidden source row and visible unified row. No false
      // success on errors; only the single v58 scheduler owns this setting.
      document.querySelectorAll('[data-v121-worker-limits]').forEach(row => {
        if (row.dataset.v121WorkerLimits !== clientId) return;
        const summary = row.querySelector('[data-v121-limit-summary]');
        if (summary) summary.textContent = String(concurrency);
        if (row.dataset.v121InlineEditing === '1') restoreLimitEditor(row);
      });
      const reload = globalThis.chat2apiReloadCanonicalWorkerListV59;
      if (typeof reload === 'function') {
        try { await reload(); } catch (_) { /* saved configuration remains authoritative */ }
      }
    } catch (error) {
      if (note) note.textContent = '保存失败：' + String(error?.message || error);
      button.textContent = '重试保存';
    } finally {
      button.disabled = false;
      if (editor.isConnected && editor.dataset.v121InlineEditing === '1' && button.textContent === '保存中…')
        button.textContent = '保存';
    }
  }

  function loginEndpoint(workerId) {
    return `/api/admin/linux-workers/${encodeURIComponent(workerId)}/login-session/clipboard`;
  }

  async function clipboardCommand(action, text = undefined) {
    const session = state.login;
    if (!session.workerId || !session.ticket) throw new Error("远程登录会话尚未就绪");
    const body = action === "paste" ? {action, text:String(text ?? "")} : {action};
    return jsonRequest(loginEndpoint(session.workerId), {
      method:"POST",
      headers:{"X-Chat2API-Login-Ticket":session.ticket},
      body,
    });
  }

  async function pasteRemote(text) {
    text = String(text ?? "").replace(/\x00/g, "");
    if (!text) return;
    if (text.length > 16384) throw new Error("粘贴文本过长，最多 16384 个字符");
    status("正在粘贴到远程 Chrome…");
    const result = await clipboardCommand("paste", text);
    status(`已粘贴 ${Number(result?.characters || text.length)} 个字符（支持中文）`, "ok");
  }

  function fallbackCopyBuffer(text) {
    let node = document.getElementById("linuxLoginCopyBufferV121");
    if (!node) {
      node = document.createElement("textarea");
      node.id = "linuxLoginCopyBufferV121";
      node.readOnly = true;
      node.style.cssText = "box-sizing:border-box;width:100%;height:70px;margin-top:8px;padding:8px;border:1px solid #475569;border-radius:7px;background:#0f172a;color:#e5e7eb";
      document.getElementById("linuxLoginViewport")?.insertAdjacentElement("afterend", node);
    }
    node.value = text;
    node.style.display = "block";
    node.focus();
    node.select();
    status("浏览器未允许自动写入剪贴板：文本已选中，请按 Ctrl+C", "bad");
  }

  async function copyRemoteSelection() {
    status("正在读取远程选中文本…");
    const result = await clipboardCommand("copy_selection");
    const text = String(result?.text || "");
    if (!text) {
      status("远程页面当前没有选中文本");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      status(`已复制远程选中文本 ${text.length} 个字符到本机剪贴板`, "ok");
    } catch (_) {
      fallbackCopyBuffer(text);
    }
  }

  function ensureClipboardToolbar() {
    const dialog = document.getElementById("linuxWorkerLoginDialog");
    const viewport = document.getElementById("linuxLoginViewport");
    if (!dialog || !viewport || dialog.querySelector("[data-v121-clipboard-toolbar]")) return;
    const toolbar = document.createElement("div");
    toolbar.dataset.v121ClipboardToolbar = "1";
    toolbar.style.cssText = "display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px";
    toolbar.innerHTML = `<button class="action" type="button" data-v121-paste-remote>粘贴到远程（中文）</button><button class="action" type="button" data-v121-copy-remote>复制远程选中文本</button><span style="font-size:12px;color:#94a3b8">可在远程画面拖动选择文字；Ctrl+C 复制回本机，Ctrl+V 粘贴本机文本到远程。</span>`;
    viewport.insertAdjacentElement("beforebegin", toolbar);
    const copyBuffer = document.getElementById("linuxLoginCopyBufferV121");
    if (copyBuffer) copyBuffer.style.display = "none";
  }

  function pointFor(event) {
    const image = document.getElementById("linuxLoginFrame");
    const rect = image?.getBoundingClientRect();
    if (!image || !rect?.width || !rect?.height) return null;
    const x = Math.max(0, Math.min(state.login.sourceWidth - 1, Math.round((event.clientX - rect.left) / rect.width * state.login.sourceWidth)));
    const y = Math.max(0, Math.min(state.login.sourceHeight - 1, Math.round((event.clientY - rect.top) / rect.height * state.login.sourceHeight)));
    return {x, y};
  }

  function sendMouse(action, event) {
    const point = pointFor(event);
    if (!point || !state.login.workerId || !state.login.ticket) return;
    const sink = document.getElementById("linuxLoginKeyboardSink");
    if (action === "down") sink?.focus({preventScroll:true});
    jsonRequest(`/api/admin/linux-workers/${encodeURIComponent(state.login.workerId)}/login-session/input`, {
      method:"POST",
      headers:{"X-Chat2API-Login-Ticket":state.login.ticket},
      body:{kind:"mouse", action, button:1, ...point},
    }).catch(error => status(String(error?.message || error), "bad"));
  }

  function installLoginEvents() {
    ensureClipboardToolbar();
    document.addEventListener("paste", event => {
      const sink = document.getElementById("linuxLoginKeyboardSink");
      if (!state.login.ticket || event.target !== sink) return;
      const text = event.clipboardData?.getData("text/plain") || "";
      if (!text) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      sink.value = "";
      pasteRemote(text).catch(error => status(String(error?.message || error), "bad"));
    }, true);

    document.addEventListener("keydown", event => {
      const sink = document.getElementById("linuxLoginKeyboardSink");
      if (event.key === "Escape") closeLimitPopovers();
      if (!state.login.ticket || event.target !== sink) return;
      const key = String(event.key || "").toLowerCase();
      const command = (event.ctrlKey || event.metaKey) && !event.altKey;
      if (command && key === "c") {
        event.preventDefault();
        event.stopImmediatePropagation();
        copyRemoteSelection().catch(error => status(String(error?.message || error), "bad"));
      }
    }, true);

    document.addEventListener("mousedown", event => {
      if (event.button !== 0 || event.target?.id !== "linuxLoginFrame" || !state.login.ticket) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      state.dragging = true;
      sendMouse("down", event);
    }, true);
    document.addEventListener("mousemove", event => {
      if (!state.dragging || !state.login.ticket) return;
      const now = performance.now();
      if (now - state.lastMoveAt < 28) return;
      state.lastMoveAt = now;
      sendMouse("move", event);
    }, true);
    document.addEventListener("mouseup", event => {
      if (!state.dragging || event.button !== 0) return;
      state.dragging = false;
      sendMouse("up", event);
    }, true);
    document.addEventListener("click", event => {
      if (event.target?.id === "linuxLoginFrame" && state.login.ticket) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
      const paste = event.target?.closest?.("[data-v121-paste-remote]");
      if (paste) {
        event.preventDefault();
        (async () => {
          let text = "";
          try { text = await navigator.clipboard.readText(); } catch (_) {}
          if (!text) text = prompt("粘贴到远程 ChatGPT 的文本（支持中文和多行）", "") || "";
          if (text) await pasteRemote(text);
        })().catch(error => status(String(error?.message || error), "bad"));
        return;
      }
      const copy = event.target?.closest?.("[data-v121-copy-remote]");
      if (copy) {
        event.preventDefault();
        copyRemoteSelection().catch(error => status(String(error?.message || error), "bad"));
      }
    }, true);
  }

  function updateLoginFromResponse(url, method, payload) {
    let parsed;
    try { parsed = new URL(url, location.href); } catch (_) { return; }
    const match = parsed.pathname.match(/^\/api\/admin\/linux-workers\/([^/]+)\/login-session(?:\/(frame))?$/);
    if (!match) return;
    const workerId = decodeURIComponent(match[1] || "");
    if (method === "POST" && !match[2] && payload?.ticket) {
      state.login.workerId = workerId;
      state.login.ticket = String(payload.ticket || "");
      state.login.sourceWidth = Number(payload.source_width || 1920);
      state.login.sourceHeight = Number(payload.source_height || 1080);
      ensureClipboardToolbar();
      const copyBuffer = document.getElementById("linuxLoginCopyBufferV121");
      if (copyBuffer) copyBuffer.style.display = "none";
    } else if (method === "GET" && match[2]) {
      state.login.sourceWidth = Number(payload?.source_width || state.login.sourceWidth || 1920);
      state.login.sourceHeight = Number(payload?.source_height || state.login.sourceHeight || 1080);
    } else if (method === "DELETE" && !match[2]) {
      state.login = {workerId:"", ticket:"", sourceWidth:1920, sourceHeight:1080};
      state.dragging = false;
    }
  }

  function installFetchObserver() {
    const baseFetch = globalThis.fetch;
    if (typeof baseFetch !== "function" || baseFetch.__chat2apiClipboardV121) return;
    const wrapped = async (input, init = {}) => {
      const url = typeof input === "string" ? input : String(input?.url || "");
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const response = await baseFetch(input, init);
      if (response.ok && url.includes("/api/admin/linux-workers/") && url.includes("/login-session")) {
        response.clone().json().then(payload => updateLoginFromResponse(url, method, payload)).catch(() => {});
      }
      return response;
    };
    wrapped.__chat2apiClipboardV121 = true;
    globalThis.fetch = wrapped;
  }

  document.addEventListener("click", event => {
    const edit = event.target?.closest?.("[data-v121-edit-limits]");
    if (edit) {
      event.preventDefault();
      event.stopPropagation();
      toggleLimitPopover(edit);
      return;
    }
    const save = event.target?.closest?.("[data-v121-save-limits]");
    if (save) {
      event.preventDefault();
      event.stopPropagation();
      void saveLimits(save);
      return;
    }
  });

  function start() {
    installFetchObserver();
    installLoginEvents();
    renderLimits();
    ensureClipboardToolbar();
  }

  state.renderLimits = renderLimits;
  state.settingsHelp = SETTINGS_HELP;
  state.copyRemoteSelection = copyRemoteSelection;
  state.pasteRemote = pasteRemote;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once:true});
  else start();
})();
