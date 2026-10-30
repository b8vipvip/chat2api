/* Non-Voice native rich reply observer. No script/HTML/CSS or source URLs leave the tab.
 * Only the same request-v6 assistant candidate is eligible. This observer may
 * fail without changing chat.completed, delta, cancellation or window lifetime.
 */
(() => {
  "use strict";
  const KEY = "__CHAT2API_NATIVE_CONTENT_V1__";
  if (globalThis[KEY]) return;
  const tags = new Set("div p span strong em h1 h2 h3 h4 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br".split(" "));
  const banned = "button,script,style,form,nav,header,footer,aside,input,textarea,select,iframe,canvas,svg,[role='menu'],[contenteditable='true']";
  const styleKeys = {
    display: ["block","inline","inline-block","flex","inline-flex","grid"],
    flexDirection: ["row","row-reverse","column","column-reverse"],
    flexWrap: ["wrap","nowrap"],
    justifyContent: ["start","center","end","space-between","space-around"],
    alignItems: ["start","center","end","stretch","baseline"],
    objectFit: ["contain","cover","fill","none"],
    textAlign: ["left","center","right","justify","start","end"],
    fontWeight: ["400","500","600","700"],
  };
  const allowedLength = new Set(["gap","padding","marginTop","marginBottom","width","maxWidth","height","borderRadius","fontSize","lineHeight"]);
  const length = /^(?:0|\d{1,4}(?:\.\d{1,2})?(?:px|rem|%))$/;
  const color = /^(?:#[0-9a-f]{3,8}|rgba?\([0-9.,\s]+\))$/i;
  const grid = /^(?:\d{1,3}(?:px|%)|minmax\(\d{1,3}px,\s*1fr\)|1fr)(?:\s+(?:\d{1,3}(?:px|%)|minmax\(\d{1,3}px,\s*1fr\)|1fr)){0,5}$/;
  const cssList = [...Object.keys(styleKeys),...allowedLength,"color","backgroundColor","gridTemplateColumns"];
  function safeStyle(element) {
    const css = getComputedStyle(element), obj = {};
    for (const key of cssList) {
      const value = String(css[key] || "").trim();
      if (styleKeys[key]?.includes(value) ||
          allowedLength.has(key) && value.length <= 16 && length.test(value) ||
          ["color","backgroundColor"].includes(key) && value.length <= 48 && color.test(value) ||
          key === "gridTemplateColumns" && value.length <= 120 && grid.test(value)) {
        obj[key] = value;
      }
    }
    return obj;
  }
  function snapshot(active) {
    if (!active?.submitStartedAt || typeof Element === "undefined") return null;
    const candidate = active.lastCandidateNode;
    if (!(candidate instanceof Element) || !candidate.isConnected) return null;
    const reply = candidate.closest?.("[data-message-author-role='assistant'],[data-testid^='conversation-turn']")
      || candidate;
    if (reply.matches?.("[data-message-author-role='user']") || reply.closest?.("nav,aside")) return null;
    let nodes = 0, chars = 0, images = 0;
    const textBudget = 48000;
    const ignore = element => element.matches?.(banned) || !!element.closest?.("nav,aside,footer");
    const build = (element, depth) => {
      if (depth > 14 || nodes >= 420) return null;
      if (element.nodeType === Node.TEXT_NODE) {
        const raw = String(element.nodeValue || "");
        if (!raw.trim()) return null;
        if (chars + raw.length > textBudget) return null;
        chars += raw.length; nodes += 1;
        return {tag:"span",text:raw};
      }
      if (!(element instanceof Element) || ignore(element)) return null;
      const rect = element.getBoundingClientRect?.();
      const computed = getComputedStyle(element);
      if (computed.display === "none" || computed.visibility === "hidden") return null;
      const tag = element.tagName.toLowerCase();
      let mapped = tags.has(tag) ? tag : "div";
      if (tag === "img") {
        if (!rect || rect.width < 40 || rect.height < 40 || images >= 40) return null;
        nodes += 1;
        return {tag:"img",media_index:images++,style:safeStyle(element)};
      }
      const children = [];
      for (const child of element.childNodes) {
        if (nodes >= 420 || children.length >= 80) break;
        const value = build(child, depth + 1);
        if (value) children.push(value);
      }
      if (!children.length && !["hr","br"].includes(mapped)) return null;
      nodes += 1;
      const result = {tag:mapped,style:safeStyle(element)};
      if (children.length) result.children = children;
      return result;
    };
    const root = build(reply, 0);
    if (!root) return null;
    return {version:1,revision:(active.nativeRevision || 0) + 1,root};
  }

  async function capture(active, final = false) {
    if (!active?.requestId || !active.submitStartedAt) return;
    const now = Date.now();
    if (!final && now - Number(active.nativeLastAt || 0) < 900) return;
    active.nativeLastAt = now;
    try {
      const tree = snapshot(active);
      if (!tree) return;
      const hash = JSON.stringify(tree.root);
      if (!final && hash === active.nativeLastHash) return;
      if (hash.length > 57000) return;
      active.nativeLastHash = hash;
      active.nativeRevision = tree.revision;
      const send = chrome.runtime.sendMessage({type:"chat2api.event",event:{
        type:"chat.native.snapshot",request_id:active.requestId,snapshot:tree,
      }});
      if (final) await Promise.race([send.catch(() => {}),new Promise(r=>setTimeout(r,240))]);
      else void send.catch(()=>{});
    } catch (_) { /* never alter request termination */ }
  }
  globalThis[KEY] = Object.freeze({capture,snapshot});
})();
