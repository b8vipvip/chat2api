(() => {
  const KEY = "__CHAT2API_RICH_RESPONSE_V69__";
  if (globalThis[KEY]) return;

  const VERSION = 69;
  const MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024;
  const MAX_INLINE_IMAGE_TOTAL_BYTES = 6 * 1024 * 1024;
  const MAX_INLINE_IMAGES = 4;
  const RASTER_IMAGE = /^image\/(?:png|jpeg|webp|gif)$/i;
  const RASTER_DATA_URI = /^data:image\/(?:png|jpeg|webp|gif);base64,[a-z0-9+/=]+$/i;
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

  function candidateImage(img) {
    if (!(img instanceof Element) || !visible(img)) return false;
    const box = img.getBoundingClientRect();
    const src = String(img.currentSrc || img.getAttribute("src") || "").trim();
    // A large preview inside the assistant's own turn is evidence of a media
    // result. Exclude profile avatars, thumbnails and unrelated page images.
    return box.width >= 96 && box.height >= 96
      && /^(blob:|data:image\/|https:\/\/)/i.test(src);
  }

  function hasCandidateImage(node) {
    return Boolean(node instanceof Element
      && [...node.querySelectorAll("img")].some(candidateImage));
  }

  // The redesigned ChatGPT image generator can render a finished preview
  // outside [data-message-author-role='assistant']. Restrict discovery to the
  // active chat's main content, never the sidebar, modals, uploads or menus.
  const CHAT_MEDIA_SELECTOR = [
    "main img", "[role='main'] img", "#thread img",
    "[data-testid^='conversation-turn'] img",
  ].join(",");

  function candidateSource(img) {
    return String(img?.currentSrc || img?.getAttribute?.("src") || "").trim();
  }

  function candidateMediaKey(img) {
    const src = candidateSource(img);
    // Do not include the URL in diagnostics; these can contain signed tokens.
    return src && /^(blob:|data:image\\/|https:\\/\\/)/i.test(src) ? src : "";
  }

  function pageMediaCandidates() {
    return [...document.querySelectorAll(CHAT_MEDIA_SELECTOR)]
      .filter(img => {
        if (!candidateImage(img)) return false;
        if (img.closest("aside,nav,header,footer,form,[role='dialog'],[role='menu'],[data-message-author-role='user']")) return false;
        const turn = img.closest("article[data-testid^='conversation-turn'],[data-testid^='conversation-turn'],article[data-message-id]");
        if (turn && (turn.getAttribute("data-message-author-role") === "user"
            || turn.querySelector("[data-message-author-role='user']"))) return false;
        return Boolean(candidateMediaKey(img));
      });
  }

  function snapshotPageMedia() {
    const images = pageMediaCandidates();
    return { keys: new Set(images.map(candidateMediaKey)), nodes: new Set(images), count: images.length };
  }

  function locateNewPageMedia(baseline, userAnchor = null) {
    const images = pageMediaCandidates();
    const oldKeys = baseline?.keys || new Set();
    const fresh = images.filter(img => !oldKeys.has(candidateMediaKey(img)));
    const afterUser = userAnchor instanceof Element
      ? fresh.filter(img => {
          if (!userAnchor.isConnected || !img.isConnected || userAnchor === img) return false;
          return Boolean(userAnchor.compareDocumentPosition(img) & Node.DOCUMENT_POSITION_FOLLOWING);
        })
      : [];
    // An accepted new image URL is the fallback evidence when ChatGPT does
    // not expose an assistant role/turn node. It must be new since pre-send,
    // inside the same tab's main conversation, and not a user upload.
    const selected = afterUser.length ? afterUser : fresh;
    return {
      images: selected.slice(-MAX_INLINE_IMAGES),
      candidate_count_document: images.length,
      new_image_candidate_count: fresh.length,
      after_user_count: afterUser.length,
      anchor: afterUser.length ? "after-current-user" : fresh.length ? "fresh-page-media" : "none",
      baseline_image_count: Number(baseline?.count || 0),
    };
  }

  async function capturePageImages(images) {
    if (!Array.isArray(images) || !images.length) return {
      text: "", image_inlined_count: 0, image_embedded_count: 0,
      image_inlined_bytes: 0,
    };
    const wrapper = document.createElement("div");
    for (const image of images.slice(-MAX_INLINE_IMAGES)) {
      if (!candidateImage(image)) continue;
      const src = candidateSource(image);
      if (!src) continue;
      const copy = image.cloneNode(false);
      copy.setAttribute("src", src);
      copy.removeAttribute("srcset");
      wrapper.appendChild(copy);
    }
    return captureFinalMarkdown(wrapper);
  }

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
    const selected = candidates[0];
    // Generated images may be rendered as siblings of the Markdown node.
    // Keep the same assistant turn, not a global page image or old response.
    if (node instanceof Element && node !== selected) {
      const mediaOutsideMarkdown = [...node.querySelectorAll("img")].some(img =>
        candidateImage(img) && !selected.contains(img));
      if (mediaOutsideMarkdown) return node;
    }
    return selected;
  }

  function cleanClone(node) {
    const root = contentRoot(node);
    if (!root) return null;
    let clone = null;
    try { clone = root.cloneNode(true); } catch (_) { return null; }
    if (!clone?.querySelectorAll) return clone;
    // Browsers commonly expose <picture>/<img> as a srcset/currentSrc without
    // an ordinary src. Copy the resolved image source into the cloned turn
    // before removing preview action wrappers.
    const realImages = [...root.querySelectorAll("img")];
    const cloneImages = [...clone.querySelectorAll("img")];
    realImages.forEach((img, index) => {
      const resolved = String(img.currentSrc || "").trim();
      if (cloneImages[index] && candidateImage(img)
          && /^(blob:|data:image\/|https:\/\/)/i.test(resolved)) {
        cloneImages[index].setAttribute("src", resolved);
      }
    });
    // Generated pictures can live *inside* a clickable preview button. Remove
    // the button's action chrome without discarding the actual user-requested
    // image. Only preserve images that were visibly large in this same turn.
    const visibleMediaSources = new Set([...root.querySelectorAll("img")]
      .filter(candidateImage)
      .map(img => String(img.currentSrc || img.getAttribute("src") || "").trim()));
    for (const button of [...clone.querySelectorAll("button")]) {
      const pictureNodes = [...button.querySelectorAll("img")]
        .filter(img => visibleMediaSources.has(String(img.getAttribute("src") || "").trim()));
      if (pictureNodes.length) button.replaceWith(...pictureNodes.map(img => img.cloneNode(true)));
      else button.remove();
    }
    for (const item of [...clone.querySelectorAll(ACTION_SELECTOR)]) {
      if (!clone.contains(item)) continue;
      const pictures = [...item.querySelectorAll("img")].filter(img =>
        visibleMediaSources.has(String(img.getAttribute("src") || "").trim()));
      if (pictures.length) item.replaceWith(...pictures.map(img => img.cloneNode(true)));
      else item.remove();
    }
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
      // Same-origin authenticated images and CORS-readable CDN images must
      // travel with the API response; a transient browser URL is not durable.
      if (!src.startsWith("blob:") && !/^https:\/\//i.test(src)) continue;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4500);
      try {
        const response = await fetch(src, { credentials: "include", signal: controller.signal });
        if (!response.ok) throw new Error("media-fetch-unavailable");
        const declaredSize = Number(response.headers.get("content-length") || 0);
        if (declaredSize > MAX_INLINE_IMAGE_BYTES || declaredSize + total > MAX_INLINE_IMAGE_TOTAL_BYTES) continue;
        const type = String(response.headers.get("content-type") || "").toLowerCase();
        if (type && !RASTER_IMAGE.test(type.split(";")[0].trim())) continue;
        const blob = await response.blob();
        if (!RASTER_IMAGE.test(blob.type)) continue;
        if (!blob.size || blob.size > MAX_INLINE_IMAGE_BYTES || total + blob.size > MAX_INLINE_IMAGE_TOTAL_BYTES) continue;
        const dataUrl = await blobToDataUrl(blob);
        if (!dataUrl.startsWith("data:image/")) continue;
        img.setAttribute("src", dataUrl);
        total += blob.size;
        inlined += 1;
      } catch (_) {
        // Content scripts cannot always fetch signed CDN images due to page
        // CORS. The read-only background relay only accepts approved OpenAI
        // media hosts and never receives arbitrary page URLs.
        if (/^https:\/\//i.test(src)) {
          try {
            const result = await chrome.runtime.sendMessage({
              type: "chat2api.media.fetch-v175", url: src,
            });
            const data = String(result?.data_url || "");
            const bytes = Number(result?.bytes || 0);
            if (result?.ok && RASTER_DATA_URI.test(data)
                && bytes > 0 && bytes <= MAX_INLINE_IMAGE_BYTES
                && total + bytes <= MAX_INLINE_IMAGE_TOTAL_BYTES) {
              img.setAttribute("src", data);
              total += bytes;
              inlined += 1;
            }
          } catch (_) {}
        }
      } finally {
        clearTimeout(timer);
      }
    }
    return { inlined, bytes: total };
  }

  async function captureFinalMarkdown(node) {
    const clone = cleanClone(node);
    if (!clone) return { text: "", image_inlined_count: 0, image_inlined_bytes: 0 };
    let embedded = 0;
    for (const image of [...clone.querySelectorAll("img")]) {
      const src = String(image.getAttribute("src") || "");
      if (!src.startsWith("data:")) continue;
      const base64 = src.slice(src.indexOf(",") + 1);
      const bounded = RASTER_DATA_URI.test(src)
        && base64.length > 0
        && base64.length % 4 === 0
        && base64.length <= 4 * Math.ceil(MAX_INLINE_IMAGE_BYTES / 3);
      if (bounded) embedded += 1;
      else image.removeAttribute("src");
    }
    const imageStats = await inlineBlobImages(clone);
    return {
      text: normalizeOutput(blockMarkdown(clone)),
      image_inlined_count: imageStats.inlined,
      image_embedded_count: embedded,
      image_inlined_bytes: imageStats.bytes,
    };
  }

  globalThis[KEY] = Object.freeze({
    version: VERSION,
    format: "markdown",
    contentRoot,
    hasCandidateImage,
    snapshotPageMedia,
    locateNewPageMedia,
    capturePageImages,
    extractMarkdown,
    captureFinalMarkdown,
  });
})();
