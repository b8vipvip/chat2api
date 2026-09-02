(() => {
  const KEY = "__CHAT2API_RICH_RESPONSE_V69__";
  if (globalThis[KEY]) return;

  const VERSION = 69;
  const MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024;
  const MAX_INLINE_IMAGE_TOTAL_BYTES = 6 * 1024 * 1024;
  const MAX_INLINE_IMAGES = 4;
  const ACTION_SELECTOR = [
    "button",
    "nav",
    "footer",
    "[aria-hidden='true']",
    "[data-testid*='copy']",
    "[data-testid*='feedback']",
    "[data-testid*='action']",
    "[data-testid*='thumb']",
  ].join(",");

  const visible = node => {
    if (!(node instanceof Element)) return false;
    try {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    } catch (_) {
      return false;
    }
  };

  const normalizeInline = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\u00a0/g, " ")
    .replace(/[\t\f\v ]+/g, " ");

  const normalizeOutput = value => String(value || "")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  function contentRoot(node) {
    if (!node) return null;
    const candidates = [];
    const seen = new Set();
    const add = candidate => {
      if (!(candidate instanceof Element) || seen.has(candidate) || !visible(candidate)) return;
      seen.add(candidate);
      candidates.push(candidate);
    };
    if (node instanceof Element && node.matches?.("[data-message-content],.markdown,[class*='markdown'],[class*='prose']")) add(node);
    if (node.querySelectorAll) {
      for (const selector of ["[data-message-content]", ".markdown", "[class*='markdown']", "[class*='prose']"]) {
        for (const candidate of node.querySelectorAll(selector)) add(candidate);
      }
    }
    if (!candidates.length) return node instanceof Element ? node : null;
    candidates.sort((left, right) => {
      const leftText = String(left.innerText || left.textContent || "").length;
      const rightText = String(right.innerText || right.textContent || "").length;
      return rightText - leftText;
    });
    return candidates[0];
  }

  function cleanClone(node) {
    const root = contentRoot(node);
    if (!root) return null;
    let clone = null;
    try { clone = root.cloneNode(true); } catch (_) { return null; }
    if (!clone?.querySelectorAll) return clone;
    clone.querySelectorAll(ACTION_SELECTOR).forEach(item => item.remove());
    for (const svg of clone.querySelectorAll("svg")) {
      const label = normalizeInline(svg.getAttribute("aria-label") || svg.querySelector("title")?.textContent || "").trim();
      if (label && svg.parentNode) svg.replaceWith(document.createTextNode(label));
      else svg.remove();
    }
    return clone;
  }

  const safeUrl = value => {
    const text = String(value || "").trim();
    if (/^(https?:|mailto:|data:image\/|blob:)/i.test(text)) return text;
    return "";
  };

  function languageOf(pre) {
    const code = pre?.querySelector?.("code") || pre;
    const values = [
      code?.getAttribute?.("data-language"),
      pre?.getAttribute?.("data-language"),
      code?.className,
      pre?.className,
    ].filter(Boolean).join(" ");
    const match = values.match(/(?:language-|lang-)([A-Za-z0-9_+.#-]+)/i);
    return match ? match[1] : "";
  }

  function inlineMarkdown(node) {
    if (!node) return "";
    if (node.nodeType === Node.TEXT_NODE) return normalizeInline(node.nodeValue || "");
    if (!(node instanceof Element)) return "";
    const tag = node.tagName.toLowerCase();
    if (tag === "br") return "\n";
    if (tag === "img") {
      const alt = normalizeInline(node.getAttribute("alt") || node.getAttribute("aria-label") || "图片").trim() || "图片";
      const src = safeUrl(node.getAttribute("src") || "");
      return src ? `![${alt.replace(/[\[\]]/g, "")}](${src})` : `[图片：${alt}]`;
    }
    if (tag === "a") {
      const label = inlineChildren(node).trim() || normalizeInline(node.getAttribute("aria-label") || "链接").trim() || "链接";
      const href = safeUrl(node.getAttribute("href") || "");
      return href ? `[${label}](${href})` : label;
    }
    if (tag === "code" && node.parentElement?.tagName?.toLowerCase() !== "pre") {
      const text = normalizeInline(node.textContent || "").trim();
      if (!text) return "";
      const fence = text.includes("`") ? "``" : "`";
      return `${fence}${text}${fence}`;
    }
    if (["strong", "b"].includes(tag)) {
      const text = inlineChildren(node).trim();
      return text ? `**${text}**` : "";
    }
    if (["em", "i"].includes(tag)) {
      const text = inlineChildren(node).trim();
      return text ? `*${text}*` : "";
    }
    if (["s", "del", "strike"].includes(tag)) {
      const text = inlineChildren(node).trim();
      return text ? `~~${text}~~` : "";
    }
    return inlineChildren(node);
  }

  function inlineChildren(node) {
    return [...(node?.childNodes || [])].map(inlineMarkdown).join("");
  }

  function listItemText(li, depth, ordered, index) {
    const direct = [...li.childNodes]
      .filter(child => !(child instanceof Element && ["ul", "ol"].includes(child.tagName.toLowerCase())))
      .map(inlineMarkdown)
      .join("")
      .replace(/\s*\n\s*/g, " ")
      .trim();
    const prefix = ordered ? `${index + 1}. ` : "- ";
    const indent = "  ".repeat(depth);
    const lines = [`${indent}${prefix}${direct}`.trimEnd()];
    for (const nested of [...li.children].filter(child => ["UL", "OL"].includes(child.tagName))) {
      const nestedText = blockMarkdown(nested, depth + 1);
      if (nestedText) lines.push(nestedText);
    }
    return lines.join("\n");
  }

  function tableMarkdown(table) {
    const rows = [...table.querySelectorAll("tr")].map(row => [...row.querySelectorAll(":scope > th, :scope > td")]
      .map(cell => normalizeInline(cell.innerText || cell.textContent || "").replace(/\|/g, "\\|").trim()));
    if (!rows.length || !rows[0].length) return "";
    const width = Math.max(...rows.map(row => row.length));
    const padded = rows.map(row => [...row, ...Array(Math.max(0, width - row.length)).fill("")]);
    const output = [`| ${padded[0].join(" | ")} |`, `| ${Array(width).fill("---").join(" | ")} |`];
    for (const row of padded.slice(1)) output.push(`| ${row.join(" | ")} |`);
    return output.join("\n");
  }

  function blockMarkdown(node, depth = 0) {
    if (!node) return "";
    if (node.nodeType === Node.TEXT_NODE) return normalizeInline(node.nodeValue || "").trim();
    if (!(node instanceof Element)) return "";
    const tag = node.tagName.toLowerCase();
    if (tag === "pre") {
      const text = String(node.querySelector("code")?.textContent || node.textContent || "").replace(/\r\n?/g, "\n").replace(/\n+$/, "");
      const language = languageOf(node);
      return `\`\`\`${language}\n${text}\n\`\`\``;
    }
    if (["ul", "ol"].includes(tag)) {
      const ordered = tag === "ol";
      return [...node.children].filter(child => child.tagName === "LI")
        .map((li, index) => listItemText(li, depth, ordered, index))
        .filter(Boolean)
        .join("\n");
    }
    if (tag === "table") return tableMarkdown(node);
    if (/^h[1-6]$/.test(tag)) {
      const level = Number(tag.slice(1));
      const text = inlineChildren(node).replace(/\s*\n\s*/g, " ").trim();
      return text ? `${"#".repeat(level)} ${text}` : "";
    }
    if (tag === "blockquote") {
      const text = blockChildren(node, depth).trim();
      return text ? text.split("\n").map(line => `> ${line}`).join("\n") : "";
    }
    if (tag === "hr") return "---";
    if (["p", "div", "section", "article", "main"].includes(tag)) {
      const hasBlockChildren = [...node.children].some(child => /^(P|DIV|SECTION|ARTICLE|PRE|UL|OL|TABLE|BLOCKQUOTE|H[1-6]|HR)$/.test(child.tagName));
      if (hasBlockChildren) return blockChildren(node, depth);
      return inlineChildren(node).replace(/[ \t]+\n/g, "\n").trim();
    }
    if (tag === "li") return listItemText(node, depth, false, 0);
    return inlineMarkdown(node).trim();
  }

  function blockChildren(node, depth = 0) {
    const chunks = [];
    for (const child of node.childNodes || []) {
      const value = child instanceof Element && /^(PRE|UL|OL|TABLE|BLOCKQUOTE|H[1-6]|HR|P|DIV|SECTION|ARTICLE)$/i.test(child.tagName)
        ? blockMarkdown(child, depth)
        : inlineMarkdown(child).trim();
      if (value) chunks.push(value);
    }
    return chunks.join("\n\n");
  }

  function extractMarkdown(node) {
    const clone = cleanClone(node);
    if (!clone) return "";
    return normalizeOutput(blockMarkdown(clone));
  }

  function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("Unable to read image"));
      reader.readAsDataURL(blob);
    });
  }

  async function inlineBlobImages(clone) {
    if (!clone?.querySelectorAll) return { inlined: 0, bytes: 0 };
    let inlined = 0;
    let total = 0;
    for (const img of [...clone.querySelectorAll("img")].slice(0, MAX_INLINE_IMAGES)) {
      const src = String(img.getAttribute("src") || "").trim();
      if (!src.startsWith("blob:")) continue;
      try {
        const response = await fetch(src);
        if (!response.ok) continue;
        const blob = await response.blob();
        if (!blob.size || blob.size > MAX_INLINE_IMAGE_BYTES || total + blob.size > MAX_INLINE_IMAGE_TOTAL_BYTES) continue;
        const dataUrl = await blobToDataUrl(blob);
        if (!dataUrl.startsWith("data:image/")) continue;
        img.setAttribute("src", dataUrl);
        total += blob.size;
        inlined += 1;
      } catch (_) {}
    }
    return { inlined, bytes: total };
  }

  async function captureFinalMarkdown(node) {
    const clone = cleanClone(node);
    if (!clone) return { text: "", image_inlined_count: 0, image_inlined_bytes: 0 };
    const imageStats = await inlineBlobImages(clone);
    return {
      text: normalizeOutput(blockMarkdown(clone)),
      image_inlined_count: imageStats.inlined,
      image_inlined_bytes: imageStats.bytes,
    };
  }

  globalThis[KEY] = Object.freeze({
    version: VERSION,
    format: "markdown",
    contentRoot,
    extractMarkdown,
    captureFinalMarkdown,
  });
})();
