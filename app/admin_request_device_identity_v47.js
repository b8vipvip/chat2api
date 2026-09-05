(() => {
  const KEY = "__CHAT2API_ADMIN_DEVICE_IDENTITY_V47__";
  if (globalThis[KEY]) return;
  const state = { revision: 94, structural_owner: false, canonicalizeTimer: null };
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
    state.canonicalizeTimer = setTimeout(canonicalizeActiveView, 0);
  }

  function canonicalizeStaticChrome() {
    for (const selector of [".brand", ".nav", ".topbar"]) canonicalizeElement(document.querySelector(selector));
    canonicalizeActiveView();
  }

  // v94: this module is terminology-only. Request-history structure and values
  // are owned exclusively by admin_request_history_v94.js. Do not wrap api(),
  // do not observe #rqBody, and do not add request-table cells here.
  canonicalizeStaticChrome();
  document.addEventListener("click", event => {
    if (event.target?.closest?.(".nav button[data-view]")) queueCanonicalizeActiveView();
  }, true);
  window.addEventListener("hashchange", queueCanonicalizeActiveView);
})();
