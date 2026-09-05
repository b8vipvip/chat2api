(() => {
  const KEY = "__CHAT2API_ADMIN_DEVICE_IDENTITY_V47__";
  if (globalThis[KEY]) return;
  const state = {
    rows: [],
    apiHooked: false,
    canonicalizeTimer: null,
    requestPaintTimer: null,
    requestStabilityRevision: 93,
  };
  globalThis[KEY] = state;

  const replacements = [
    [/配对码/g, "设备码"],
    [/扩展/g, "Worker"],
    [/Chrome Bridge/g, "Worker"],
    [/Chrome extension/gi, "Worker"],
    [/\bExtension\b/g, "Worker"],
  ];

  const canonical = value => {
    let text = String(value ?? "");
    for (const [pattern, replacement] of replacements) text = text.replace(pattern, replacement);
    return text;
  };

  function canonicalizeTextNode(node) {
    if (!node || node.nodeType !== Node.TEXT_NODE) return;
    const before = String(node.nodeValue || "");
    const after = canonical(before);
    if (after !== before) node.nodeValue = after;
  }

  function canonicalizeElement(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      if (!root.parentElement?.closest("script,style,tbody,pre,code")) canonicalizeTextNode(root);
      return;
    }
    if (!(root instanceof Element)) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      if (!node.parentElement?.closest("script,style,tbody,pre,code")) canonicalizeTextNode(node);
      node = walker.nextNode();
    }
    const elements = [root, ...root.querySelectorAll("[placeholder],[title],[aria-label]")];
    for (const element of elements) {
      if (element.closest?.("tbody,pre,code")) continue;
      for (const attr of ["placeholder", "title", "aria-label"]) {
        if (!element.hasAttribute?.(attr)) continue;
        const before = element.getAttribute(attr) || "";
        const after = canonical(before);
        if (after !== before) element.setAttribute(attr, after);
      }
    }
  }

  function activeView() {
    return document.querySelector(".view.active") || null;
  }

  function canonicalizeActiveView() {
    state.canonicalizeTimer = null;
    canonicalizeElement(activeView());
  }

  function queueCanonicalizeActiveView() {
    if (state.canonicalizeTimer !== null) return;
    // Navigation is the only bounded lifecycle boundary for terminology repaint.
    // Never connect this work to background API traffic or request-row mutation.
    state.canonicalizeTimer = setTimeout(canonicalizeActiveView, 0);
  }

  function canonicalizeStaticChrome() {
    for (const selector of [".brand", ".nav", ".topbar"]) {
      canonicalizeElement(document.querySelector(selector));
    }
    canonicalizeActiveView();
  }

  function requestHeader() {
    return document.querySelector("#view-requests table thead tr");
  }

  function suppressLegacyRequestIdObserver() {
    const body = document.getElementById("rqBody");
    if (!body) return false;
    // admin_window_manager_v88.js historically owned a second rqBody observer.
    // Mark that legacy hook as already installed before v88 starts, then render the
    // request-id column from this bounded request lifecycle instead. This keeps one
    // structural owner for the request table and prevents render/observer feedback.
    body.dataset.chat2apiRequestIdObserverV88 = "1";
    body.dataset.chat2apiRequestOwnerV93 = "device-identity";
    return true;
  }

  function ensureRequestIdHeader() {
    const header = requestHeader();
    if (!header) return -1;
    const existing = header.querySelector("th[data-chat2api-request-id-v88]");
    if (existing) return Array.from(header.children).indexOf(existing);
    const th = document.createElement("th");
    th.dataset.chat2apiRequestIdV88 = "1";
    th.textContent = "请求ID";
    header.insertBefore(th, header.children[1] || null);
    return Array.from(header.children).indexOf(th);
  }

  function ensureDeviceHeader() {
    const header = requestHeader();
    if (!header) return -1;
    const existing = header.querySelector("th[data-chat2api-device-identity]");
    if (existing) return Array.from(header.children).indexOf(existing);
    const headers = Array.from(header.children);
    let index = headers.findIndex(cell => String(cell.textContent || "").trim() === "模型");
    if (index < 0) index = Math.min(4, headers.length);
    const th = document.createElement("th");
    th.dataset.chat2apiDeviceIdentity = "1";
    th.textContent = "设备标识";
    header.insertBefore(th, header.children[index] || null);
    return Array.from(header.children).indexOf(th);
  }

  function paintRequestRows() {
    state.requestPaintTimer = null;
    const tbody = document.getElementById("rqBody");
    if (!tbody) return;
    suppressLegacyRequestIdObserver();
    const requestIdIndex = ensureRequestIdHeader();
    const deviceIndex = ensureDeviceHeader();
    if (requestIdIndex < 0 || deviceIndex < 0) return;
    const domRows = Array.from(tbody.querySelectorAll(":scope > tr"));
    domRows.forEach((tr, rowIndex) => {
      if (tr.children.length === 1) {
        tr.children[0].colSpan = Math.max(Number(tr.children[0].colSpan || 1), requestHeader()?.children.length || 1);
        return;
      }
      const row = state.rows[rowIndex] || {};

      let requestIdCell = tr.querySelector("td[data-chat2api-request-id-v88]");
      if (!requestIdCell) {
        requestIdCell = document.createElement("td");
        requestIdCell.dataset.chat2apiRequestIdV88 = "1";
        tr.insertBefore(requestIdCell, tr.children[requestIdIndex] || null);
      }
      const requestId = String(row.request_id || "");
      const requestIdText = requestId || "-";
      if (requestIdCell.textContent !== requestIdText) {
        requestIdCell.textContent = "";
        if (requestId) {
          const code = document.createElement("code");
          code.textContent = requestId;
          requestIdCell.appendChild(code);
        } else {
          requestIdCell.textContent = "-";
        }
      }
      if (requestIdCell.title !== requestId) requestIdCell.title = requestId;

      let cell = tr.querySelector("td[data-chat2api-device-identity]");
      if (!cell) {
        cell = document.createElement("td");
        cell.dataset.chat2apiDeviceIdentity = "1";
        tr.insertBefore(cell, tr.children[deviceIndex] || null);
      }
      const clientId = String(row.worker_client_id || row.client_id || "");
      const label = String(row.device_name || "").trim();
      const next = label || (clientId ? `未绑定 · ${clientId}` : "-");
      if (cell.textContent !== next) cell.textContent = next;
      const title = clientId ? `Worker ID：${clientId}${row.device_code_id ? ` · 设备码 ID：${row.device_code_id}` : ""}` : "";
      if (cell.title !== title) cell.title = title;
    });
  }

  function scheduleRequestPaint() {
    if (state.requestPaintTimer !== null) return;
    // The API wrapper runs before base loadRequests writes rqBody.innerHTML.
    // A single macrotask runs after that synchronous render without observing the
    // table. This makes request decoration one-shot and removes the observer ->
    // DOM write -> observer feedback path that repeatedly froze the console.
    state.requestPaintTimer = setTimeout(paintRequestRows, 0);
  }

  function captureRequestRows(path, payload) {
    const clean = String(path || "").split("#", 1)[0];
    if (!/^\/api\/admin\/requests(?:\?|$)/.test(clean)) return;
    if (!Array.isArray(payload?.data)) return;
    state.rows = payload.data.map(row => ({...row}));
    scheduleRequestPaint();
  }

  function hookApi() {
    if (state.apiHooked) return true;
    let base = null;
    try { if (typeof api === "function") base = api; } catch (_) {}
    if (!base && typeof globalThis.api === "function") base = globalThis.api;
    if (typeof base !== "function") return false;
    if (base.__chat2apiDeviceIdentityV47) {
      state.apiHooked = true;
      return true;
    }
    const wrapped = async function(path, opt = {}) {
      const payload = await base(path, opt);
      captureRequestRows(path, payload);
      return payload;
    };
    wrapped.__chat2apiDeviceIdentityV47 = true;
    try { api = wrapped; } catch (_) { globalThis.api = wrapped; }
    if (typeof globalThis.api === "function" && globalThis.api === base) globalThis.api = wrapped;
    state.apiHooked = true;
    return true;
  }

  // Do not install an rqBody MutationObserver here. Request rendering is a bounded
  // fetch -> synchronous table write -> one-shot decoration lifecycle. The legacy
  // v88 request-id observer is suppressed above before window-manager startup.
  if (!suppressLegacyRequestIdObserver() && document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", suppressLegacyRequestIdObserver, { once: true });
  }
  canonicalizeStaticChrome();
  document.addEventListener("click", event => {
    const button = event.target?.closest?.(".nav button[data-view]");
    if (button) queueCanonicalizeActiveView();
  }, true);
  window.addEventListener("hashchange", queueCanonicalizeActiveView);

  if (!hookApi()) {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (hookApi() || attempts >= 40) clearInterval(timer);
    }, 50);
  }

  scheduleRequestPaint();
})();