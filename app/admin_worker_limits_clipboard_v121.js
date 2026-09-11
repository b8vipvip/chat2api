(() => {
  const KEY = "__CHAT2API_WORKER_LIMITS_CLIPBOARD_V121__";
  if (globalThis[KEY]) return;

  const state = {
    version: 121,
    renderTask: null,
    login: {workerId:"", ticket:"", sourceWidth:1920, sourceHeight:1080},
    dragging: false,
    lastMoveAt: 0,
  };
  globalThis[KEY] = state;

  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);

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

  function workerTable() {
    const body = document.getElementById("extensionDeviceBody");
    return {body, header: body?.closest("table")?.querySelector("thead tr") || null};
  }

  function cellByKey(parent, key) {
    return [...(parent?.children || [])].find(node => String(node.dataset?.chat2apiColumnKey || "") === key) || null;
  }

  function rowClientId(tr) {
    return String(tr?.dataset?.clientId || cellByKey(tr, "client_id")?.textContent || "").trim();
  }

  function limitEditor(row) {
    const id = String(row?.client_id || "");
    const concurrency = Math.max(1, Math.min(32, Number(row?.max_concurrency || row?.capacity?.limit_units || 1)));
    const windows = Math.max(concurrency, Math.min(32, Number(row?.max_windows || concurrency)));
    const source = String(row?.window_limit_source || "concurrency");
    const note = source === "concurrency"
      ? "窗口跟随并发"
      : source === "clamped-to-concurrency"
        ? "窗口已随并发自动抬高"
        : "窗口独立设置";
    return `<div data-v121-worker-limits="${esc(id)}" style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:260px">
      <label style="display:inline-flex;align-items:center;gap:4px;font-size:12px">并发 <input data-v121-concurrency type="number" min="1" max="32" value="${concurrency}" style="width:54px;padding:4px 5px"></label>
      <label style="display:inline-flex;align-items:center;gap:4px;font-size:12px">窗口 <input data-v121-windows type="number" min="1" max="32" value="${windows}" style="width:54px;padding:4px 5px"></label>
      <button class="action" type="button" data-v121-save-limits>保存</button>
      <span class="muted" data-v121-limit-note style="font-size:11px">${esc(note)}</span>
    </div>`;
  }

  async function renderLimits(force = false) {
    if (state.renderTask) return state.renderTask;
    const task = (async () => {
      const view = document.getElementById("view-extensions");
      if (!force && view && !view.classList.contains("active")) return null;
      const payload = await jsonRequest("/api/admin/extensions");
      const rows = Array.isArray(payload?.clients) ? payload.clients : [];
      const byId = new Map(rows.map(row => [String(row?.client_id || ""), row]));
      const {body, header} = workerTable();
      if (!body || !header) return payload;
      const headerCell = cellByKey(header, "worker_settings");
      if (headerCell) {
        headerCell.textContent = "并发 / 窗口";
        headerCell.title = "并发=同时执行的请求上限；窗口=此 Worker 最多保留的按需 ChatGPT 路由窗口。窗口不会预开。";
      }
      for (const tr of body.rows) {
        if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) continue;
        const id = rowClientId(tr);
        const row = byId.get(id);
        const cell = cellByKey(tr, "worker_settings");
        if (!row || !cell) continue;
        const html = limitEditor(row);
        if (cell.innerHTML !== html) cell.innerHTML = html;
      }
      document.documentElement.dataset.chat2apiWorkerLimitsRevision = "121";
      return payload;
    })().catch(error => {
      console.warn("chat2api worker limits v121 render failed", error);
      return null;
    });
    state.renderTask = task;
    try { return await task; } finally { if (state.renderTask === task) state.renderTask = null; }
  }

  async function saveLimits(button) {
    const editor = button.closest("[data-v121-worker-limits]");
    if (!editor) return;
    const clientId = String(editor.dataset.v121WorkerLimits || "");
    const concurrency = Number(editor.querySelector("[data-v121-concurrency]")?.value || 0);
    const windows = Number(editor.querySelector("[data-v121-windows]")?.value || 0);
    const note = editor.querySelector("[data-v121-limit-note]");
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 32 || !Number.isInteger(windows) || windows < 1 || windows > 32) {
      if (note) note.textContent = "请输入 1~32 的整数";
      return;
    }
    if (windows < concurrency) {
      if (note) note.textContent = "窗口数不能小于并发数";
      return;
    }
    button.disabled = true;
    if (note) note.textContent = "保存中…";
    try {
      await jsonRequest(`/api/admin/extensions/${encodeURIComponent(clientId)}/concurrency`, {
        method:"PUT",
        body:{max_concurrency:concurrency},
      });
      let concurrencyApplied = true;
      try {
        const applied = await jsonRequest(`/api/admin/extensions/${encodeURIComponent(clientId)}/capacity/apply`, {
          method:"POST",
          body:{target:concurrency},
        });
        concurrencyApplied = applied?.applied === true || applied?.ok === true;
      } catch (_) {
        concurrencyApplied = false;
      }
      const windowResult = await jsonRequest(`/api/admin/extensions/${encodeURIComponent(clientId)}/windows/limit`, {
        method:"PUT",
        body:{max_windows:windows},
      });
      const windowsApplied = windowResult?.applied === true;
      if (note) note.textContent = concurrencyApplied && windowsApplied ? "已保存并生效" : "已保存 · Worker 在线后自动生效";
      const reload = globalThis.chat2apiReloadCanonicalWorkerListV59;
      if (typeof reload === "function") await reload();
      await renderLimits(true);
    } catch (error) {
      if (note) note.textContent = String(error?.message || error);
    } finally {
      button.disabled = false;
    }
  }

  function installWorkerHooks() {
    const baseReload = globalThis.chat2apiReloadCanonicalWorkerListV59;
    if (typeof baseReload === "function" && !baseReload.__chat2apiWorkerLimitsV121) {
      const wrapped = async (...args) => {
        const result = await baseReload(...args);
        await renderLimits(true);
        return result;
      };
      wrapped.__chat2apiWorkerLimitsV121 = true;
      globalThis.chat2apiReloadCanonicalWorkerListV59 = wrapped;
    }
    const baseShow = globalThis.show;
    if (typeof baseShow === "function" && !baseShow.__chat2apiWorkerLimitsV121) {
      const wrappedShow = async (...args) => {
        const result = await baseShow(...args);
        if (args[0] === "extensions") await renderLimits(true);
        return result;
      };
      wrappedShow.__chat2apiWorkerLimitsV121 = true;
      globalThis.show = wrappedShow;
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
    const button = event.target?.closest?.("[data-v121-save-limits]");
    if (!button) return;
    event.preventDefault();
    saveLimits(button);
  });

  function start() {
    installFetchObserver();
    installLoginEvents();
    installWorkerHooks();
    setTimeout(() => { installWorkerHooks(); renderLimits(true); ensureClipboardToolbar(); }, 140);
    setTimeout(() => { installWorkerHooks(); renderLimits(true); ensureClipboardToolbar(); }, 900);
  }

  state.renderLimits = renderLimits;
  state.copyRemoteSelection = copyRemoteSelection;
  state.pasteRemote = pasteRemote;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once:true});
  else start();
})();
