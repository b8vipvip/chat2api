/* Mode 1 native-content renderer. */
(() => {
  "use strict";
  const HTML_TAGS = new Set(["div","p","span","strong","em","h1","h2","h3","h4","ul","ol","li","blockquote","pre","code","table","thead","tbody","tr","td","th","figure","figcaption","img","hr","br"]);
  const MATH_TAGS = new Set(["math","semantics","mrow","mi","mo","mn","msub","mfrac","mtext","mover","munder","munderover","msup","msubsup","msqrt","mroot"]);
  const SVG_TAGS = new Set(["svg","g","path","line","rect","circle","ellipse","polyline","polygon","text","tspan","defs","clippath"]);
  const SAFE_TAGS = new Set([...HTML_TAGS,...MATH_TAGS,...SVG_TAGS]);
  const SVG_ATTRS = new Set(["viewBox","width","height","preserveAspectRatio","x","y","dx","dy","x1","x2","y1","y2","cx","cy","r","rx","ry","d","points","transform","fill","stroke","stroke-width","stroke-linecap","stroke-linejoin","stroke-dasharray","opacity","fill-opacity","stroke-opacity","text-anchor","dominant-baseline","font-size","font-weight","id","clip-path"]);
  const SAFE_STYLES = {
    display:["block","inline","inline-block","flex","inline-flex","grid","block math","inline math"],
    flexDirection:["row","row-reverse","column","column-reverse"],flexWrap:["wrap","nowrap"],
    justifyContent:["start","center","end","space-between","space-around"],
    alignItems:["start","center","end","stretch","baseline"],objectFit:["contain","cover","fill","none"],
    textAlign:["left","center","right","justify","start","end"],fontWeight:["400","500","600","700"],
    whiteSpace:["normal","nowrap","pre","pre-wrap","pre-line","break-spaces"],
    wordBreak:["normal","break-all","keep-all","break-word"],overflowWrap:["normal","break-word","anywhere"],
    verticalAlign:["baseline","middle","top","bottom","sub","super"],
    textDecorationLine:["none","underline","line-through","overline"],borderStyle:["none","solid","dashed","dotted","double"],
    boxSizing:["border-box","content-box"],borderCollapse:["collapse","separate"],
    alignSelf:["auto","start","center","end","stretch","baseline"],justifySelf:["auto","start","center","end","stretch"]
  };
  const LENGTH_STYLES = new Set(["gap","rowGap","columnGap","padding","marginTop","marginBottom","marginLeft","marginRight","width","minWidth","maxWidth","height","minHeight","maxHeight","borderRadius","borderWidth","letterSpacing","fontSize","lineHeight"]);
  const COLOR_STYLES = new Set(["color","backgroundColor","borderColor"]);
  const LENGTH = /^(?:0|(?:\d{1,4}(?:\.\d{1,3})?)(?:px|rem|%))$/;
  const PADDING = /^(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|%))(?:\s+(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|%))){0,3}$/;
  const BORDER_SPACING = /^(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|%))(?:\s+(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|%)))?$/;
  const GRID_COLUMNS = /^(?:\d{1,4}(?:\.\d{1,2})?(?:px|%)|minmax\(\d{1,4}(?:\.\d{1,2})?px,\s*1fr\)|1fr)(?:\s+(?:\d{1,4}(?:\.\d{1,2})?(?:px|%)|minmax\(\d{1,4}(?:\.\d{1,2})?px,\s*1fr\)|1fr)){0,5}$/;
  const HEX_COLOR = /^#[0-9a-fA-F]{3,8}$/;
  const RGB_COLOR = /^rgba?\([0-9.,\s%]+\)$/;
  const MODERN_COLOR = /^(?:(?:oklab|oklch)\(\s*[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+(?:\s*\/\s*\d*\.?\d+)?\s*\)|color\(srgb\s+\d*\.?\d+\s+\d*\.?\d+\s+\d*\.?\d+(?:\s*\/\s*\d*\.?\d+)?\s*\))$/i;
  const SVG_COLOR = /^(?:none|currentColor|transparent|black|white|red|blue|green|gray|grey|#[\da-f]{3,8}|rgba?\([\d.,\s%]+\))$/i;
  const SVG_NUMBER = /^[-+]?(?:\d{1,6}(?:\.\d{1,5})?|\.\d{1,5})(?:px|%)?$/;
  const SVG_GEOMETRY = /^[MmLlHhVvCcSsQqTtAaZzEe0-9+.,\-\s]+$/;
  const SVG_NUMBERS = /^[0-9eE+.,\-\s]+$/;
  const SVG_TRANSFORM = /^(?:(?:translate|scale|rotate|matrix|skewX|skewY)\([-+0-9.,\s]+\)\s*){1,8}$/;
  const SVG_ID = /^[A-Za-z_][A-Za-z0-9_-]{0,80}$/;
  const SVG_CLIP = /^url\(#([A-Za-z_][A-Za-z0-9_-]{0,80})\)$/;
  const SVG_RATIO = /^(?:none|x(?:Min|Mid|Max)Y(?:Min|Mid|Max))(?:\s+(?:meet|slice))?$/;
  const MEDIA_PATH = /^\/api\/media\/[A-Za-z0-9_-]{6,100}\/[A-Za-z0-9_-]{8,150}$/;
  const GENERATED_PATH = /^\/api\/generated-files\/(?:pending\/pgen_[a-f0-9]{32}\/[A-Za-z0-9_-]{16,150}|[A-Za-z0-9_-]{6,100}\/[A-Za-z0-9_-]{8,150})$/;
  const SVG_NS = "http://www.w3.org/2000/svg", MATH_NS = "http://www.w3.org/1998/Math/MathML";
  const MAX_NODES = 30000, MAX_TEXT = 300000;

  function safeRouteUrl(value, pattern) {
    if (typeof value !== "string" || !value || value.length > 2048) return "";
    try {
      const url = new URL(value,window.location.origin);
      if (url.username || url.password || !pattern.test(url.pathname)) return "";
      // Archived media and generated files are served by this Chat2API origin.
      // An HTTPS URL on another host is still untrusted and must never be loaded.
      if (url.origin !== window.location.origin) return "";
      if (url.search || url.hash) return "";
      return url.href;
    } catch (_) { return ""; }
  }
  function safeMediaUrl(value) { return safeRouteUrl(value,MEDIA_PATH); }
  function safeGeneratedFileUrl(value) { return safeRouteUrl(value,GENERATED_PATH); }
  function validSvgAttribute(key,value) {
    if (!SVG_ATTRS.has(key) || typeof value !== "string" || !value || value !== value.trim() ||
        value.length > (key === "d" ? 16000 : key === "points" ? 4096 : 240)) return false;
    if (key === "d") return SVG_GEOMETRY.test(value);
    if (["points","stroke-dasharray","viewBox"].includes(key)) return SVG_NUMBERS.test(value);
    if (key === "transform") return SVG_TRANSFORM.test(value);
    if (key === "fill" || key === "stroke") return SVG_COLOR.test(value) || MODERN_COLOR.test(value);
    if (key === "id") return SVG_ID.test(value);
    if (key === "clip-path") return SVG_CLIP.test(value);
    if (key === "preserveAspectRatio") return SVG_RATIO.test(value);
    if (key === "stroke-linecap") return ["butt","round","square"].includes(value);
    if (key === "stroke-linejoin") return ["miter","round","bevel"].includes(value);
    if (key === "text-anchor") return ["start","middle","end"].includes(value);
    if (key === "dominant-baseline") return ["auto","middle","central","hanging","alphabetic","text-after-edge"].includes(value);
    if (key === "font-weight") return ["normal","bold","400","500","600","700"].includes(value);
    return SVG_NUMBER.test(value);
  }
  function validStyle(key,value,tag) {
    if (typeof value !== "string" || !value || value.length > 240 ||
        /[;{}<>]|url\s*\(|expression\s*\(|var\s*\(/i.test(value)) return false;
    if (SAFE_STYLES[key]) return SAFE_STYLES[key].includes(value);
    if (LENGTH_STYLES.has(key) && LENGTH.test(value) && value.length <= 16) return true;
    if (key === "padding") return value.length <= 64 && PADDING.test(value);
    if (key === "borderSpacing" && tag === "table") return value.length <= 36 && BORDER_SPACING.test(value);
    if (key === "gridTemplateColumns") return value.length <= 120 && GRID_COLUMNS.test(value);
    if (COLOR_STYLES.has(key) && value.length <= 96) return HEX_COLOR.test(value) || RGB_COLOR.test(value) || MODERN_COLOR.test(value);
    return false;
  }
  function validateNode(value,depth,parentTag,counts) {
    if (!value || typeof value !== "object" || Array.isArray(value) || depth > 64 || counts.nodes >= MAX_NODES) return false;
    counts.nodes += 1;
    const tag = value.tag;
    if (typeof tag !== "string" || !SAFE_TAGS.has(tag)) return false;
    if (SVG_TAGS.has(parentTag) && !SVG_TAGS.has(tag)) return false;
    if (SVG_TAGS.has(tag) && !SVG_TAGS.has(parentTag) && tag !== "svg") return false;
    const allowed = new Set(["tag","text","children","style","media_index","intrinsic_width","intrinsic_height","source_sha256","attrs","col_span","row_span"]);
    if (Object.keys(value).some(key => !allowed.has(key))) return false;
    if (Object.prototype.hasOwnProperty.call(value,"attrs")) {
      if (!SVG_TAGS.has(tag) || !value.attrs || typeof value.attrs !== "object" || Array.isArray(value.attrs) || Object.keys(value.attrs).length > 24) return false;
      if (Object.entries(value.attrs).some(([key,item]) => !validSvgAttribute(key,item))) return false;
    }
    if (tag === "img") {
      if (!Number.isInteger(value.media_index) || value.media_index < 0 || value.media_index >= 40) return false;
      const hasWidth = Object.prototype.hasOwnProperty.call(value,"intrinsic_width"), hasHeight = Object.prototype.hasOwnProperty.call(value,"intrinsic_height");
      if (hasWidth !== hasHeight) return false;
      if (hasWidth && (!Number.isInteger(value.intrinsic_width) || !Number.isInteger(value.intrinsic_height) ||
          value.intrinsic_width < 1 || value.intrinsic_width > 16384 || value.intrinsic_height < 1 || value.intrinsic_height > 16384)) return false;
      if (value.source_sha256 !== undefined && !/^[a-f0-9]{64}$/.test(value.source_sha256)) return false;
      if (value.text !== undefined || value.children !== undefined) return false;
    } else if (["media_index","intrinsic_width","intrinsic_height","source_sha256"].some(key => Object.prototype.hasOwnProperty.call(value,key))) return false;
    if (value.text !== undefined) {
      if (typeof value.text !== "string" || tag === "img" || (SVG_TAGS.has(tag) && !["text","tspan"].includes(tag))) return false;
      counts.chars += value.text.length;
      if (counts.chars > MAX_TEXT) return false;
    }
    if (value.style !== undefined) {
      if (!value.style || typeof value.style !== "object" || Array.isArray(value.style) || Object.keys(value.style).length > 32) return false;
      if (Object.entries(value.style).some(([key,item]) => !validStyle(key,item,tag))) return false;
    }
    if (value.col_span !== undefined && (!["td","th"].includes(tag) || !Number.isInteger(value.col_span) || value.col_span < 2 || value.col_span > 256)) return false;
    if (value.row_span !== undefined && (!["td","th"].includes(tag) || !Number.isInteger(value.row_span) || (value.row_span !== 0 && value.row_span < 2) || value.row_span > 256)) return false;
    if (value.children !== undefined) {
      if (["img","hr","br"].includes(tag) || !Array.isArray(value.children) || value.children.length > 8192) return false;
      for (const child of value.children) if (!validateNode(child,depth+1,tag,counts)) return false;
    }
    return true;
  }
  function validateSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot) || snapshot.version !== 1 ||
        !Number.isInteger(snapshot.revision) || snapshot.revision < 1 || snapshot.revision > 1000000 ||
        !["live-provisional","verified-final"].includes(snapshot.finality) ||
        !Array.isArray(snapshot.assets) || snapshot.assets.length > 40 ||
        !snapshot.assets.every(item => typeof item === "string" && item.length <= 2048) ||
        !Array.isArray(snapshot.files) || snapshot.files.length > 10) return false;
    return validateNode(snapshot.root,0,"",{nodes:0,chars:0});
  }
  function createNode(value,snapshot,depth) {
    if (depth > 64) throw new Error("native tree depth exceeded");
    const tag = value.tag;
    let element;
    if (SVG_TAGS.has(tag)) element = document.createElementNS(SVG_NS,tag === "clippath" ? "clipPath" : tag);
    else if (MATH_TAGS.has(tag)) element = document.createElementNS(MATH_NS,tag);
    else element = document.createElement(tag);
    if (value.style) Object.entries(value.style).forEach(([key,item]) => { if (validStyle(key,item,tag)) element.style[key] = item; });
    if (value.attrs) Object.entries(value.attrs).forEach(([key,item]) => { if (validSvgAttribute(key,item)) element.setAttribute(key,item); });
    if (value.col_span !== undefined) element.colSpan = value.col_span;
    if (value.row_span !== undefined) element.rowSpan = value.row_span;
    if (tag === "img") {
      const src = safeMediaUrl(snapshot.assets[value.media_index]);
      if (!src) {
        const fallback = document.createElement("span");
        fallback.className = "native-image-unverified";
        fallback.textContent = "图片未通过内容完整性校验";
        return fallback;
      }
      element.src = src; element.alt = "ChatGPT 原生图片"; element.loading = "lazy";
      element.decoding = "async"; element.referrerPolicy = "no-referrer";
      if (value.intrinsic_width && value.intrinsic_height) { element.width = value.intrinsic_width; element.height = value.intrinsic_height; }
    }
    if (typeof value.text === "string") element.appendChild(document.createTextNode(value.text));
    for (const child of value.children || []) element.appendChild(createNode(child,snapshot,depth+1));
    return element;
  }
  function renderNativeSnapshot(host,snapshot) {
    if (!host || !validateSnapshot(snapshot)) return false;
    try {
      const state = document.createElement("div");
      state.className = "native-delivery-state";
      state.textContent = snapshot.finality === "verified-final" ? "ChatGPT 原生内容 · 已验证完成" : "ChatGPT 原生内容 · 实时更新，最终校验中";
      const root = document.createElement("div"); root.className = "native-rich-root";
      root.appendChild(createNode(snapshot.root,snapshot,0));
      const files = document.createElement("div"); files.className = "native-generated-files";
      for (const item of snapshot.files) {
        if (!item || typeof item !== "object" || typeof item.filename !== "string") continue;
        const url = item.status === "ready" ? safeGeneratedFileUrl(item.url) : "";
        const card = document.createElement("div"); card.className = "native-generated-file";
        if (url) {
          const link = document.createElement("a"); link.href = url; link.rel = "noopener noreferrer";
          link.referrerPolicy = "no-referrer"; link.textContent = item.filename.slice(0,180); card.appendChild(link);
        } else {
          const label = document.createElement("span");
          label.textContent = item.filename.slice(0,180) + " · " + (item.status === "processing" ? "文件归档中" : "文件不可用");
          card.appendChild(label);
        }
        files.appendChild(card);
      }
      host.replaceChildren(state,root);
      if (files.childNodes.length) host.appendChild(files);
      return true;
    } catch (_) { return false; }
  }
  window.Chat2APINativeRendererV1 = Object.freeze({validateSnapshot,renderNativeSnapshot,safeMediaUrl,safeGeneratedFileUrl});
})();