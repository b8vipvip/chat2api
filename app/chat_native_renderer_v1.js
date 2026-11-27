/* Mode 1 native-content renderer. */
(() => {
  "use strict";
  const HTML_TAGS = new Set(["div","p","span","strong","em","h1","h2","h3","h4","ul","ol","li","blockquote","pre","code","table","thead","tbody","tr","td","th","figure","figcaption","img","hr","br","a","details","summary","input","select","option","button"]);
  const MATH_TAGS = new Set(["math","semantics","mrow","mi","mo","mn","msub","mfrac","mtext","annotation","mover","munder","munderover","msup","msubsup","msqrt","mroot","mspace","mstyle","mtable","mtr","mtd","mmultiscripts","mphantom","menclose","mpadded","merror","ms","mfenced","mprescripts","none","mstack","mlongdiv","msgroup","msrow","mscarries","mscarry","msline"]);
  const MATH_GLOBAL_ATTRS = new Set(["mathvariant","mathsize","mathcolor","mathbackground","displaystyle","scriptlevel","dir"]);
  const MATH_ATTRS_BY_TAG = {
    math:["display"],
    mo:["stretchy","fence","separator","symmetric","largeop","movablelimits","accent","form","lspace","rspace","minsize","maxsize"],
    mfrac:["linethickness","numalign","denomalign","bevelled"],
    msub:["subscriptshift"],msup:["superscriptshift"],msubsup:["subscriptshift","superscriptshift"],
    munder:["accentunder"],mover:["accent"],munderover:["accent","accentunder"],
    mspace:["width","height","depth"],mfenced:["open","close","separators"],menclose:["notation"],
    mtable:["align","rowalign","columnalign","rowspacing","columnspacing","frame","framespacing","equalrows","equalcolumns","side","minlabelspacing","columnwidth"],
    mtr:["rowalign","columnalign","groupalign"],mtd:["rowalign","columnalign","groupalign","rowspan","columnspan"],
    mpadded:["width","height","depth","lspace","voffset"],annotation:["encoding"]
  };
  const MATH_BOOLEAN_ATTRS = new Set(["displaystyle","stretchy","fence","separator","symmetric","largeop","movablelimits","accent","accentunder","bevelled","equalrows","equalcolumns"]);
  const MATH_ENUMS = {
    mathvariant:["normal","bold","italic","bold-italic","double-struck","bold-fraktur","script","bold-script","fraktur","sans-serif","bold-sans-serif","sans-serif-italic","sans-serif-bold-italic","monospace","initial","tailed","looped","stretched"],
    mathsize:["small","normal","big"],display:["block","inline"],dir:["ltr","rtl"],
    form:["prefix","infix","postfix"],numalign:["left","center","right"],denomalign:["left","center","right"],
    align:["top","bottom","center","baseline","axis","left","right"],
    frame:["none","solid","dashed"],side:["left","right"],
    encoding:["application/x-tex","application/mathml-presentation+xml","application/mathml-content+xml","text/plain"]
  };
  const MATH_LENGTH_ATTRS = new Set(["mathsize","width","height","depth","lspace","rspace","minsize","maxsize","linethickness","subscriptshift","superscriptshift","minlabelspacing","voffset"]);
  const MATH_LENGTH = /^(?:[+-]?(?:\d{1,4}(?:\.\d{1,3})?|\.\d{1,3})(?:em|ex|px|pt|pc|in|cm|mm|%)|0)$/;
  const MATH_SPACE_NAMES = new Set(["veryverythinmathspace","verythinmathspace","thinmathspace","mediummathspace","thickmathspace","verythickmathspace","veryverythickmathspace"]);
  const MATH_ALIGN_VALUES = {
    rowalign:new Set(["top","bottom","center","baseline","axis"]),
    columnalign:new Set(["left","center","right"]),
    groupalign:new Set(["left","center","right","decimalpoint"])
  };
  const MATH_NOTATION = new Set(["longdiv","actuarial","radical","box","roundedbox","circle","left","right","top","bottom","updiagonalstrike","downdiagonalstrike","verticalstrike","horizontalstrike","madruwb"]);
  const MATH_MULTI_LENGTH_ATTRS = new Set(["rowspacing","columnspacing","framespacing","columnwidth"]);
  const SVG_TAGS = new Set(["svg","g","path","line","rect","circle","ellipse","polyline","polygon","text","tspan","title","desc","defs","clippath"]);
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
  const AUDIO_MIMES = new Set(["audio/mpeg","audio/wav","audio/mp4","audio/ogg","audio/flac"]);
  const VIDEO_MIMES = new Set(["video/mp4","video/webm"]);
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
  function safeExternalLinkUrl(value) {
    if (typeof value !== "string" || !value || value.length > 2048 || value !== value.trim() ||
        /[\u0000-\u001f\u007f]/.test(value)) return "";
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase().replace(/\.$/, "");
      if (url.protocol !== "https:" || url.origin === window.location.origin ||
          url.username || url.password || (url.port && url.port !== "443") ||
          !host || host.includes(":") ||
          /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) return "";
      if (["chatgpt.com","chat.openai.com","localhost"].includes(host) ||
          /(?:^|\.)(?:chatgpt\.com|localhost|local|internal|test|invalid|example|onion)$/.test(host)) return "";
      if (!/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host)) return "";
      return url.href;
    } catch (_) { return ""; }
  }
  function cleanSourceText(value, limit) {
    if (typeof value !== "string") return "";
    return value.replace(/[\u0000-\u001f\u007f]/g," ").replace(/\s+/g," ").trim().slice(0,limit);
  }
  function normalizeSourceReferences(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set(), rows = [];
    for (const item of value.slice(0,32)) {
      if (!item || typeof item !== "object" || Array.isArray(item)) continue;
      const url = safeExternalLinkUrl(item.url);
      if (!url) continue;
      const rawId = cleanSourceText(item.id,128);
      const id = /^[A-Za-z0-9_.:-]{1,128}$/.test(rawId) ? rawId : "";
      const identity = id ? "id:" + id : "url:" + url;
      if (seen.has(identity)) continue;
      seen.add(identity);
      const host = new URL(url).hostname;
      rows.push({
        url, id,
        title: cleanSourceText(item.title,180) || host,
        snippet: cleanSourceText(item.snippet,420),
      });
      if (rows.length >= 8) break;
    }
    return rows;
  }

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
  function validMathAttribute(tag,key,value) {
    const local = MATH_ATTRS_BY_TAG[tag] || [];
    if (!MATH_GLOBAL_ATTRS.has(key) && !local.includes(key)) return false;
    if (typeof value !== "string" || value !== value.trim() || value.length > 160 ||
        /[\u0000-\u001f\u007f]/.test(value)) return false;
    if (MATH_BOOLEAN_ATTRS.has(key)) return value === "true" || value === "false";
    if (MATH_ENUMS[key]) return MATH_ENUMS[key].includes(value);
    if (key === "scriptlevel") return /^[+-]?\d{1,2}$/.test(value) && Number(value) >= -10 && Number(value) <= 10;
    if (key === "mathcolor" || key === "mathbackground") {
      return /^(?:#[0-9a-f]{3,8}|rgba?\([0-9.,\s%]+\)|black|white|red|green|blue|gray|grey|transparent|currentColor)$/i.test(value)
        || MODERN_COLOR.test(value);
    }
    if (MATH_LENGTH_ATTRS.has(key)) {
      return MATH_SPACE_NAMES.has(value) || ["thin","medium","thick","infinity","auto","fit"].includes(value) || MATH_LENGTH.test(value);
    }
    if (MATH_MULTI_LENGTH_ATTRS.has(key)) {
      const tokens = value.split(/\s+/);
      return tokens.length >= 1 && tokens.length <= 16 && tokens.every(token =>
        ["auto","fit"].includes(token) || MATH_SPACE_NAMES.has(token) || MATH_LENGTH.test(token));
    }
    if (["rowalign","columnalign","groupalign"].includes(key)) {
      const tokens = value.split(/\s+/), allowed = MATH_ALIGN_VALUES[key];
      return tokens.length <= 16 && tokens.every(token => allowed.has(token));
    }
    if (key === "notation") {
      const tokens = value.split(/\s+/);
      return tokens.length >= 1 && tokens.length <= 8 && tokens.every(token => MATH_NOTATION.has(token));
    }
    if (key === "rowspan" || key === "columnspan") return /^\d{1,3}$/.test(value) && Number(value) >= 1 && Number(value) <= 256;
    if (key === "open" || key === "close" || key === "separators") return /^[^<>\u0000-\u001f\u007f]{0,16}$/.test(value);
    return false;
  }
  function safeControlText(value,limit,allowNewlines=false) {
    if (typeof value !== "string" || value.length > limit) return false;
    return ![...value].some(char => {
      const code=char.charCodeAt(0);
      return (code < 32 && !(allowNewlines && [9,10,13].includes(code))) || code===127;
    });
  }
  function validControl(tag,control) {
    if (!control || typeof control!=="object" || Array.isArray(control) ||
        typeof control.kind!=="string") return false;
    const keys=Object.keys(control).sort().join(",");
    const exact=(expected)=>keys===[...expected].sort().join(",");
    if (tag==="details") return exact(["kind","open"]) &&
      control.kind==="disclosure" && typeof control.open==="boolean";
    if (tag==="input") {
      if (!safeControlText(control.label,160) || !control.label.trim() ||
          typeof control.disabled!=="boolean") return false;
      if (control.kind==="checkbox") return exact(["kind","label","disabled","checked"]) &&
        typeof control.checked==="boolean";
      if (control.kind==="radio") return exact(["kind","label","disabled","checked","group"]) &&
        typeof control.checked==="boolean" && typeof control.group==="string" &&
        /^[A-Za-z0-9_-]{1,64}$/.test(control.group);
      if (control.kind==="range") return exact(["kind","label","disabled","min","max","step","value"]) &&
        [control.min,control.max,control.step,control.value].every(Number.isFinite) &&
        [control.min,control.max,control.step,control.value].every(value=>Math.abs(value)<=1000000) &&
        control.min<control.max && control.step>0 &&
        control.value>=control.min && control.value<=control.max;
      return false;
    }
    if (tag==="select") return exact(["kind","label","disabled","selected"]) &&
      control.kind==="select" && safeControlText(control.label,160) &&
      control.label.trim().length>0 && safeControlText(control.selected,128) &&
      typeof control.disabled==="boolean";
    if (tag==="option") return exact(["kind","value","disabled","selected"]) &&
      control.kind==="option" && safeControlText(control.value,128) &&
      typeof control.disabled==="boolean" && typeof control.selected==="boolean";
    if (tag==="div") return exact(["kind","group"]) && control.kind==="tablist" &&
      typeof control.group==="string" && /^tg_[1-9][0-9]{0,3}$/.test(control.group);
    if (tag==="button") {
      if (control.kind==="copy") return exact(["kind","label","text"]) &&
        safeControlText(control.label,80) && control.label.trim().length>0 &&
        safeControlText(control.text,150000,true) && control.text.length>0;
      return control.kind==="tab" && exact(["kind","label","disabled","selected","group"]) &&
        safeControlText(control.label,160) && control.label.trim().length>0 &&
        typeof control.disabled==="boolean" && typeof control.selected==="boolean" &&
        typeof control.group==="string" && /^tg_[1-9][0-9]{0,3}$/.test(control.group);
    }
    return false;
  }
  let renderSequence=0;
  function validateNode(value,depth,parentTag,counts) {
    if (!value || typeof value !== "object" || Array.isArray(value) || depth > 64 || counts.nodes >= MAX_NODES) return false;
    counts.nodes += 1;
    const tag = value.tag;
    if (typeof tag !== "string" || !SAFE_TAGS.has(tag)) return false;
    if (SVG_TAGS.has(parentTag) && !SVG_TAGS.has(tag)) return false;
    if (SVG_TAGS.has(tag) && !SVG_TAGS.has(parentTag) && tag !== "svg") return false;
    const allowed = new Set(["tag","text","children","style","media_index","intrinsic_width","intrinsic_height","source_sha256","attrs","math_attrs","col_span","row_span","href","control","interaction_id"]);
    if (Object.keys(value).some(key => !allowed.has(key))) return false;
    const controlRequired=["details","input","select","option","button"].includes(tag);
    if (controlRequired !== (value.control !== undefined)) return false;
    if (value.control !== undefined && !validControl(tag,value.control)) return false;
    if (value.interaction_id !== undefined) {
      if (!(counts.interactionIds instanceof Set) || counts.interactionIds.has(value.interaction_id)) return false;
      counts.interactionIds.add(value.interaction_id);
      if (!["details","input","select","button"].includes(tag)
          || typeof value.interaction_id !== "string"
          || !/^nc_[1-9][0-9]{0,6}_[1-9][0-9]{0,3}$/.test(value.interaction_id)) return false;
      if (tag==="details" && value.control?.kind!=="disclosure") return false;
      if (tag==="input" && !["checkbox","radio","range"].includes(value.control?.kind)) return false;
      if (tag==="select" && value.control?.kind!=="select") return false;
      if (tag==="button" && value.control?.kind!=="tab") return false;
    }
    if (value.control !== undefined) {
      // Copy text is the same adjacent code block already counted in the tree.
      counts.chars += (value.control.label || "").length;
      if (counts.chars > MAX_TEXT) return false;
    }
    if (["details","select"].includes(tag) && !Array.isArray(value.children)) return false;
    if (["input","select","button"].includes(tag) && value.text !== undefined) return false;
    if (["input","option","button"].includes(tag) && value.children !== undefined) return false;
    if (tag==="option" && (typeof value.text!=="string" || !value.text.trim())) return false;
    if (tag==="summary" && parentTag!=="details") return false;
    if (tag==="option" && parentTag!=="select") return false;
    if (value.href !== undefined && (tag !== "a" || !safeExternalLinkUrl(value.href))) return false;
    if (Object.prototype.hasOwnProperty.call(value,"attrs")) {
      if (!SVG_TAGS.has(tag) || !value.attrs || typeof value.attrs !== "object" || Array.isArray(value.attrs) || Object.keys(value.attrs).length > 24) return false;
      if (Object.entries(value.attrs).some(([key,item]) => !validSvgAttribute(key,item))) return false;
    }
    if (Object.prototype.hasOwnProperty.call(value,"math_attrs")) {
      if (!MATH_TAGS.has(tag) || !value.math_attrs || typeof value.math_attrs !== "object" ||
          Array.isArray(value.math_attrs) || Object.keys(value.math_attrs).length > 24) return false;
      if (Object.entries(value.math_attrs).some(([key,item]) => !validMathAttribute(tag,key,item))) return false;
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
      if (typeof value.text !== "string" || tag === "img" || (SVG_TAGS.has(tag) && !["text","tspan","title","desc"].includes(tag))) return false;
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
      if (["img","hr","br","input","option","button"].includes(tag) || !Array.isArray(value.children) || value.children.length > 8192) return false;
      if (tag==="select" && (!value.children.length || value.children.length>64 || value.children.some(child=>child?.tag!=="option"))) return false;
      if (tag==="details" && (value.children.filter(child=>child?.tag==="summary").length>1 || (value.children.some(child=>child?.tag==="summary") && value.children[0]?.tag!=="summary"))) return false;
      for (const child of value.children) if (!validateNode(child,depth+1,tag,counts)) return false;
      if (tag==="select") {
        const selected=value.children.filter(child=>child.control?.selected===true);
        if (selected.length!==1 || selected[0].control.value!==value.control.selected) return false;
      }
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
    return validateNode(snapshot.root,0,"",{nodes:0,chars:0,interactionIds:new Set()});
  }
  function bindNativeInteraction(element,value,context,eventName,action,readValue,restoreValue,onAccepted=null) {
    const interactionId = String(value?.interaction_id || "");
    if (!interactionId || typeof context?.onInteraction !== "function") return false;
    let accepted = readValue();
    let pending = false;
    element.addEventListener(eventName, async () => {
      if (pending) {
        try { restoreValue(accepted); } catch {}
        return;
      }
      const next = readValue();
      if (Object.is(next,accepted)) return;
      const previous = accepted;
      pending = true;
      element.setAttribute("aria-busy","true");
      try {
        const result = await context.onInteraction({
          interaction_id:interactionId,
          revision:context.revision,
          action,
          value:next,
        });
        if (result?.ok !== true) throw new Error("native interaction was not acknowledged");
        accepted = next;
        if (typeof onAccepted==="function") onAccepted(next);
        element.removeAttribute?.("aria-invalid");
        if (result?.persisted === false) {
          element.setAttribute("title","操作已同步到 Worker，但服务器尚未确认持久化");
        } else {
          element.removeAttribute?.("title");
        }
      } catch (_) {
        try { restoreValue(previous); } catch {}
        element.setAttribute("aria-invalid","true");
        element.setAttribute("title","同步失败，请重试");
      } finally {
        pending = false;
        element.removeAttribute?.("aria-busy");
      }
    });
    return true;
  }

  function bindTabInteraction(element,value,context) {
    const interactionId=String(value?.interaction_id || "");
    const control=value?.control || {};
    if (!interactionId || typeof context?.onInteraction!=="function") return false;
    const group=String(control.group || "");
    if (control.selected) context.tabSelection.set(group,element);
    let pending=false;
    element.addEventListener("click",async()=>{
      if (pending || element.getAttribute("aria-selected")==="true") return;
      pending=true;
      element.setAttribute("aria-busy","true");
      try {
        const result=await context.onInteraction({
          interaction_id:interactionId,
          revision:context.revision,
          action:"set_selected",
          value:true,
        });
        if (result?.ok!==true) throw new Error("native tab action was not acknowledged");
        const previous=context.tabSelection.get(group);
        if (previous && previous!==element) {
          previous.setAttribute("aria-selected","false");
          previous.tabIndex=-1;
        }
        element.setAttribute("aria-selected","true");
        element.tabIndex=0;
        context.tabSelection.set(group,element);
        element.removeAttribute?.("aria-invalid");
        if (result?.persisted===false) {
          element.setAttribute("title","标签已同步到 Worker，但服务器尚未确认持久化");
        } else {
          element.removeAttribute?.("title");
        }
      } catch (_) {
        element.setAttribute("aria-invalid","true");
        element.setAttribute("title","标签切换同步失败，请重试");
      } finally {
        pending=false;
        element.removeAttribute?.("aria-busy");
      }
    });
    return true;
  }

  function createNode(value,snapshot,depth,context) {
    if (depth > 64) throw new Error("native tree depth exceeded");
    const tag = value.tag;
    let element;
    if (SVG_TAGS.has(tag)) element = document.createElementNS(SVG_NS,tag === "clippath" ? "clipPath" : tag);
    else if (tag==="button" && value.control?.kind==="copy") {
      element=document.createElement("button");
      element.type="button";
      element.className="native-copy-control";
      element.textContent=value.control.label;
      if (value.style) Object.entries(value.style).forEach(([key,item]) => {
        if (validStyle(key,item,tag)) element.style[key] = item;
      });
      element.addEventListener?.("click",()=>{
        const clipboard=globalThis.navigator?.clipboard;
        if (!clipboard?.writeText) {
          element.textContent="无法访问剪贴板";
          return;
        }
        Promise.resolve(clipboard.writeText(value.control.text)).then(()=>{
          element.textContent="已复制";
          element.setAttribute("aria-label","已复制");
        }).catch(()=>{
          element.textContent="复制失败";
        });
      });
      return element;
    } else if (tag==="button" && value.control?.kind==="tab") {
      element=document.createElement("button");
      element.type="button";
      element.className="native-tab-control";
      element.disabled=value.control.disabled;
      element.textContent=value.control.label;
      element.setAttribute("role","tab");
      element.setAttribute("aria-label",value.control.label);
      element.setAttribute("aria-selected",String(value.control.selected));
      element.setAttribute("aria-disabled",String(value.control.disabled));
      element.tabIndex=value.control.selected?0:-1;
      if (value.style) Object.entries(value.style).forEach(([key,item])=>{
        if(validStyle(key,item,tag))element.style[key]=item;
      });
      bindTabInteraction(element,value,context);
      return element;
    } else if (tag==="input") {
      element=document.createElement("input");
      element.type=value.control.kind;
      element.disabled=value.control.disabled;
      element.setAttribute("aria-label",value.control.label);
      if (value.control.kind==="range") {
        element.min=String(value.control.min);
        element.max=String(value.control.max);
        element.step=String(value.control.step);
        element.value=String(value.control.value);
      } else {
        element.checked=value.control.checked;
        if (value.control.kind==="radio") {
          const groupKey=String(value.control.group || "");
          if (groupKey) {
            if (!context.radioGroups.has(groupKey))
              context.radioGroups.set(groupKey,context.prefix+"-"+groupKey);
            element.name=context.radioGroups.get(groupKey);
          } else {
            context.radioIndex=(context.radioIndex || 0)+1;
            element.name=context.prefix+"-radio-"+context.radioIndex;
          }
          if (element.checked) context.radioSelection.set(element.name,element);
        }
      }
      // Input controls return early, so apply their validated captured styles
      // here rather than silently dropping width/layout information.
      if (value.style) Object.entries(value.style).forEach(([key,item]) => {
        if (validStyle(key,item,tag)) element.style[key] = item;
      });
      if (value.control.kind==="range") {
        bindNativeInteraction(element,value,context,"change","set_value",
          ()=>Number(element.value),previous=>{element.value=String(previous);});
      } else if (value.control.kind==="radio") {
        const groupName = element.name;
        bindNativeInteraction(element,value,context,"change","set_checked",
          ()=>Boolean(element.checked),
          previous=>{
            if (previous) {
              element.checked=true;
              context.radioSelection.set(groupName,element);
            } else {
              const acceptedRadio=context.radioSelection.get(groupName);
              if (acceptedRadio && acceptedRadio!==element) acceptedRadio.checked=true;
              element.checked=false;
            }
          },
          next=>{if(next) context.radioSelection.set(groupName,element);});
      } else {
        bindNativeInteraction(element,value,context,"change","set_checked",
          ()=>Boolean(element.checked),previous=>{element.checked=Boolean(previous);});
      }
      return element;
    } else if (tag==="select") {
      element=document.createElement("select");
      element.disabled=value.control.disabled;
      element.setAttribute("aria-label",value.control.label);
    } else if (tag==="option") {
      element=document.createElement("option");
      element.value=value.control.value;
      element.disabled=value.control.disabled;
      element.selected=value.control.selected;
    }
    else if (MATH_TAGS.has(tag)) element = document.createElementNS(MATH_NS,tag);
    else if (tag === "a") {
      const href = safeExternalLinkUrl(value.href);
      element = document.createElement(href ? "a" : "span");
      if (href) {
        element.href = href;
        element.target = "_blank";
        element.rel = "noopener noreferrer nofollow";
        element.referrerPolicy = "no-referrer";
      }
    } else element = document.createElement(tag);
    if (value.style) Object.entries(value.style).forEach(([key,item]) => { if (validStyle(key,item,tag)) element.style[key] = item; });
    if (value.attrs) Object.entries(value.attrs).forEach(([key,item]) => { if (validSvgAttribute(key,item)) element.setAttribute(key,item); });
    if (value.math_attrs) Object.entries(value.math_attrs).forEach(([key,item]) => { if (validMathAttribute(tag,key,item)) element.setAttribute(key,item); });
    if (value.control?.kind==="disclosure") element.open=value.control.open;
    if (value.control?.kind==="tablist") element.setAttribute("role","tablist");
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
    for (const child of value.children || []) element.appendChild(createNode(child,snapshot,depth+1,context));
    if (tag==="select") {
      element.value=value.control.selected;
      bindNativeInteraction(element,value,context,"change","set_value",
        ()=>String(element.value),previous=>{element.value=String(previous);});
    } else if (tag==="details") {
      bindNativeInteraction(element,value,context,"toggle","set_open",
        ()=>Boolean(element.open),previous=>{element.open=Boolean(previous);});
    }
    return element;
  }
  function hasLocalOnlyControls(root,onInteraction=null) {
    const pending=[root];
    while(pending.length) {
      const node=pending.pop();
      if (!node || typeof node!=="object") continue;
      if (["input","select","details"].includes(node.tag)
          && (!node.interaction_id || typeof onInteraction!=="function")) return true;
      if (node.tag==="button" && node.control?.kind==="tab"
          && (!node.interaction_id || typeof onInteraction!=="function")) return true;
      pending.push(...(node.children || []));
    }
    return false;
  }
  function renderNativeSnapshot(host,snapshot,sourceReferences=[],onInteraction=null) {
    if (!host || !validateSnapshot(snapshot)) return false;
    try {
      const state = document.createElement("div");
      state.className = "native-delivery-state";
      state.textContent = (snapshot.finality === "verified-final" ? "ChatGPT 原生内容 · 已验证完成" : "ChatGPT 原生内容 · 实时更新，最终校验中") +
        (hasLocalOnlyControls(snapshot.root,onInteraction) ? " · 部分控件仅在本地预览" : "");
      const root = document.createElement("div"); root.className = "native-rich-root";
      root.appendChild(createNode(snapshot.root,snapshot,0,{radioGroups:new Map(),radioSelection:new Map(),radioIndex:0,tabSelection:new Map(),prefix:"native-"+(++renderSequence),onInteraction,revision:snapshot.revision}));
      const files = document.createElement("div"); files.className = "native-generated-files";
      for (const item of snapshot.files) {
        if (!item || typeof item !== "object" || typeof item.filename !== "string") continue;
        const url = item.status === "ready" ? safeGeneratedFileUrl(item.url) : "";
        const card = document.createElement("div"); card.className = "native-generated-file";
        const mime = typeof item.mime_type === "string" ? item.mime_type.toLowerCase() : "";
        if (url && AUDIO_MIMES.has(mime)) {
          const label = document.createElement("span");
          label.textContent = item.filename.slice(0,180);
          const player = document.createElement("audio");
          player.controls = true; player.preload = "metadata";
          player.src = url; player.referrerPolicy = "no-referrer";
          card.appendChild(label); card.appendChild(player);
        } else if (url && VIDEO_MIMES.has(mime)) {
          const label = document.createElement("span");
          label.textContent = item.filename.slice(0,180);
          const player = document.createElement("video");
          player.controls = true; player.preload = "metadata"; player.playsInline = true;
          player.src = url; player.referrerPolicy = "no-referrer";
          card.appendChild(label); card.appendChild(player);
        } else if (url) {
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
      const references = normalizeSourceReferences(sourceReferences);
      if (references.length) {
        const section = document.createElement("section");
        section.className = "native-source-references";
        const heading = document.createElement("div");
        heading.className = "native-source-references-title";
        heading.textContent = "引用来源";
        section.appendChild(heading);
        references.forEach((reference,index) => {
          const link = document.createElement("a");
          link.className = "native-source-reference";
          link.href = reference.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer nofollow";
          link.referrerPolicy = "no-referrer";
          const title = document.createElement("strong");
          title.textContent = "[" + (index + 1) + "] " + reference.title;
          link.appendChild(title);
          if (reference.snippet) {
            const snippet = document.createElement("span");
            snippet.textContent = reference.snippet;
            link.appendChild(snippet);
          }
          section.appendChild(link);
        });
        host.appendChild(section);
      }
      return true;
    } catch (_) { return false; }
  }
  window.Chat2APINativeRendererV1 = Object.freeze({validateSnapshot,renderNativeSnapshot,safeMediaUrl,safeGeneratedFileUrl,safeExternalLinkUrl});
})();