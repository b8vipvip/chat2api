/* W-03: Strictly DOM-built Markdown and rich-block presentation.
 * This is a view layer. It does not authenticate media, execute tools, or
 * decide Worker completion/native snapshot finality.
 */
(() => {
  "use strict";
  const MAX_TEXT = 120000;
  const MAX_BLOCKS = 100;
  const MAX_MATH_CHARS = 4096;
  const TICK = String.fromCharCode(96);
  const FENCE = TICK.repeat(3);

  function node(tag, css, value) {
    const element = document.createElement(tag);
    if (css) element.className = css;
    if (value !== undefined) element.textContent = String(value);
    return element;
  }

  function safeUrl(value) {
    const raw = typeof value === "string" ? value.trim() : "";
    if (!raw || raw.length > 2048) return "";
    try {
      const url = new URL(raw, document.baseURI);
      if (url.protocol !== "http:" && url.protocol !== "https:") return "";
      // Protocol-relative and credential-bearing URLs are avoided.
      if (raw.startsWith("//") || url.username || url.password) return "";
      return url.href;
    } catch { return ""; }
  }

  function appendLink(parent, text, raw) {
    const url = safeUrl(raw);
    if (!url) {
      parent.appendChild(document.createTextNode(text));
      return;
    }
    const a = node("a", "", text);
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.referrerPolicy = "no-referrer";
    parent.appendChild(a);
  }

  function mathFallback(host, tex, displayMode) {
    if (displayMode) {
      const pre = node("pre", "math-fallback");
      pre.appendChild(node("code", "", tex));
      host.appendChild(pre);
    } else {
      host.appendChild(node("code", "math-fallback", tex));
    }
  }

  function renderMath(host, source, displayMode) {
    const raw = String(source || "");
    if (!raw.trim()) return;
    if (raw.length > MAX_MATH_CHARS) {
      mathFallback(host, raw.slice(0, MAX_MATH_CHARS) + "\n[公式过长，未完整渲染]", displayMode);
      return;
    }
    const tex = raw;
    const katex = window.katex;
    if (!katex || typeof katex.render !== "function") {
      mathFallback(host, tex, displayMode);
      return;
    }
    const target = node(displayMode ? "div" : "span",
      displayMode ? "math-block" : "math-inline");
    try {
      katex.render(tex, target, {
        displayMode: Boolean(displayMode),
        output: "htmlAndMathml",
        throwOnError: false,
        trust: false,
        strict: "warn",
        maxExpand: 1000,
        maxSize: 20,
      });
      host.appendChild(target);
    } catch {
      mathFallback(host, tex, displayMode);
    }
  }

  function inline(parent, raw) {
    const text = String(raw || "");
    const pattern = new RegExp(
      "(" + TICK + "[^" + TICK + "\\n]+" + TICK +
      "|\\\\\\([^\\n]+?\\\\\\)" +
      "|\\$(?!\\$)[^$\\n]+\\$(?!\\$)" +
      "|\\[[^\\]\\n]{1,160}\\]\\([^\\s)]+\\)" +
      "|\\*\\*[^*\\n]+\\*\\*|\\*[^*\\n]+\\*" +
      "|https?:\\/\\/[^\\s<>]+)", "g"
    );
    let last = 0;
    for (const match of text.matchAll(pattern)) {
      const token = match[0];
      if (match.index > last) {
        parent.appendChild(document.createTextNode(text.slice(last, match.index)));
      }
      if (token.startsWith(TICK)) {
        parent.appendChild(node("code", "", token.slice(1, -1)));
      } else if (token.startsWith("\\(")) {
        renderMath(parent, token.slice(2, -2), false);
      } else if (token.startsWith("$")) {
        renderMath(parent, token.slice(1, -1), false);
      } else if (token.startsWith("**")) {
        parent.appendChild(node("strong", "", token.slice(2, -2)));
      } else if (token.startsWith("*")) {
        parent.appendChild(node("em", "", token.slice(1, -1)));
      } else if (token.startsWith("[")) {
        const split = token.indexOf("](");
        appendLink(parent, token.slice(1, split), token.slice(split + 2, -1));
      } else {
        appendLink(parent, token, token);
      }
      last = match.index + token.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function paragraph(host, lines) {
    const p = node("p");
    lines.forEach((line, index) => {
      if (index) p.appendChild(node("br"));
      inline(p, line);
    });
    host.appendChild(p);
  }

  function tableCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(s => s.trim());
  }

  function codeBlock(host, language, lines) {
    const wrapper = node("div", "code-block");
    const toolbar = node("div", "code-toolbar");
    toolbar.appendChild(node("span", "", language.slice(0, 40) || "code"));
    const copy = node("button", "", "复制代码");
    copy.type = "button";
    const value = lines.join("\n");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(value);
        copy.textContent = "已复制";
      } catch {
        copy.textContent = "复制失败";
      }
    });
    toolbar.appendChild(copy);
    const pre = node("pre");
    pre.appendChild(node("code", "", value));
    wrapper.append(toolbar, pre);
    host.appendChild(wrapper);
  }

  function renderMarkdown(host, source) {
    const input = String(source || "");
    const lines = input.slice(0, MAX_TEXT).replace(/\r\n/g, "\n").split("\n");
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (line.trim().startsWith(FENCE)) {
        const language = line.trim().slice(FENCE.length).trim();
        const code = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(FENCE)) code.push(lines[i++]);
        if (i < lines.length) i++;
        codeBlock(host, language, code);
        continue;
      }
      const trimmed = line.trim();
      const displayInline = trimmed.match(/^\$\$([\s\S]+)\$\$/)
        || trimmed.match(/^\\\[([\s\S]+)\\\]$/);
      if (trimmed === "$$" || trimmed === "\\[" || displayInline) {
        if (displayInline) {
          renderMath(host, displayInline[1], true);
          i++;
          continue;
        }
        const close = trimmed === "$$" ? "$$" : "\\]";
        let end = i + 1;
        while (end < lines.length && lines[end].trim() !== close) end++;
        if (end >= lines.length) {
          paragraph(host, lines.slice(i));
          break;
        }
        const math = lines.slice(i + 1, end);
        i = end + 1;
        renderMath(host, math.join("\n"), true);
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        const h = node("h" + heading[1].length);
        inline(h, heading[2]);
        host.appendChild(h);
        i++;
        continue;
      }
      if (/^\s*([-*_])\1\1+\s*$/.test(line)) {
        host.appendChild(node("hr"));
        i++;
        continue;
      }
      if (/^>\s?/.test(line)) {
        const quotes = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          quotes.push(lines[i++].replace(/^>\s?/, ""));
        }
        const quote = node("blockquote");
        paragraph(quote, quotes);
        host.appendChild(quote);
        continue;
      }
      if (/^\s*(?:[-*+] |\d+\. )/.test(line)) {
        const ordered = /^\s*\d+\. /.test(line);
        const list = node(ordered ? "ol" : "ul");
        while (i < lines.length) {
          const match = lines[i].match(/^\s*(?:[-*+] |\d+\. )(.*)$/);
          if (!match) break;
          const item = node("li");
          inline(item, match[1]);
          list.appendChild(item);
          i++;
        }
        host.appendChild(list);
        continue;
      }
      if (line.includes("|") && i + 1 < lines.length &&
          /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
        const headers = tableCells(line);
        i += 2;
        const table = node("table");
        const thead = node("thead");
        const headerRow = node("tr");
        headers.forEach(value => {
          const th = node("th");
          inline(th, value);
          headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);
        const tbody = node("tbody");
        while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
          const values = tableCells(lines[i++]);
          const tr = node("tr");
          headers.forEach((_, index) => {
            const cell = node("td");
            inline(cell, values[index] || "");
            tr.appendChild(cell);
          });
          tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        host.appendChild(table);
        continue;
      }
      const body = [];
      while (i < lines.length && lines[i].trim()) {
        if (body.length && (lines[i].trim().startsWith(FENCE) ||
            lines[i].trim() === "$$" || lines[i].trim() === "\\[" ||
            /^\$\$[\s\S]+\$\$/.test(lines[i].trim()) ||
            /^\\\[[\s\S]+\\\]$/.test(lines[i].trim()) ||
            /^#{1,4}\s+/.test(lines[i]) ||
            /^>\s?/.test(lines[i]) || /^\s*(?:[-*+] |\d+\. )/.test(lines[i]) ||
            (lines[i].includes("|") && i + 1 < lines.length &&
             /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) ||
            /^\s*([-*_])\1\1+\s*$/.test(lines[i]))) break;
        body.push(lines[i++]);
      }
      if (body.length) paragraph(host, body);
      else i++;
    }
    if (input.length > MAX_TEXT) {
      host.appendChild(node("p", "render-truncated", "[内容过长，已截断]"));
    }
  }

  function renderRichBlocks(host, blocks) {
    // Only display evidence supplied by an authorized API; this method does
    // not fetch external media, execute tools or claim any request is final.
    if (!Array.isArray(blocks)) return;
    blocks.slice(0, MAX_BLOCKS).forEach(part => {
      if (!part || typeof part !== "object") return;
      const type = String(part.type || "");
      if (type === "text") {
        renderMarkdown(host, part.text);
      } else if (type === "code") {
        codeBlock(host, String(part.language || "code"), String(part.text || "").slice(0, MAX_TEXT).split("\n"));
      } else if (type === "math") {
        renderMath(host, String(part.tex || ""), true);
      } else if (type === "citation") {
        const div = node("div", "rich-citation");
        div.appendChild(node("span", "", "引用链接（未验证）："));
        appendLink(div, String(part.title || part.url || "查看来源").slice(0, 240), part.url);
        host.appendChild(div);
      } else if (type === "file") {
        const fileId = String(part.file_id || "");
        if (/^file_[A-Za-z0-9_-]{8,100}$/.test(fileId)) {
          // The endpoint independently verifies the session and FileStore owner;
          // the renderer never trusts a client-provided path or remote URL.
          const card = node("div", "rich-file-card");
          const link = node("a", "rich-file-download",
            String(part.filename || "下载附件").slice(0, 180));
          link.href = "/api/user/chat/files/" + encodeURIComponent(fileId) + "/download";
          link.rel = "noopener noreferrer";
          link.referrerPolicy = "no-referrer";
          card.appendChild(link);
          host.appendChild(card);
        } else {
          host.appendChild(node("div", "rich-attachment-fallback", "文件附件：缺少有效文件引用"));
        }
      } else if (type === "image") {
        const fileId = String(part.file_id || "");
        if (/^file_[A-Za-z0-9_-]{8,100}$/.test(fileId)) {
          // W-01's endpoint rechecks the session, owner key and raster MIME
          // allowlist. Never accept a remote/data URL from the rich block.
          const image = node("img", "rich-image-preview");
          image.src = "/api/user/files/" + encodeURIComponent(fileId) + "/content";
          image.alt = String(part.alt || "图片附件").slice(0, 200);
          image.loading = "lazy";
          image.decoding = "async";
          image.referrerPolicy = "no-referrer";
          host.appendChild(image);
        } else {
          host.appendChild(node("div", "rich-attachment-fallback", "图片附件：缺少有效文件引用"));
        }
      } else if (type === "tool") {
        const states = new Set(["pending", "running", "completed", "failed"]);
        const state = states.has(part.status) ? part.status : "unknown";
        host.appendChild(node("div", "rich-tool-status", "工具状态：" + state));
      } else {
        host.appendChild(node("div", "rich-unsupported", "此内容暂不支持安全预览"));
      }
    });
    if (blocks.length > MAX_BLOCKS) {
      host.appendChild(node("div", "render-truncated", "[内容块过多，已截断]"));
    }
  }

  Object.defineProperty(window, "Chat2APISafeRenderer", {
    value: Object.freeze({renderMarkdown, renderRichBlocks}),
    writable: false, configurable: false,
  });
})();
