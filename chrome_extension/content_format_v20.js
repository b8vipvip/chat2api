(() => {
  const KEY = "__CHAT2API_CONTENT_FORMAT_V20__";
  if (globalThis[KEY]) return;

  const baseSendMessage = chrome.runtime.sendMessage.bind(chrome.runtime);
  const state = {
    textByRequest: new Map(),
    statusTimers: new Map(),
    lastStatusByRequest: new Map(),
  };
  globalThis[KEY] = state;

  function visible(node) {
    if (!node || !(node instanceof Element)) return false;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function cleanInline(value) {
    return String(value || "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t\f\v]+/g, " ")
      .replace(/ *\n */g, "\n")
      .trim();
  }

  function cleanDocument(value) {
    return String(value || "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\u00a0/g, " ")
      .replace(/\r\n?/g, "\n")
      .split("\n")
      .map(line => line.replace(/[ \t]+$/g, ""))
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function inlineText(node) {
    if (!node) return "";
    if (node.nodeType === Node.TEXT_NODE) return String(node.nodeValue || "");
    if (!(node instanceof Element)) return "";
    if (["BUTTON", "SVG", "NAV", "FOOTER"].includes(node.tagName)) return "";
    if (node.getAttribute("aria-hidden") === "true") return "";
    if (/copy|feedback|action/i.test(String(node.getAttribute("data-testid") || ""))) return "";
    if (node.tagName === "BR") return "\n";
    if (node.tagName === "CODE" && node.parentElement?.tagName !== "PRE") {
      const value = cleanInline(node.textContent || "");
      return value ? `\`${value.replace(/`/g, "\\`")}\`` : "";
    }
    let output = "";
    for (const child of node.childNodes) output += inlineText(child);
    return output;
  }

  function directListItemText(li, depth) {
    const parts = [];
    let inline = "";
    for (const child of li.childNodes) {
      if (child instanceof Element && ["UL", "OL"].includes(child.tagName)) {
        if (cleanInline(inline)) parts.push(cleanInline(inline));
        inline = "";
        const nested = blockText(child, depth + 1);
        if (nested) parts.push(nested);
      } else {
        inline += inlineText(child);
      }
    }
    if (cleanInline(inline)) parts.unshift(cleanInline(inline));
    return parts.join("\n");
  }

  function blockText(node, depth = 0) {
    if (!node) return "";
    if (node.nodeType === Node.TEXT_NODE) return cleanInline(node.nodeValue || "");
    if (!(node instanceof Element)) return "";
    if (!visible(node) && node !== document.body) return "";

    const tag = node.tagName;
    if (/^H[1-6]$/.test(tag)) {
      const level = Number(tag.slice(1));
      const text = cleanInline(inlineText(node));
      return text ? `${"#".repeat(level)} ${text}` : "";
    }
    if (tag === "P") return cleanInline(inlineText(node));
    if (tag === "HR") return "---";
    if (tag === "PRE") {
      const text = String(node.textContent || "").replace(/\r\n?/g, "\n").trimEnd();
      if (!text) return "";
      return text.split("\n").map(line => `    ${line}`).join("\n");
    }
    if (tag === "BLOCKQUOTE") {
      const value = serializeChildren(node, depth + 1);
      return value ? value.split("\n").map(line => `> ${line}`).join("\n") : "";
    }
    if (tag === "UL" || tag === "OL") {
      const rows = [...node.children].filter(child => child.tagName === "LI");
      return rows.map((li, index) => {
        const marker = tag === "OL" ? `${index + 1}. ` : "- ";
        const raw = directListItemText(li, depth + 1);
        const lines = raw.split("\n");
        const first = `${"  ".repeat(depth)}${marker}${lines.shift() || ""}`;
        const rest = lines.map(line => `${"  ".repeat(depth + 1)}${line}`);
        return [first, ...rest].join("\n");
      }).filter(Boolean).join("\n");
    }
    if (tag === "TABLE") {
      const rows = [...node.querySelectorAll("tr")];
      return rows.map(row => [...row.children]
        .filter(cell => ["TH", "TD"].includes(cell.tagName))
        .map(cell => cleanInline(inlineText(cell)))
        .join(" | "))
        .filter(Boolean)
        .join("\n");
    }

    const hasBlockChildren = [...node.children].some(child =>
      /^(H[1-6]|P|UL|OL|PRE|BLOCKQUOTE|TABLE|HR|DIV|SECTION|ARTICLE)$/.test(child.tagName)
    );
    if (hasBlockChildren) return serializeChildren(node, depth);
    return cleanInline(inlineText(node));
  }

  function serializeChildren(root, depth = 0) {
    const blocks = [];
    let pendingInline = "";
    for (const child of root.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        pendingInline += String(child.nodeValue || "");
        continue;
      }
      if (!(child instanceof Element)) continue;
      const blockLike = /^(H[1-6]|P|UL|OL|PRE|BLOCKQUOTE|TABLE|HR|DIV|SECTION|ARTICLE)$/.test(child.tagName);
      if (!blockLike) {
        pendingInline += inlineText(child);
        continue;
      }
      const pending = cleanInline(pendingInline);
      if (pending) blocks.push(pending);
      pendingInline = "";
      const value = blockText(child, depth);
      if (value) blocks.push(value);
    }
    const pending = cleanInline(pendingInline);
    if (pending) blocks.push(pending);
    return cleanDocument(blocks.join("\n\n"));
  }

  function assistantNodes() {
    const nodes = [];
    const seen = new Set();
    for (const selector of [
      "[data-message-author-role='assistant']",
      "article[data-testid^='conversation-turn'] [data-message-author-role='assistant']",
    ]) {
      for (const node of document.querySelectorAll(selector)) {
        if (!seen.has(node) && visible(node)) {
          seen.add(node);
          nodes.push(node);
        }
      }
    }
    return nodes;
  }

  function latestAssistantRoot() {
    const nodes = assistantNodes();
    const latest = nodes[nodes.length - 1];
    if (!latest) return null;
    const formatted = [...latest.querySelectorAll("[data-message-content], .markdown, [class*='markdown']")]
      .filter(visible)
      .pop();
    return formatted || latest;
  }

  function currentStructuredText() {
    const root = latestAssistantRoot();
    return root ? serializeChildren(root) || blockText(root) : "";
  }

  function statusKind(text) {
    const value = cleanInline(text).replace(/[.。…·:：]+$/g, "").trim().toLowerCase();
    if (/^(正在)?思考(中)?$|^thinking( now)?$/.test(value)) return "thinking";
    if (/^(正在)?(分析|推理)(中)?$|^(analyzing|reasoning)( now)?$/.test(value)) return "reasoning";
    if (/^(正在)?(搜索|浏览)(中)?$|^(searching|browsing)( now)?$/.test(value)) return "searching";
    if (/^(正在)?(生成|处理)(中)?$|^(generating|working)( now)?$/.test(value)) return "working";
    return null;
  }

  function visibleStatus() {
    const root = latestAssistantRoot()?.closest("article,[data-testid^='conversation-turn']") || document;
    const candidates = [...root.querySelectorAll("[role='status'],[aria-live='polite'],button,span")].filter(visible);
    for (let i = candidates.length - 1; i >= 0; i -= 1) {
      const text = cleanInline(candidates[i].innerText || candidates[i].textContent || "");
      if (!text || text.length > 60) continue;
      const kind = statusKind(text);
      if (kind) return { kind, text };
    }
    return null;
  }

  function stopStatusPolling(requestId) {
    const timer = state.statusTimers.get(requestId);
    if (timer) clearInterval(timer);
    state.statusTimers.delete(requestId);
    state.lastStatusByRequest.delete(requestId);
  }

  function startStatusPolling(requestId) {
    if (!requestId || state.statusTimers.has(requestId)) return;
    const tick = async () => {
      const found = visibleStatus();
      if (!found) {
        state.lastStatusByRequest.set(requestId, "");
        return;
      }
      const fingerprint = `${found.kind}:${found.text}`;
      if (state.lastStatusByRequest.get(requestId) === fingerprint) return;
      state.lastStatusByRequest.set(requestId, fingerprint);
      try {
        await baseSendMessage({
          type: "chat2api.event",
          event: {
            type: "chat.status",
            request_id: requestId,
            status: found.kind,
            text: found.text,
            source: "visible-chatgpt-ui-v20",
          },
        });
      } catch (_) {}
    };
    const timer = setInterval(() => { tick().catch(() => {}); }, 260);
    state.statusTimers.set(requestId, timer);
    tick().catch(() => {});
  }

  chrome.runtime.sendMessage = async function chat2apiFormattedSendMessage(message, ...rest) {
    let outgoing = message;
    const event = message?.type === "chat2api.event" ? message.event : null;
    const requestId = String(event?.request_id || "");

    if (event?.type === "chat.started" && requestId) startStatusPolling(requestId);

    if (requestId && ["chat.delta", "chat.snapshot"].includes(event?.type)) {
      const structured = currentStructuredText();
      if (structured) {
        const previous = state.textByRequest.get(requestId) || "";
        if (structured === previous) return undefined;
        if (structured.startsWith(previous)) {
          state.textByRequest.set(requestId, structured);
          outgoing = {
            ...message,
            event: { ...event, type: "chat.delta", delta: structured.slice(previous.length), format: "markdown" },
          };
        } else {
          state.textByRequest.set(requestId, structured);
          outgoing = {
            ...message,
            event: { ...event, type: "chat.snapshot", text: structured, format: "markdown" },
          };
        }
      }
    }

    if (requestId && event?.type === "chat.completed") {
      const structured = currentStructuredText();
      if (structured) {
        state.textByRequest.set(requestId, structured);
        outgoing = { ...message, event: { ...event, text: structured, format: "markdown" } };
      }
    }

    if (requestId && ["chat.completed", "chat.error", "chat.cancelled"].includes(event?.type)) {
      stopStatusPolling(requestId);
      setTimeout(() => state.textByRequest.delete(requestId), 5000);
    }

    return baseSendMessage(outgoing, ...rest);
  };
})();
