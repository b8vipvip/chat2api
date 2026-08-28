(() => {
  const KEY = "__CHAT2API_POPUP_TERMINOLOGY_V47__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = true;

  const pairs = [
    [/配对码/g, "设备码"],
    [/扩展/g, "Worker"],
    [/Chrome Bridge/g, "Worker"],
    [/未配对/g, "未绑定设备码"],
    [/配对失败/g, "设备码绑定失败"],
    [/配对成功/g, "设备码绑定成功"],
  ];

  const canonical = value => {
    let text = String(value ?? "");
    for (const [pattern, replacement] of pairs) text = text.replace(pattern, replacement);
    return text;
  };

  const patchText = node => {
    if (!node || node.nodeType !== Node.TEXT_NODE) return;
    const before = String(node.nodeValue || "");
    const after = canonical(before);
    if (before !== after) node.nodeValue = after;
  };

  const patch = root => {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      patchText(root);
      return;
    }
    if (!(root instanceof Element) && root !== document.body) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      if (!node.parentElement?.closest("script,style")) patchText(node);
      node = walker.nextNode();
    }
    const elements = root instanceof Element ? [root, ...root.querySelectorAll("[placeholder],[title],[aria-label]")] : [];
    for (const element of elements) {
      for (const attr of ["placeholder", "title", "aria-label"]) {
        if (!element.hasAttribute(attr)) continue;
        const before = element.getAttribute(attr) || "";
        const after = canonical(before);
        if (before !== after) element.setAttribute(attr, after);
      }
    }
  };

  patch(document.body);
  new MutationObserver(mutations => {
    for (const mutation of mutations) {
      if (mutation.type === "characterData") patchText(mutation.target);
      for (const node of mutation.addedNodes || []) patch(node);
    }
  }).observe(document.body, {childList:true,subtree:true,characterData:true});
})();
