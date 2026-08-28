(() => {
  const KEY = "__CHAT2API_ADMIN_DEVICE_IDENTITY_V47__";
  if (globalThis[KEY]) return;
  const state = { rows: [], apiHooked: false };
  globalThis[KEY] = state;

  const replacements = [
    [/配对码/g, "设备码"],
    [/扩展/g, "Worker"],
    [/Chrome Bridge/g, "Worker"],
    [/Chrome extension/gi, "Worker"],
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
      canonicalizeTextNode(root);
      return;
    }
    if (!(root instanceof Element) && root !== document.body) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      if (!node.parentElement?.closest("script,style")) canonicalizeTextNode(node);
      node = walker.nextNode();
    }
    const elements = root instanceof Element ? [root, ...root.querySelectorAll("[placeholder],[title],[aria-label]")] : [];
    for (const element of elements) {
      for (const attr of ["placeholder", "title", "aria-label"]) {
        if (!element.hasAttribute?.(attr)) continue;
        const before = element.getAttribute(attr) || "";
        const after = canonical(before);
        if (after !== before) element.setAttribute(attr, after);
      }
    }
  }

  function requestHeader() {
    return document.querySelector("#view-requests table thead tr");
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
    return index;
  }

  function paintRequestRows() {
    const tbody = document.getElementById("rqBody");
    if (!tbody) return;
    const index = ensureDeviceHeader();
    if (index < 0) return;
    const domRows = Array.from(tbody.querySelectorAll(":scope > tr"));
    domRows.forEach((tr, rowIndex) => {
      if (tr.children.length === 1) {
        tr.children[0].colSpan = Math.max(Number(tr.children[0].colSpan || 1), requestHeader()?.children.length || 1);
        return;
      }
      const row = state.rows[rowIndex] || {};
      let cell = tr.querySelector("td[data-chat2api-device-identity]");
      if (!cell) {
        cell = document.createElement("td");
        cell.dataset.chat2apiDeviceIdentity = "1";
        tr.insertBefore(cell, tr.children[index] || null);
      }
      const clientId = String(row.worker_client_id || row.client_id || "");
      const label = String(row.device_name || "").trim();
      const next = label || (clientId ? `未绑定 · ${clientId}` : "-");
      if (cell.textContent !== next) cell.textContent = next;
      const title = clientId ? `Worker ID：${clientId}${row.device_code_id ? ` · 设备码 ID：${row.device_code_id}` : ""}` : "";
      if (cell.title !== title) cell.title = title;
    });
    canonicalizeElement(document.getElementById("view-requests"));
  }

  function captureRequestRows(path, payload) {
    const clean = String(path || "").split("#", 1)[0];
    if (!/^\/api\/admin\/requests(?:\?|$)/.test(clean)) return;
    if (!Array.isArray(payload?.data)) return;
    state.rows = payload.data.map(row => ({...row}));
    queueMicrotask(paintRequestRows);
  }

  function hookApi() {
    if (state.apiHooked) return true;
    const base = globalThis.api;
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
    globalThis.api = wrapped;
    state.apiHooked = true;
    return true;
  }

  const tbody = document.getElementById("rqBody");
  if (tbody) {
    new MutationObserver(() => queueMicrotask(paintRequestRows)).observe(tbody, {childList:true,subtree:false});
  }

  // Normalize every currently visible administrator label, then only inspect
  // changed/added nodes. Writes are conditional so this observer cannot create
  // the self-triggering loop that previously froze the Linux Worker page.
  canonicalizeElement(document.body);
  new MutationObserver(mutations => {
    for (const mutation of mutations) {
      if (mutation.type === "characterData") canonicalizeTextNode(mutation.target);
      for (const node of mutation.addedNodes || []) canonicalizeElement(node);
    }
  }).observe(document.body, {childList:true,subtree:true,characterData:true});

  if (!hookApi()) {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (hookApi() || attempts >= 40) clearInterval(timer);
    }, 50);
  }

  paintRequestRows();
})();
