(() => {
  const root = document.getElementById("view-playground");
  if (!root) return;
  document.getElementById("playgroundChatPanel")?.remove();

  const STORAGE_KEY = "chat2api.playground.chat.v1";
  const MAX_HISTORY_MESSAGES = 40;
  let sending = false;
  let messages = [];
  let lastRequest = null;

  const escHtml = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
  const trustedRecoveryBlobs = new Set();
  const RASTER_DATA_URL = /^data:image\/(?:png|jpeg|webp|gif);base64,[a-z0-9+/]+={0,2}$/i;
  const MAX_IMAGE_BASE64 = 4 * Math.ceil(4 * 1024 * 1024 / 3);
  const ownedMediaOriginalUrl = value => {
    try {
      const url = new URL(String(value || ""), location.href);
      if (url.origin !== location.origin || url.username || url.password || url.search || url.hash) return "";
      if (!/^\/api\/media\/med_[A-Za-z0-9]+\/[A-Za-z0-9_-]+(?:\/(?:thumbnail|original))?$/.test(url.pathname)) return "";
      return url.href.replace(/\/(?:thumbnail|original)$/, "");
    } catch (_) { return ""; }
  };
  const safeImageUrl = value => {
    const text = String(value || "").trim();
    if (text.startsWith("blob:") && trustedRecoveryBlobs.has(text)) return text;
    if (RASTER_DATA_URL.test(text) && text.slice(text.indexOf(",") + 1).length <= MAX_IMAGE_BASE64) return text;
    try {
      const url = new URL(text, location.href);
      const host = url.hostname.toLowerCase();
      // Only archive-owned, tokenized image variants are trusted on this origin.
      if (ownedMediaOriginalUrl(url.href)) return url.href;
      return url.protocol === "https:" && !url.username && !url.password
        && (["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(host)
          || [".oaiusercontent.com", ".oaistatic.com", ".openai.com", ".chatgpt.com"]
            .some(suffix => host.endsWith(suffix))) ? url.href : "";
    } catch (_) { return ""; }
  };
  const safeUrl = value => {
    const text = String(value || "").trim();
    return /^(https:\/\/|mailto:)/i.test(text) ? text : "";
  };
  function normalizeAssistantContent(content) {
    if (typeof content === "string") return content;
    if (!Array.isArray(content)) return "";
    return content.map(part => {
      if (!part || typeof part !== "object") return "";
      if (part.type === "text" || part.type === "output_text") return String(part.text || "");
      if (part.type === "image_url" || part.type === "output_image") {
        const value = typeof part.image_url === "object" ? part.image_url.url : part.image_url || part.url;
        const url = safeImageUrl(value);
        return url ? `![生成图片](${url})` : "";
      }
      return "";
    }).filter(Boolean).join("\n\n");
  }
  function imageResultMarkdown(payload) {
    const results = Array.isArray(payload?.data) ? payload.data : [];
    return results.slice(0, 4).map((item, index) => {
      const encoded = String(item?.b64_json || "");
      const mime = String(item?.mime_type || "image/png").toLowerCase();
      const source = encoded && ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(mime)
        ? `data:${mime};base64,${encoded}` : item?.url;
      const safe = safeImageUrl(source);
      if (!safe) return "";
      return `![生成图片 ${index + 1}](${safe})`;
    }).filter(Boolean).join("\n\n");
  }
  function isActualImageReply(markdown) {
    return /!\[[^\]]*\]\((?:data:image\/(?:png|jpeg|webp|gif);base64,|https:\/\/)/i.test(String(markdown || ""));
  }

  // Native MathML: keeps LaTeX data as the API format while rendering common
  // ChatGPT mathematics locally, without remote scripts or arbitrary HTML.
  // Unsupported TeX falls back to escaped source rather than a false formula.
  function mathMarkup(tex, display = false) {
    const input = String(tex || "").trim();
    if (!input || input.length > 8000) return null;
    let offset = 0;
    let failed = false;
    const esc = text => escHtml(String(text));
    const commands = {
      times:"×", cdot:"·", div:"÷", pm:"±", leq:"≤", le:"≤", geq:"≥", ge:"≥",
      neq:"≠", approx:"≈", to:"→", rightarrow:"→", leftarrow:"←",
      infty:"∞", sum:"∑", prod:"∏", int:"∫", alpha:"α", beta:"β",
      gamma:"γ", delta:"δ", theta:"θ", lambda:"λ", mu:"μ", pi:"π", sigma:"σ",
      Delta:"Δ", Sigma:"Σ", Omega:"Ω"
    };
    const skip = () => { while (/\s/.test(input[offset] || "") && offset < input.length) offset++; };
    function groupText() {
      skip();
      if (input[offset] !== "{") { failed = true; return ""; }
      offset++;
      const from = offset;
      let depth = 1;
      while (offset < input.length && depth > 0) {
        if (input[offset] === "{") depth++;
        if (input[offset] === "}") depth--;
        offset++;
      }
      if (depth) { failed = true; return ""; }
      return input.slice(from, offset - 1);
    }
    function atom() {
      skip();
      const c = input[offset];
      if (!c) return "";
      if (c === "{") {
        offset++;
        const children = parse("}");
        if (input[offset] !== "}") failed = true;
        else offset++;
        return "<mrow>" + children + "</mrow>";
      }
      if (c === "\\") {
        offset++;
        const match = input.slice(offset).match(/^[A-Za-z]+/);
        if (!match) {
          const literal = input[offset++] || "";
          if (/[{}%_$]/.test(literal)) return "<mo>" + esc(literal) + "</mo>";
          failed = true; return "";
        }
        const command = match[0];
        offset += command.length;
        if (command === "frac" || command === "dfrac" || command === "tfrac") {
          const top = atom(), bottom = atom();
          if (!top || !bottom) failed = true;
          return "<mfrac>" + top + bottom + "</mfrac>";
        }
        if (["text", "mathrm", "operatorname", "mathbf"].includes(command))
          return "<mtext>" + esc(groupText()) + "</mtext>";
        if (commands[command]) return "<mo>" + esc(commands[command]) + "</mo>";
        failed = true; return "";
      }
      if (/[0-9]/.test(c)) {
        const from = offset++;
        while (/[0-9.,]/.test(input[offset] || "") && offset < input.length) offset++;
        return "<mn>" + esc(input.slice(from, offset)) + "</mn>";
      }
      if (/[A-Za-z]/.test(c)) {
        const from = offset++;
        while (/[A-Za-z]/.test(input[offset] || "") && offset < input.length) offset++;
        return "<mi>" + esc(input.slice(from, offset)) + "</mi>";
      }
      if (/[\u3400-\u9fff]/.test(c)) {
        const from = offset++;
        while (/[\u3400-\u9fff]/.test(input[offset] || "") && offset < input.length) offset++;
        return "<mtext>" + esc(input.slice(from, offset)) + "</mtext>";
      }
      if (/[=+\-*\/()\[\],.:<>|%!?]/.test(c)) {
        offset++;
        return "<mo>" + esc(c) + "</mo>";
      }
      failed = true;
      offset++;
      return "";
    }
    function parse(end = "") {
      const parts = [];
      while (offset < input.length && !failed) {
        skip();
        if (end && input[offset] === end) break;
        if (!input[offset]) break;
        if (input[offset] === "^" || input[offset] === "_") {
          const type = input[offset++];
          const base = parts.pop();
          const script = atom();
          if (!base || !script) { failed = true; break; }
          parts.push(type === "^" ? "<msup>" + base + script + "</msup>"
            : "<msub>" + base + script + "</msub>");
        } else parts.push(atom());
      }
      return parts.join("");
    }
    const body = parse();
    if (failed || offset < input.length || !body) return null;
    return '<math xmlns="http://www.w3.org/1998/Math/MathML" display="' +
      (display ? "block" : "inline") + '"><mrow>' + body + '</mrow></math>';
  }

  function inlineMarkdown(value) {
    let source = String(value || "");
    const slots = [];
    const stash = html => {
      const token = `@@CHAT2API_RICH_${slots.length}@@`;
      slots.push(html);
      return token;
    };
    source = source.replace(/`([^`\n]+)`/g, (_all, code) => stash(`<code>${escHtml(code)}</code>`));
    source = source.replace(/\\\(([\s\S]*?)\\\)/g, (raw, math) => {
      const result = mathMarkup(math, false);
      return result ? stash('<span class="pgChatInlineMath">' + result + "</span>") : raw;
    });
    source = source.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_all, alt, url) => {
      const src = safeImageUrl(url);
      if (!src) return stash(`<span class="pgChatImageMissing">图片 · ${escHtml(alt || "未命名")}</span>`);
      const original = ownedMediaOriginalUrl(src);
      if (original) {
        // Render a small owned WebP, but clicking retrieves the real full-size
        // original. This does not change the OpenAI-compatible response body.
        return stash(`<a href="${escHtml(original)}" target="_blank" rel="noopener noreferrer" title="查看原图"><img class="pgChatImage" src="${escHtml(original + "/thumbnail")}" alt="${escHtml(alt || "图片")}" loading="lazy"></a>`);
      }
      return stash(`<img class="pgChatImage" src="${escHtml(src)}" alt="${escHtml(alt || "图片")}" loading="lazy">`);
    });
    source = source.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_all, label, url) => {
      const href = safeUrl(url);
      return href
        ? stash(`<a href="${escHtml(href)}" target="_blank" rel="noopener noreferrer">${escHtml(label)}</a>`)
        : escHtml(label);
    });
    source = source.replace(/\*\*([^*\n]+)\*\*/g, (_all, text) => stash(`<strong>${escHtml(text)}</strong>`));
    source = source.replace(/~~([^~\n]+)~~/g, (_all, text) => stash(`<del>${escHtml(text)}</del>`));
    source = source.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, (_all, prefix, text) => `${prefix}${stash(`<em>${escHtml(text)}</em>`)}`);
    let output = escHtml(source);
    slots.forEach((html, index) => { output = output.replaceAll(`@@CHAT2API_RICH_${index}@@`, html); });
    return output;
  }

  const isTableDivider = line => /^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/.test(line);
  const tableCells = line => String(line || "").trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map(cell => cell.replace(/\\\|/g, "|").trim());

  function renderMarkdown(markdown) {
    const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) { index += 1; continue; }

      if (/^\s*\\\[/.test(line)) {
        const content = [];
        let done = false;
        const first = line.replace(/^\s*\\\[/, "");
        let part = first;
        while (true) {
          const pos = part.indexOf("\\]");
          if (pos >= 0) { content.push(part.slice(0, pos)); done = true; break; }
          content.push(part);
          index++;
          if (index >= lines.length) break;
          part = lines[index];
        }
        if (done) {
          const math = mathMarkup(content.join("\n"), true);
          out.push(math
            ? '<div class="pgChatDisplayMath">' + math + "</div>"
            : "<pre><code>" + escHtml("\\[" + content.join("\n") + "\\]") + "</code></pre>");
          index++;
          continue;
        }
        out.push("<pre><code>" + escHtml("\\[" + content.join("\n")) + "</code></pre>");
        continue;
      }

      const fence = line.match(/^\s*```([^\s`]*)\s*$/);
      if (fence) {
        const language = fence[1] || "";
        const code = [];
        index += 1;
        while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
          code.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        out.push(`<div class="pgChatCode"><div class="pgChatCodeHead">${escHtml(language || "代码")}</div><pre><code>${escHtml(code.join("\n"))}</code></pre></div>`);
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        const level = heading[1].length;
        out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }

      if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
        out.push("<hr>");
        index += 1;
        continue;
      }

      if (line.includes("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
        const header = tableCells(line);
        index += 2;
        const rows = [];
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          rows.push(tableCells(lines[index]));
          index += 1;
        }
        out.push(`<div class="pgChatTableWrap"><table><thead><tr>${header.map(cell => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }

      if (/^\s*>\s?/.test(line)) {
        const quote = [];
        while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
          quote.push(lines[index].replace(/^\s*>\s?/, ""));
          index += 1;
        }
        out.push(`<blockquote>${quote.map(inlineMarkdown).join("<br>")}</blockquote>`);
        continue;
      }

      if (/^\s*[-+*]\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^\s*[-+*]\s+/.test(lines[index])) {
          const match = lines[index].match(/^(\s*)[-+*]\s+(.+)$/);
          items.push({depth: Math.floor((match?.[1]?.length || 0) / 2), text: match?.[2] || ""});
          index += 1;
        }
        out.push(`<ul>${items.map(item => `<li style="margin-left:${Math.min(item.depth, 6) * 16}px">${inlineMarkdown(item.text)}</li>`).join("")}</ul>`);
        continue;
      }

      if (/^\s*\d+[.)]\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
          const match = lines[index].match(/^(\s*)\d+[.)]\s+(.+)$/);
          items.push({depth: Math.floor((match?.[1]?.length || 0) / 2), text: match?.[2] || ""});
          index += 1;
        }
        out.push(`<ol>${items.map(item => `<li style="margin-left:${Math.min(item.depth, 6) * 16}px">${inlineMarkdown(item.text)}</li>`).join("")}</ol>`);
        continue;
      }

      const paragraph = [line];
      index += 1;
      while (index < lines.length && lines[index].trim()) {
        const next = lines[index];
        if (/^\s*```/.test(next) || /^(#{1,6})\s+/.test(next) || /^\s*>\s?/.test(next)
          || /^\s*[-+*]\s+/.test(next) || /^\s*\d+[.)]\s+/.test(next)
          || /^\s*(?:---+|___+|\*\*\*+)\s*$/.test(next)) break;
        if (next.includes("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) break;
        paragraph.push(next);
        index += 1;
      }
      out.push(`<p>${paragraph.map(inlineMarkdown).join("<br>")}</p>`);
    }
    return out.join("") || `<p>${inlineMarkdown(markdown)}</p>`;
  }

  const style = document.createElement("style");
  style.dataset.chat2apiPlaygroundChat = "69";
  style.textContent = `
    .pgChatDisplayMath{overflow-x:auto;text-align:center;padding:9px 0;max-width:100%}.pgChatDisplayMath math{font-size:1.12em;margin:auto}.pgChatInlineMath{display:inline-block;vertical-align:middle}.pgChatInlineMath math{vertical-align:middle}
    .pgChatPanel{margin-bottom:14px;overflow:hidden}.pgChatHead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px}.pgChatHead h2{margin:0 0 4px}
    .pgChatToolbar{display:grid;grid-template-columns:minmax(180px,.8fr) 150px minmax(260px,1.2fr) minmax(220px,1fr);gap:9px;margin-bottom:12px}.pgChatToolbar label{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}.pgChatKeyBox{display:grid;grid-template-columns:1fr;gap:7px}
    .pgChatMessages{height:min(52vh,560px);min-height:360px;overflow:auto;border:1px solid var(--line);border-radius:13px;background:#08111f;padding:18px;display:flex;flex-direction:column;gap:14px}.pgChatEmpty{margin:auto;color:var(--muted);text-align:center;max-width:520px}
    .pgChatRow{display:flex;flex-direction:column;max-width:min(86%,920px);gap:5px}.pgChatRow.user{align-self:flex-end;align-items:flex-end}.pgChatRow.assistant{align-self:flex-start;align-items:flex-start}.pgChatRole{font-size:11px;color:var(--muted)}
    .pgChatBubble{border:1px solid var(--line);border-radius:14px;padding:11px 13px;white-space:pre-wrap;word-break:break-word;line-height:1.65;background:#111d31;min-width:52px}.pgChatRow.user .pgChatBubble{background:#17375e;border-color:#28517f}.pgChatBubble.pending{color:var(--muted)}.pgChatBubble.error{border-color:#70323c;background:#351b22;color:#ffd5d9}
    .pgChatBubble.rich{white-space:normal;min-width:min(300px,100%)}.pgChatBubble.rich p{margin:.35em 0 .75em}.pgChatBubble.rich p:first-child{margin-top:0}.pgChatBubble.rich p:last-child{margin-bottom:0}.pgChatBubble.rich h1,.pgChatBubble.rich h2,.pgChatBubble.rich h3,.pgChatBubble.rich h4,.pgChatBubble.rich h5,.pgChatBubble.rich h6{margin:.8em 0 .4em;line-height:1.3}.pgChatBubble.rich ul,.pgChatBubble.rich ol{padding-left:24px;margin:.45em 0}.pgChatBubble.rich li{margin:.18em 0}.pgChatBubble.rich blockquote{margin:.6em 0;padding:.4em .8em;border-left:3px solid #56749d;background:#0b1728;color:#cbd8ea}.pgChatBubble.rich hr{border:0;border-top:1px solid var(--line);margin:12px 0}.pgChatBubble.rich a{color:#8bb8ff}.pgChatBubble.rich code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;background:#07101e;border:1px solid #24344d;border-radius:5px;padding:1px 4px}.pgChatCode{margin:.65em 0;border:1px solid #263a57;border-radius:10px;overflow:hidden;background:#050b14}.pgChatCodeHead{padding:6px 10px;border-bottom:1px solid #263a57;color:#9eb1ce;font-size:11px}.pgChatCode pre{margin:0;padding:12px;overflow:auto;white-space:pre}.pgChatCode pre code{border:0;background:transparent;padding:0}.pgChatTableWrap{overflow:auto;margin:.65em 0}.pgChatTableWrap table{border-collapse:collapse;min-width:360px}.pgChatTableWrap th,.pgChatTableWrap td{border:1px solid #2b3c58;padding:7px 9px;position:static;background:transparent}.pgChatImage{display:block;max-width:min(100%,720px);max-height:520px;object-fit:contain;border-radius:10px;margin:8px 0;border:1px solid #293d59}.pgChatImageMissing{display:inline-block;padding:3px 7px;border-radius:8px;background:#13233a;color:#adc7eb}
    .pgChatMode{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:10px}.pgChatMode label{font-size:12px;color:var(--muted)}.pgChatMode select{max-width:100%;min-width:min(100%,420px)}.pgChatMode span{font-size:11px;color:var(--muted)}.pgChatMeta{font-size:11px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}.pgChatActions{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:8px}.pgChatCopy{border:0;background:transparent;color:var(--muted);padding:2px 5px;border-radius:6px;cursor:pointer;line-height:1}.pgChatCopy:hover{background:#17263d;color:var(--text)}.pgChatAttachment{font-size:11px;color:#adc7eb;border:1px solid #294363;background:#0d1b2e;border-radius:999px;padding:2px 8px}.pgChatComposer{display:grid;grid-template-columns:1fr auto;gap:9px;margin-top:10px;align-items:end}.pgChatComposer textarea{width:100%;min-height:76px;max-height:220px;resize:vertical;line-height:1.55}.pgChatComposer button{height:42px;min-width:94px}.pgChatAttachHint{font-size:11px;color:var(--muted);margin-top:6px;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}.pgChatBadge{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:2px 8px;background:#0d1728}
    @media(max-width:1180px){.pgChatToolbar{grid-template-columns:1fr 1fr}.pgChatMessages{height:480px}}@media(max-width:760px){.pgChatToolbar{grid-template-columns:1fr}.pgChatMessages{height:420px;min-height:300px;padding:12px}.pgChatRow{max-width:94%}.pgChatComposer{grid-template-columns:1fr}.pgChatComposer button{width:100%}}
  `;
  document.head.appendChild(style);

  const panel = document.createElement("div");
  panel.id = "playgroundChatPanel";
  panel.className = "panel pgChatPanel";
  panel.innerHTML = `
    <div class="pgChatHead"><div><h2>聊天对话</h2><div class="muted">通过真实 chat2api 请求与 ChatGPT 多轮对话。此模式等待完整回答后返回，以保留段落、列表、代码、表格、链接、表情和可传输图片等原始语义格式。</div></div><button class="action" id="pgChatNew">新建对话</button></div>
    <div class="pgChatToolbar">
      <div><label>模型</label><select id="pgChatModel"><option value="default" selected>默认（页面当前模型）</option><option value="gpt-5.6-sol">gpt-5.6-sol</option><option value="gpt-5.5-mini">gpt-5.5-mini</option></select></div>
      <div><label>推理强度</label><select id="pgChatReasoning"><option value="default" selected>默认（不检查/不切换）</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></div>
      <div><label>业务 API Key</label><div class="pgChatKeyBox"><select id="pgChatKey"><option value="">正在读取可用业务 Key…</option></select><input id="pgChatKeyInput" type="password" autocomplete="off" placeholder="粘贴业务 API Key（可选，优先使用）"></div></div>
      <div><label>本轮附件（最多 4 个）</label><input id="pgChatFiles" type="file" multiple></div>
    </div>
    <div class="pgChatMode"><label for="pgChatMode">执行方式</label><select id="pgChatMode"><option value="chat">对话（选择的文本模型，允许模型自主调用图片工具）</option><option value="image">图片生成（使用 gpt-image 专用接口，保证返回图片数据）</option></select><span id="pgChatModeHint">对话模式只展示上游实际返回的内容；PIL 代码和 /mnt/data 路径不是生成图片。</span></div>
    <div class="pgChatMessages" id="pgChatMessages"><div class="pgChatEmpty">输入消息即可开始。支持连续追问；后续请求会携带本窗口中的文本对话历史。</div></div>
    <div class="pgChatComposer"><textarea id="pgChatInput" placeholder="输入测试消息。Enter 发送，Shift+Enter 换行。"></textarea><button class="action good" id="pgChatSend">发送</button></div>
    <div class="pgChatAttachHint"><span>附件只绑定当前一轮请求；完整 Markdown 语义会写入后续文本上下文。ChatGPT 网页自身的复制/点赞等操作图标不会当作模型回答传输。</span><span id="pgChatLast">尚未发送</span></div>`;
  root.insertBefore(panel, root.firstElementChild);

  const $chat = id => document.getElementById(id);

  function saveHistory() {
    // Never put a multi-megabyte image base64 or transient blob URL in sessionStorage.
    try {
      const safe = messages.slice(-MAX_HISTORY_MESSAGES).map(message => ({
        ...message,
        content: String(message.content || "").replace(
          /!\[[^\]\r\n]*\]\(data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+\)/ig,
          "【生成图片已归档，请到请求记录查看】"
        ).replace(/!\[[^\]\r\n]*\]\(blob:[^)]+\)/ig,
          "【生成图片已归档，请到请求记录查看】").slice(0, 50000),
      }));
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(safe));
    } catch (_) {}
  }
  function loadHistory() {
    try {
      const value = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
      messages = Array.isArray(value) ? value.filter(item => item && ["user", "assistant"].includes(item.role) && typeof item.content === "string")
        .map(item => ({...item, attachment_names: Array.isArray(item.attachment_names) ? item.attachment_names.map(String).slice(0, 4) : [], include_in_context: item.include_in_context !== false})).slice(-MAX_HISTORY_MESSAGES) : [];
    } catch (_) { messages = []; }
  }
  function formatMessageTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    return new Intl.DateTimeFormat("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(date);
  }
  async function copyMessage(index, button) {
    const item = messages[index];
    if (!item) return;
    const value = String(item.content || "");
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
      else {
        const area = document.createElement("textarea");
        area.value = value; area.style.position = "fixed"; area.style.opacity = "0";
        document.body.appendChild(area); area.select(); document.execCommand("copy"); area.remove();
      }
      const before = button.textContent;
      button.textContent = "✓"; button.title = "已复制";
      setTimeout(() => { button.textContent = before; button.title = "复制消息"; }, 900);
    } catch (_) { button.title = "复制失败"; }
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
      const richClass = item.role === "assistant" && !item.error ? " rich" : "";
      const content = item.role === "assistant" && !item.error ? renderMarkdown(item.content) : escHtml(item.content);
      const messageTime = formatMessageTime(item.created_at);
      return `<div class="pgChatRow ${item.role}" data-chat-index="${index}"><div class="pgChatRole">${item.role === "user" ? "你" : "模型"}</div><div class="pgChatBubble${errorClass}${richClass}">${content}</div>${(attachments || item.meta) ? `<div class="pgChatMeta">${attachments}${item.meta ? `<span>${escHtml(item.meta.request_id || "")}</span><span>${escHtml(item.meta.model || "")}</span><span>${item.meta.total_ms != null ? `${Math.round(item.meta.total_ms)} ms` : ""}</span>` : ""}</div>` : ""}<div class="pgChatActions"><span>${escHtml(messageTime)}</span><button type="button" class="pgChatCopy" data-copy-index="${index}" title="复制消息" aria-label="复制消息">⧉</button></div></div>`;
    }).join("");
    box.scrollTop = box.scrollHeight;
  }
  function addPendingAssistant() {
    const box = $chat("pgChatMessages");
    const row = document.createElement("div");
    row.className = "pgChatRow assistant";
    row.id = "pgChatPending";
    row.innerHTML = '<div class="pgChatRole">模型</div><div class="pgChatBubble pending">正在等待 ChatGPT 完整回答…</div><div class="pgChatMeta"></div>';
    box.appendChild(row); box.scrollTop = box.scrollHeight;
  }

  async function syncModels() {
    const source = document.getElementById("testModel"), target = $chat("pgChatModel");
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
    const target = $chat("pgChatKey"), current = target.value;
    try {
      const data = await api("/api/admin/keys");
      const options = ['<option value="">请选择可用业务 API Key</option>'];
      let firstUsable = "";
      for (const item of data.data || []) {
        if (!item.managed) continue;
        const usable = item.enabled && !item.expired && !item.revoked_at && item.secret_recoverable;
        const stateLabel = item.revoked_at ? "已撤销" : item.expired ? "已过期" : !item.enabled ? "已停用" : !item.secret_recoverable ? "旧 Key 无法恢复" : "可用";
        if (usable && !firstUsable) firstUsable = String(item.key_id || "");
        options.push(`<option value="${escHtml(item.key_id)}" data-key-name="${escHtml(item.name || item.key_id)}" ${usable ? "" : "disabled"}>${escHtml(item.name)} · ${escHtml(item.prefix)} · ${escHtml(stateLabel)}</option>`);
      }
      target.innerHTML = options.join("");
      if (current && [...target.options].some(option => option.value === current && !option.disabled)) target.value = current;
      else if (firstUsable) target.value = firstUsable;
    } catch (_) {}
  }
  function updateReasoningState() {
    const mini = $chat("pgChatModel").value === "gpt-5.5-mini";
    const imageMode = $chat("pgChatMode")?.value === "image";
    $chat("pgChatReasoning").disabled = mini || imageMode;
    $chat("pgChatReasoning").title = imageMode ? "图片生成使用 gpt-image 专用接口" : mini ? "gpt-5.5-mini 使用自动推理强度" : "";
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
      const value = await api("/v1/files", {method:"POST", key:token, body:{filename:file.name,mime_type:file.type||"application/octet-stream",data_base64:dataBase64,purpose:"chat2api"}});
      uploaded.push({file_id:value.id, filename:file.name});
    }
    return uploaded;
  }
  async function cleanupFiles(uploaded, token) {
    for (const item of uploaded) { try { await api(`/v1/files/${encodeURIComponent(item.file_id)}`, {method:"DELETE", key:token}); } catch (_) {} }
  }
  function normalizedHistory() {
    return messages.filter(item => item.include_in_context !== false).slice(-MAX_HISTORY_MESSAGES).map(item => ({role:item.role, content:item.content}));
  }
  async function recoverAuthoritativeResult(requestId, {waitForTerminal=false} = {}) {
    if (!/^((imgreq|req)_[a-z0-9-]{12,110})$/i.test(requestId)) return null;
    let outcome = null, observed = false, consecutiveErrors = 0;
    // A disconnected HTTP body does not cancel the Worker-owned request.
    // Poll only the EXISTING authenticated request-id outcome; never submit
    // another prompt or infer success from a timeout, DOM hint or SSE closure.
    // The longer loop is presentation-only and stops as soon as Broker
    // persisted its actual terminal. If it runs out, return UNCONFIRMED, not
    // an invented error/success terminal.
    const maxChecks = waitForTerminal ? 300 : 4;
    for (let attempt = 0; attempt < maxChecks; attempt++) {
      try {
        outcome = await api("/api/admin/playground/requests/" +
          encodeURIComponent(requestId) + "/outcome?full_text=1");
        if (outcome?.status) {
          observed = true;
          consecutiveErrors = 0;
          if (!["running","queued","pending"].includes(outcome.status)) break;
          if (waitForTerminal && (attempt === 0 || attempt % 8 === 0))
            status("浏览器连接已中断；服务端请求 " + requestId +
              " 仍在执行，正在等待实际 Worker 终态", "warn");
        }
      } catch (_) {
        consecutiveErrors++;
        // A request rejected before reaching the Broker has no durable ID.
        // Do not spend twenty minutes polling an unregistered request.
        if (!observed && attempt >= 3) return null;
        if (consecutiveErrors >= 12) break;
      }
      if (attempt + 1 < maxChecks)
        await new Promise(resolve => setTimeout(resolve, waitForTerminal ? 4000 : 400));
    }
    if (!outcome || !observed) return null;
    if (["running","queued","pending"].includes(outcome.status))
      return {pending:true, requestId, status:outcome.status};
    if (outcome.status !== "completed")
      return {terminalStatus:outcome.status, error:String(outcome.error || ""),requestId};
    const full = typeof outcome.response_text === "string"
      ? outcome.response_text : String(outcome.response_text_preview || "");
    let markdown = full || "服务端确认已完成，但未返回可恢复的文字内容";
    let downloaded = 0;
    for (const media of (outcome.response_media || []).slice(0, 4)) {
      const fileId = String(media?.file_id || "");
      if (!/^file_[a-z0-9_-]{8,90}$/i.test(fileId)) continue;
      try {
        const response = await fetch("/api/admin/requests/" +
          encodeURIComponent(requestId) + "/media/" + encodeURIComponent(fileId), {
          headers: {Authorization: "Bearer " + key()}, cache: "no-store",
        });
        if (!response.ok) continue;
        const blob = await response.blob();
        if (!["image/png","image/jpeg","image/webp","image/gif"].includes(blob.type)
            || blob.size > 4 * 1024 * 1024) continue;
        const url = URL.createObjectURL(blob);
        trustedRecoveryBlobs.add(url);
        markdown += "\n\n![生成图片](" + url + ")";
        downloaded++;
      } catch (_) { /* preserve completion status even without preview */ }
    }
    return {content:markdown,imageCount:downloaded,requestId:outcome.request_id,
      terminalStatus:"completed",textTruncated:outcome.response_text_truncated === true};
  }

  async function recordChatTurn({requestId, model, credential, text, statusValue, totalMs, firstTokenMs, responseChars, attachmentsCount, error}) {
    try {
      await api("/api/admin/playground/chat-records", {method:"POST", body:{request_id:requestId,model,api_key_id:credential?.key_id||null,api_key_name:credential?.label||($chat("pgChatKey").selectedOptions?.[0]?.dataset?.keyName||"未解析"),status:statusValue,duration_ms:totalMs,first_token_ms:firstTokenMs,prompt:text,response_chars:responseChars,attachments_count:attachmentsCount,error:error||null}});
      if (typeof loadTests === "function") await loadTests();
    } catch (recordError) { console.warn("Playground chat record persistence failed", recordError); }
  }

  async function sendMessage() {
    if (sending) return;
    const input = $chat("pgChatInput"), text = input.value.trim();
    if (!text) return;
    if (!$chat("pgChatModel")?.value && $chat("pgChatMode").value !== "image") return status("没有已验证且可路由的在线 Worker 文本模型，请在模型管理查看发现错误", "bad");
    if (typeof api !== "function" || typeof fileToBase64 !== "function") return status("测试场脚本尚未初始化，请刷新页面后重试", "bad");
    sending = true; $chat("pgChatSend").disabled = true; $chat("pgChatSend").textContent = "生成中"; input.disabled = true;
    let uploaded = [], credential = null, assistantText = "", firstTokenMs = null, diagnostics = null;
    const userFiles = [...$chat("pgChatFiles").files].slice(0, 4), userIndex = messages.length;
    messages.push({role:"user",content:text,attachment_names:userFiles.map(file=>file.name),include_in_context:true,created_at:new Date().toISOString()}); input.value=""; renderMessages(); addPendingAssistant();
    const requestId = "req_" + (globalThis.crypto?.randomUUID ? crypto.randomUUID().replaceAll("-", "") : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`);
    const imageMode = $chat("pgChatMode").value === "image";
    const model = imageMode ? "gpt-image" : $chat("pgChatModel").value;
    const started = performance.now();
    try {
      credential = await resolveCredential();
      uploaded = await uploadFiles(userFiles, credential.token);
      // A generated-image file can serve as the reference for the next image
      // edit turn, but only when the managed API Key is unchanged. Never
      // persist a pasted secret or silently send the previous image to a new Key.
      const previousImage = imageMode && credential.key_id && messages.slice(0,-1).reverse()
        .find(item => item.role === "assistant" && item.meta?.generated_file_id
          && item.meta?.generated_key_id === credential.key_id);
      const referenceFiles = uploaded.map(item => ({file_id:item.file_id}));
      if (imageMode && previousImage && referenceFiles.length < 4)
        referenceFiles.push({file_id: previousImage.meta.generated_file_id});
      const body = imageMode
        ? {model:"gpt-image",prompt:text,attachments:referenceFiles,response_format:"b64_json",n:1,timeout:600}
        // Omit an explicit chat timeout, preserving broker-owned adaptive
        // 300s text / 600s verified media handoff without a second clock.
        : {model,messages:normalizedHistory(),prompt_mode:"full",attachments:referenceFiles,stream:false};
      if (!imageMode && model !== "gpt-5.5-mini" && $chat("pgChatReasoning").value && $chat("pgChatReasoning").value !== "default") body.reasoning_effort = $chat("pgChatReasoning").value;
      const response = await fetch(imageMode ? "/v1/images/generations" : "/v1/chat/completions", {
        method:"POST",headers:{...headers(credential.token),"Content-Type":"application/json",
          "X-Chat2API-Request-ID": imageMode ? requestId.replace(/^req_/, "imgreq_") : requestId},
        body:JSON.stringify(body),
      });
      if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
      const payload = await response.json();
      assistantText = imageMode ? imageResultMarkdown(payload)
        : normalizeAssistantContent(payload.choices?.[0]?.message?.content);
      diagnostics = payload.chat2api || null;
      if (imageMode && !isActualImageReply(assistantText))
        throw new Error("图片接口未返回可展示的真实图片字节或可信图片链接，不能把文本代码标为成功");
      firstTokenMs = Number(diagnostics?.timings?.first_token_ms ?? NaN);
      if (!Number.isFinite(firstTokenMs)) firstTokenMs = null;
      if (!assistantText.trim()) throw new Error("请求完成，但没有捕获到可交付的模型回复");
      // Keep the completed response visually accessible without serializing a
      // multi-MB data URI into the session or forcing the browser to render it.
      if (assistantText.length > 500000 && assistantText.includes("data:image/")) {
        const compact = await recoverAuthoritativeResult(
          imageMode ? requestId.replace(/^req_/, "imgreq_") : requestId);
        if (compact) assistantText = compact.content;
      }
      const totalMs = performance.now() - started;
      document.getElementById("pgChatPending")?.remove();
      const responseType = isActualImageReply(assistantText) ? (imageMode ? "image" : "mixed") : "text";
      const actualRequestId = String(diagnostics?.request_id || (imageMode ? requestId.replace(/^req_/, "imgreq_") : requestId));
      const generatedFile = imageMode && credential.key_id
        ? (Array.isArray(diagnostics?.media_files) ? diagnostics.media_files[0]?.file_id : null) : null;
      messages.push({role:"assistant",content:assistantText,include_in_context:!imageMode,
        created_at:new Date().toISOString(),meta:{request_id:actualRequestId,model,total_ms:totalMs,
          first_token_ms:firstTokenMs,response_format:responseType,
          generated_file_id:generatedFile||null,generated_key_id:generatedFile?credential.key_id:null}});
      lastRequest = {request_id:actualRequestId,model,total_ms:totalMs,first_token_ms:firstTokenMs,diagnostics}; saveHistory(); renderMessages();
      $chat("pgChatLast").innerHTML = `<span class="pgChatBadge">${escHtml(actualRequestId)}</span> <span class="pgChatBadge">${Math.round(totalMs)} ms</span>`; status(`聊天请求完成：${requestId}`, "ok"); $chat("pgChatFiles").value="";
      await recordChatTurn({requestId:actualRequestId,model,credential,text,statusValue:"passed",totalMs,firstTokenMs,responseChars:assistantText.length,attachmentsCount:userFiles.length,error:null});
    } catch (error) {
      const recoveryId = imageMode ? requestId.replace(/^req_/, "imgreq_") : requestId;
      const recovered = await recoverAuthoritativeResult(recoveryId, {waitForTerminal:true}).catch(() => null);
      if (recovered?.terminalStatus === "completed") {
        const totalMs = performance.now() - started;
        document.getElementById("pgChatPending")?.remove();
        messages.push({role:"assistant",content:recovered.content,
          include_in_context:!imageMode && !recovered.imageCount && !recovered.textTruncated,
          created_at:new Date().toISOString(),meta:{request_id:recoveryId,model,total_ms:totalMs,
          recovered_from:"server-request-terminal-v170",response_format:recovered.imageCount?"image":"text"}});
        lastRequest={request_id:recoveryId,model,total_ms:totalMs,recovered:true,
          text_truncated:recovered.textTruncated};
        saveHistory(); renderMessages();
        status(recovered.textTruncated
          ? "服务端已完成；恢复正文超过 500,000 字符，已明确截断且不会用于后续上下文"
          : "服务端已完成；浏览器连接中断后已恢复完整文本及可用媒体", "ok");
        await recordChatTurn({requestId:recoveryId,model,credential,text,statusValue:"passed",
          totalMs,firstTokenMs,responseChars:recovered.content.length,attachmentsCount:userFiles.length,error:null});
      } else if (recovered?.pending) {
        // Never label a live Worker request failed because its HTTP stream
        // disappeared or the presentation-only observation window elapsed.
        const totalMs = performance.now() - started;
        document.getElementById("pgChatPending")?.remove();
        if (messages[userIndex]) messages[userIndex].include_in_context=false;
        messages.push({role:"assistant",content:"浏览器连接已中断，但服务端请求仍未结束。请求 ID：" + recoveryId +
          "。可在请求记录查看该请求的真实终态；此处不判定成功或失败。",
          include_in_context:false,created_at:new Date().toISOString(),
          meta:{request_id:recoveryId,model,total_ms:totalMs,recovery_pending:true}});
        lastRequest={request_id:recoveryId,model,total_ms:totalMs,pending:true};
        saveHistory();renderMessages();status("服务端仍在运行，等待后台真实终态", "warn");
        await recordChatTurn({requestId:recoveryId,model,credential,text,statusValue:"running",
          totalMs,firstTokenMs,responseChars:0,attachmentsCount:userFiles.length,error:null});
      } else {
      const totalMs = performance.now() - started;
      const errorText = String(recovered?.error || error?.message || error);
      const failedStatus = recovered?.terminalStatus === "cancelled" ? "cancelled"
        : /(stalled|watchdog|timed out|timeout|no observable|no response progress)/i.test(errorText) ? "stalled" : "failed";
      document.getElementById("pgChatPending")?.remove(); if (messages[userIndex]) messages[userIndex].include_in_context=false;
      messages.push({role:"assistant",content:`请求失败：${errorText}`,include_in_context:false,error:true,created_at:new Date().toISOString(),meta:{request_id:requestId,model,total_ms:totalMs}}); lastRequest={request_id:requestId,model,total_ms:totalMs,error:errorText,diagnostics}; saveHistory(); renderMessages(); status(`聊天请求失败：${errorText}`,"bad");
      await recordChatTurn({requestId:recoveryId,model,credential,text,statusValue:failedStatus,totalMs,firstTokenMs,responseChars:assistantText.length,attachmentsCount:userFiles.length,error:errorText});
      }
    } finally {
      if (credential && uploaded.length) await cleanupFiles(uploaded, credential.token);
      sending=false; $chat("pgChatSend").disabled=false; $chat("pgChatSend").textContent="发送"; input.disabled=false; input.focus();
    }
  }

  async function newConversation() {
    if (sending && !confirm("当前请求仍在生成。只清空本地显示不会取消服务器请求，仍要继续吗？")) return;
    messages=[]; lastRequest=null; saveHistory(); renderMessages(); $chat("pgChatFiles").value=""; $chat("pgChatLast").textContent="新对话已创建"; $chat("pgChatInput").focus();
  }

  $chat("pgChatSend").addEventListener("click", sendMessage); $chat("pgChatNew").addEventListener("click", newConversation); $chat("pgChatMessages").addEventListener("click", event => { const button = event.target.closest?.("[data-copy-index]"); if (!button) return; copyMessage(Number(button.dataset.copyIndex), button); });
  $chat("pgChatInput").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); } });
  $chat("pgChatMode").addEventListener("change", updateReasoningState);
  $chat("pgChatModel").addEventListener("change", updateReasoningState); $chat("pgChatModel").addEventListener("focus", syncModels); $chat("pgChatKey").addEventListener("focus", syncKeys);
  loadHistory(); renderMessages(); updateReasoningState(); setTimeout(syncModels,100); setTimeout(syncKeys,180); setTimeout(syncModels,900);
  globalThis.__CHAT2API_PLAYGROUND_CHAT_V69__ = Object.freeze({
    version:206, renderMarkdown, mathMarkup, imageResultMarkdown, normalizeAssistantContent, safeImageUrl,
  });
})();
