/* Non-Voice native rich reply observer. No script/raw HTML/CSS or media URLs leave the tab.
 * Only public HTTPS citation hrefs are retained as inert, user-clicked links.
 * Only the same request-v6 assistant candidate is eligible. This observer may
 * fail without changing chat.completed, delta, cancellation or window lifetime.
 */
(() => {
  "use strict";
  const KEY = "__CHAT2API_NATIVE_CONTENT_V1__";
  if (globalThis[KEY]) return;
  const nativeControlTargetsByRequest = new Map();
  const nativeControlLatestRevision = new Map();
  const nativeControlAppliedActions = new Map();
  const nativeControlCaptureContexts = new Map();
  const tags = new Set("div p span strong em h1 h2 h3 h4 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br a details summary input select option button".split(" "));
  // Do not serialize MathML as HTML div/span descendants; its parent structure
  // and unit labels require the MathML namespace through the server and UI.
  const mathTags = new Set("math semantics mrow mi mo mn msub mfrac mtext annotation mover munder munderover msup msubsup msqrt mroot mspace mstyle mtable mtr mtd mmultiscripts mphantom menclose mpadded merror ms mfenced mprescripts none mstack mlongdiv msgroup msrow mscarries mscarry msline".split(" "));
  const mathTokens = new Set("mi mo mn mtext annotation".split(" "));
  const mathNamespace = "http://www.w3.org/1998/Math/MathML";
  // SVG is vector data, not raw HTML. Only inert geometry/axis labels and
  // internal clip references can leave the trusted assistant reply scope.
  const svgNS="http://www.w3.org/2000/svg";
  // Chrome 156+ emits these CSS Color 4 forms on ChatGPT cards/charts.
  // Accept ONLY bounded numeric oklab/oklch and color(srgb), never CSS
  // variables, relative colors, nested functions or external url() values.
  const modernColor=/^(?:(?:oklab|oklch)\(\s*[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+(?:\s*\/\s*\d*\.?\d+)?\s*\)|color\(srgb\s+\d*\.?\d+\s+\d*\.?\d+\s+\d*\.?\d+(?:\s*\/\s*\d*\.?\d+)?\s*\))$/i;

  const svgTags=new Set("svg g path line rect circle ellipse polyline polygon text tspan title desc defs clippath".split(" "));
  const svgAttrs=new Set("viewBox width height preserveAspectRatio x y dx dy x1 x2 y1 y2 cx cy r rx ry d points transform fill stroke stroke-width stroke-linecap stroke-linejoin stroke-dasharray opacity fill-opacity stroke-opacity text-anchor dominant-baseline font-size font-weight id clip-path".split(" "));
  const svgNumber=/^[-+]?(?:\d{1,6}(?:\.\d{1,5})?|\.\d{1,5})(?:px|%)?$/;
  const svgGeometry=/^[MmLlHhVvCcSsQqTtAaZzEe0-9+.,\-\s]+$/;
  const svgNumbers=/^[0-9eE+.,\-\s]+$/;
  const svgColor=/^(?:none|currentColor|transparent|black|white|red|blue|green|gray|grey|#[\da-f]{3,8}|rgba?\([\d.,\s%]+\))$/i;
  const svgTransform=/^(?:(?:translate|scale|rotate|matrix|skewX|skewY)\([-+0-9.,\s]+\)\s*){1,8}$/;
  const svgTokenId=/^[A-Za-z_][A-Za-z0-9_-]{0,80}$/;
  const svgClip=/^url\(#([A-Za-z_][A-Za-z0-9_-]{0,80})\)$/;
  // Recharts may style ticks/lines with safe CSS instead of SVG attributes.
  // Capture only bounded, visual-only computed values from the same node.
  const svgComputedKeys=new Set(["fill","stroke","stroke-width","font-size",
    "font-weight","opacity","text-anchor"]);
  function svgSafeAttributes(element,computedStyle){
    const result={};
    const computed=computedStyle||getComputedStyle(element);
    for(const key of svgAttrs){
      let raw=element.getAttribute?.(key);
      if((raw===null||raw===undefined||raw==="")&&svgComputedKeys.has(key)){
        raw=computed.getPropertyValue?.(key)||computed[key]||"";
      }
      const v=safeSvgAttr(key,raw);
      if(v)result[key]=v;
    }
    return result;
  }
  function safeExternalLinkUrl(raw) {
    if (typeof raw !== "string" || !raw || raw.length > 2048 || raw !== raw.trim()
        || /[\u0000-\u001f\u007f]/.test(raw)) return "";
    try {
      const url = new URL(raw);
      const host = url.hostname.toLowerCase().replace(/\.$/, "");
      if (url.protocol !== "https:" || url.username || url.password ||
          (url.port && url.port !== "443") || !host || host.includes(":") ||
          /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) return "";
      if (["chatgpt.com","chat.openai.com","localhost"].includes(host) ||
          /(?:^|\.)(?:chatgpt\.com|localhost|local|internal|test|invalid|example|onion)$/.test(host)) return "";
      if (!/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host)) return "";
      return url.href;
    } catch (_) { return ""; }
  }

  function safeSvgAttr(key,raw){
    if(!svgAttrs.has(key)||typeof raw!=="string")return "";
    const v=raw.trim();
    if(!v||v.length>(key==="d"?16000:key==="points"?4096:240))return "";
    if(key==="d")return svgGeometry.test(v)?v:"";
    if(["points","stroke-dasharray","viewBox"].includes(key))return svgNumbers.test(v)?v:"";
    if(key==="transform")return svgTransform.test(v)?v:"";
    if(["fill","stroke"].includes(key))return (svgColor.test(v)||modernColor.test(v))?v:"";
    if(key==="id")return svgTokenId.test(v)?v:"";
    if(key==="clip-path")return svgClip.test(v)?v:"";
    if(key==="preserveAspectRatio")return /^(?:none|x(?:Min|Mid|Max)Y(?:Min|Mid|Max))(?:\s+(?:meet|slice))?$/.test(v)?v:"";
    if(key==="stroke-linecap")return ["butt","round","square"].includes(v)?v:"";
    if(key==="stroke-linejoin")return ["miter","round","bevel"].includes(v)?v:"";
    if(key==="text-anchor")return ["start","middle","end"].includes(v)?v:"";
    if(key==="dominant-baseline")return ["auto","middle","central","hanging","alphabetic","text-after-edge"].includes(v)?v:"";
    if(key==="font-weight")return ["normal","bold","400","500","600","700"].includes(v)?v:"";
    return svgNumber.test(v)?v:"";
  }
  const banned = "button,script,style,form,nav,header,footer,aside,textarea,iframe,canvas,[role='menu'],[contenteditable='true']";
  const styleKeys = {
    display: ["block","inline","inline-block","flex","inline-flex","grid","block math","inline math"],
    flexDirection: ["row","row-reverse","column","column-reverse"],
    flexWrap: ["wrap","nowrap"],
    justifyContent: ["start","center","end","space-between","space-around"],
    alignItems: ["start","center","end","stretch","baseline"],
    objectFit: ["contain","cover","fill","none"],
    textAlign: ["left","center","right","justify","start","end"],
    fontWeight: ["400","500","600","700"],
    whiteSpace: ["normal","nowrap","pre","pre-wrap","pre-line","break-spaces"],
    wordBreak: ["normal","break-all","keep-all","break-word"],
    overflowWrap: ["normal","break-word","anywhere"],
    verticalAlign: ["baseline","middle","top","bottom","sub","super"],
    textDecorationLine: ["none","underline","line-through","overline"],
    borderStyle: ["none","solid","dashed","dotted","double"],
    boxSizing: ["border-box","content-box"],
    borderCollapse: ["collapse","separate"],
    alignSelf: ["auto","start","center","end","stretch","baseline"],
    justifySelf: ["auto","start","center","end","stretch"],
  };
  const allowedLength = new Set(["gap","rowGap","columnGap","padding","marginTop","marginBottom",
    "marginLeft","marginRight","width","minWidth","maxWidth","height","minHeight",
    "maxHeight","borderRadius","borderWidth","letterSpacing","fontSize","lineHeight"]);
  const length = /^(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|%))$/;
  // Actual table cells in the 2026-11-05 Windows/Linux evidence use
  // e.g. padding:8px 8px 8px 0px. Keep at most four safe length tokens.
  const paddingShorthand = /^(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|%))(?:\s+(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|%))){0,3}$/;
  // Table border spacing is a bounded one/two-value CSS length, never CSS text.
  const borderSpacing = /^(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|%))(?:\s+(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|%)))?$/;
  const color = /^(?:#[0-9a-f]{3,8}|rgba?\([0-9.,\s]+\))$/i;
  const grid = /^(?:\d{1,4}(?:\.\d{1,2})?(?:px|%)|minmax\(\d{1,4}(?:\.\d{1,2})?px,\s*1fr\)|1fr)(?:\s+(?:\d{1,4}(?:\.\d{1,2})?(?:px|%)|minmax\(\d{1,4}(?:\.\d{1,2})?px,\s*1fr\)|1fr)){0,5}$/;
  // Server validates at most 32 style keys per element. Chromium may emit
  // more, so prioritize actual visual styling before optional dimensions.
  const cssList = [...new Set([
    "display","gridTemplateColumns","flexDirection","flexWrap",
    "justifyContent","alignItems","objectFit","textAlign",
    "color","backgroundColor","borderColor","fontWeight",
    "fontSize","lineHeight","whiteSpace","wordBreak","overflowWrap",
    "gap","rowGap","columnGap","padding",
    "boxSizing","borderCollapse","borderSpacing",
    "marginTop","marginBottom",
    "marginLeft","marginRight","width","minWidth","maxWidth",
    "height","minHeight","maxHeight","borderRadius","borderWidth",
    ...Object.keys(styleKeys),...allowedLength,
  ])];
  function safeStyle(element) {
    const css = getComputedStyle(element), obj = {};
    for (const key of cssList) {
      if (Object.keys(obj).length >= 32) break;
      const value = String(css[key] || "").trim();
      if (styleKeys[key]?.includes(value) ||
          allowedLength.has(key) && value.length <= 16 && length.test(value) ||
          key === "padding" && value.length <= 64 && paddingShorthand.test(value) ||
          key === "borderSpacing" && element.tagName?.toLowerCase()==="table"
            && value.length <= 36 && borderSpacing.test(value) ||
          ["color","backgroundColor","borderColor"].includes(key) && value.length <= 96 && (color.test(value)||modernColor.test(value)) ||
          key === "gridTemplateColumns" && value.length <= 120 && grid.test(value)) {
        obj[key] = value;
      }
    }
    return obj;
  }
  // MathML presentation attributes carry layout semantics that cannot be
  // reconstructed from text nodes alone (fraction rules, stretchy operators,
  // script sizing, matrix alignment and spacing). Capture only a narrow,
  // tag-aware allowlist; never forward arbitrary DOM attributes.
  const mathGlobalAttrs = new Set([
    "mathvariant","mathsize","mathcolor","mathbackground","displaystyle","scriptlevel","dir",
  ]);
  const mathAttrsByTag = {
    math:["display"],
    mo:["stretchy","fence","separator","symmetric","largeop","movablelimits","accent","form","lspace","rspace","minsize","maxsize"],
    mfrac:["linethickness","numalign","denomalign","bevelled"],
    msub:["subscriptshift"],msup:["superscriptshift"],msubsup:["subscriptshift","superscriptshift"],
    munder:["accentunder"],mover:["accent"],munderover:["accent","accentunder"],
    mspace:["width","height","depth"],
    mfenced:["open","close","separators"],
    menclose:["notation"],
    mtable:["align","rowalign","columnalign","rowspacing","columnspacing","frame","framespacing","equalrows","equalcolumns","side","minlabelspacing","columnwidth"],
    mtr:["rowalign","columnalign","groupalign"],
    mtd:["rowalign","columnalign","groupalign","rowspan","columnspan"],
    mpadded:["width","height","depth","lspace","voffset"],
    annotation:["encoding"],
  };
  const mathBooleanAttrs = new Set([
    "displaystyle","stretchy","fence","separator","symmetric","largeop",
    "movablelimits","accent","accentunder","bevelled","equalrows","equalcolumns",
  ]);
  const mathEnums = {
    mathvariant:["normal","bold","italic","bold-italic","double-struck","bold-fraktur",
      "script","bold-script","fraktur","sans-serif","bold-sans-serif",
      "sans-serif-italic","sans-serif-bold-italic","monospace","initial","tailed","looped","stretched"],
    mathsize:["small","normal","big"],
    display:["block","inline"],
    dir:["ltr","rtl"],
    form:["prefix","infix","postfix"],
    numalign:["left","center","right"],denomalign:["left","center","right"],
    align:["top","bottom","center","baseline","axis","left","right"],
    frame:["none","solid","dashed"],side:["left","right"],
    encoding:["application/x-tex","application/mathml-presentation+xml",
      "application/mathml-content+xml","text/plain"],
  };
  const mathLengthAttrs = new Set([
    "mathsize","width","height","depth","lspace","rspace","minsize","maxsize",
    "linethickness","subscriptshift","superscriptshift","minlabelspacing","voffset",
  ]);
  const mathLength = /^(?:[+-]?(?:\d{1,4}(?:\.\d{1,3})?|\.\d{1,3})(?:em|ex|px|pt|pc|in|cm|mm|%)|0)$/;
  const mathSpaceNames = new Set([
    "veryverythinmathspace","verythinmathspace","thinmathspace","mediummathspace",
    "thickmathspace","verythickmathspace","veryverythickmathspace",
  ]);
  const mathColor = /^(?:#[0-9a-f]{3,8}|rgba?\([0-9.,\s%]+\)|black|white|red|green|blue|gray|grey|transparent|currentColor)$/i;
  const mathAlignValues = {
    rowalign:new Set(["top","bottom","center","baseline","axis"]),
    columnalign:new Set(["left","center","right"]),
    groupalign:new Set(["left","center","right","decimalpoint"]),
  };
  const mathNotation = new Set(["longdiv","actuarial","radical","box","roundedbox","circle",
    "left","right","top","bottom","updiagonalstrike","downdiagonalstrike",
    "verticalstrike","horizontalstrike","madruwb"]);
  function safeMathAttr(tag,key,raw) {
    const local = mathAttrsByTag[tag] || [];
    if (!mathGlobalAttrs.has(key) && !local.includes(key)) return null;
    if (typeof raw !== "string" || raw !== raw.trim() || raw.length > 160 ||
        /[\u0000-\u001f\u007f]/.test(raw)) return null;
    if (mathBooleanAttrs.has(key)) return ["true","false"].includes(raw) ? raw : null;
    if (mathEnums[key]) return mathEnums[key].includes(raw) ? raw : null;
    if (key === "scriptlevel") {
      if (!/^[+-]?\d{1,2}$/.test(raw)) return null;
      const number = Number(raw);
      return Number.isInteger(number) && number >= -10 && number <= 10 ? raw : null;
    }
    if (key === "mathcolor" || key === "mathbackground") return mathColor.test(raw) || modernColor.test(raw) ? raw : null;
    if (mathLengthAttrs.has(key)) {
      if (mathSpaceNames.has(raw) || ["thin","medium","thick","infinity","auto","fit"].includes(raw)) return raw;
      if (key === "mathsize" && ["small","normal","big"].includes(raw)) return raw;
      return mathLength.test(raw) ? raw : null;
    }
    if (["rowspacing","columnspacing","framespacing","columnwidth"].includes(key)) {
      const tokens = raw.split(/\s+/);
      if (tokens.length < 1 || tokens.length > 16) return null;
      return tokens.every(token => token === "auto" || token === "fit" ||
        mathSpaceNames.has(token) || mathLength.test(token)) ? raw : null;
    }
    if (["rowalign","columnalign","groupalign"].includes(key)) {
      const tokens = raw.split(/\s+/), allowed = mathAlignValues[key];
      return tokens.length <= 16 && tokens.every(token => allowed.has(token)) ? raw : null;
    }
    if (key === "notation") {
      const tokens = raw.split(/\s+/);
      return tokens.length >= 1 && tokens.length <= 8 && tokens.every(token => mathNotation.has(token)) ? raw : null;
    }
    if (key === "rowspan" || key === "columnspan") {
      if (!/^\d{1,3}$/.test(raw)) return null;
      const number = Number(raw);
      return number >= 1 && number <= 256 ? raw : null;
    }
    if (key === "open" || key === "close" || key === "separators") {
      return /^[^<>\u0000-\u001f\u007f]{0,16}$/.test(raw) ? raw : null;
    }
    return null;
  }
  function safeMathAttributes(element) {
    const tag = String(element.tagName || "").toLowerCase();
    const result = {};
    const keys = [...mathGlobalAttrs,...(mathAttrsByTag[tag] || [])];
    for (const key of keys) {
      const raw = element.getAttribute?.(key);
      if (raw === null || raw === undefined) continue;
      const value = safeMathAttr(tag,key,raw);
      if (value !== null) result[key] = value;
    }
    return result;
  }

  // Width is bounded by the 30k-node and 6 MiB budgets too. A 160-child
  // fanout rejected the final 3a882b reply while a stale 333-node snapshot
  // remained published. Preserve DOM order/layout without synthetic wrappers.
  const MAX_NATIVE_CHILDREN = 8192;
  const MAX_NATIVE_DEPTH = 64;
  // Use the browser's composed-visible subtree, not only childNodes:
  // a chart/image can be entirely inside an OPEN Shadow DOM. A <slot>
  // renders assigned light-DOM nodes in place; never collect those twice.
  // Closed shadows and iframe contents remain unsupported/opaque.
  function composedChildren(element) {
    if (!(element instanceof Element)) return [...(element?.childNodes || [])];
    if (element.tagName?.toLowerCase()==="slot") {
      try {
        const assigned=element.assignedNodes?.({flatten:true});
        if (assigned?.length) return [...assigned];
      } catch (_) {}
    }
    const shadow=element.shadowRoot;
    return shadow?.mode==="open"
      ? [...(shadow.childNodes || [])] : [...(element.childNodes || [])];
  }
  function composedFind(root, accept, limit=40) {
    const found=[], seen=new Set(), stack=[{node:root,depth:0}];
    let visited=0;
    while(stack.length && found.length<limit && visited<30000){
      const {node,depth}=stack.pop();
      if(!(node instanceof Element)||seen.has(node))continue;
      seen.add(node);visited++;
      if(node!==root && node.matches?.(
          "nav,aside,header,footer,form,[role='menu'],[role='dialog'],[contenteditable='true'],[data-message-author-role='user']"))continue;
      if(accept(node))found.push(node);
      if(depth>=MAX_NATIVE_DEPTH)continue;
      const children=composedChildren(node);
      for(let index=children.length-1;index>=0;index--)
        stack.push({node:children[index],depth:depth+1});
    }
    return found;
  }
  function controlLabel(element) {
    const labelled=[...(element.labels || [])]
      .map(label=>String(label.innerText || label.textContent || "").replace(/\s+/g," ").trim())
      .filter(Boolean).join(" ");
    const raw=element.getAttribute?.("aria-label") || element.getAttribute?.("title")
      || labelled || element.closest?.("label")?.textContent || "";
    return String(raw).replace(/\s+/g," ").trim().slice(0,160);
  }
  function plainText(node) {
    if (!node) return "";
    if (typeof node.innerText==="string" && node.innerText) return node.innerText;
    if (typeof node.textContent==="string" && node.textContent) return node.textContent;
    return [...(node.childNodes || [])].map(child=>child.nodeType===Node.TEXT_NODE
      ? String(child.nodeValue || "") : plainText(child)).join("");
  }
  function copyCodeForButton(button) {
    let scope=button.parentElement;
    for(let depth=0;scope && depth<4;depth++,scope=scope.parentElement) {
      const blocks=composedFind(scope,node=>node.tagName?.toLowerCase()==="pre",3);
      if(blocks.length>1) return "";
      if(blocks.length===1) {
        const code=composedFind(blocks[0],node=>node.tagName?.toLowerCase()==="code",2)[0] || blocks[0];
        const text=String(code.textContent ?? code.innerText ?? "");
        if(text && text.length<=150000) return text;
        return "";
      }
    }
    return "";
  }
  function safeOptionText(value,limit=128) {
    return typeof value==="string" && value.length<=limit &&
      !/[\u0000-\u001f\u007f]/.test(value);
  }
  function snapshot(active) {
    if (active) { active.nativeStructuralFailure = ""; active.nativeStructuralMaxChildren = 0; active.nativeSvgChartsCaptured = 0; active.nativeGraphicsUnsupported = 0; active.nativeCanvasOmitted = 0; active.nativeCodeEditorCaptured = 0; }
    if (!active?.submitStartedAt || typeof Element === "undefined") return null;
    const candidate = active.lastCandidateNode;
    if (!(candidate instanceof Element) || !candidate.isConnected) {
      active.nativeStructuralFailure = "reply-candidate-unavailable";
      return null;
    }
    const reply = candidate.closest?.("[data-message-author-role='assistant'],[data-testid^='conversation-turn']")
      || candidate;
    if (reply.matches?.("[data-message-author-role='user']") || reply.closest?.("nav,aside")) return null;
    let nodes = 0, chars = 0, images = 0, canvasRasters = 0;
    // Sample only the already-proven reply subtree. Never request a context
    // from the original canvas (which could mutate WebGL/2D state).
    const captureCanvas = element => {
      if (!scopedRoots?.length || canvasRasters >= 4 || typeof document === "undefined" ||
          element.closest?.("form,nav,aside,header,footer,[data-message-author-role='user']")) return null;
      const rect = element.getBoundingClientRect?.();
      const width = Number(element.width), height = Number(element.height);
      if (!rect || rect.width < 40 || rect.height < 40 || !Number.isInteger(width) ||
          !Number.isInteger(height) || width < 1 || height < 1 || width > 16384 || height > 16384) return null;
      const style = getComputedStyle(element);
      if (style.display==="none" || style.visibility==="hidden" || style.opacity==="0") return null;
      try {
        const output = document.createElement("canvas");
        const scale = Math.min(1,1024/width,1024/height);
        output.width = Math.max(1,Math.round(width*scale));
        output.height = Math.max(1,Math.round(height*scale));
        const ctx = output.getContext("2d",{willReadFrequently:true});
        if (!ctx) return null;
        ctx.drawImage(element,0,0,output.width,output.height);
        // A cleared WebGL drawing buffer can produce transparent/blank
        // pixels. Never certify a uniform image as captured content.
        const pixels = ctx.getImageData(0,0,output.width,output.height).data;
        let visible = false, varied = false;
        for (let i=0;i<pixels.length;i+=4) {
          if (pixels[i+3]) visible=true;
          if (pixels[i]!==pixels[0] || pixels[i+1]!==pixels[1] ||
              pixels[i+2]!==pixels[2] || pixels[i+3]!==pixels[3]) varied=true;
          if (visible && varied) break;
        }
        if (!visible || !varied) return null;
        const uri = output.toDataURL("image/png");
        const png = uri.startsWith("data:image/png;base64,") ? uri.slice(22) : "";
        if (!png || png.length > 699052 || !/^[A-Za-z0-9+/]+={0,2}$/.test(png)) return null;
        canvasRasters++;
        return {tag:"canvas_raster",png,width:output.width,height:output.height};
      } catch (_) { return null; } // tainted canvas, inaccessible or lost context
    };
    let truncated = false;
    const revision = Math.max(active.nativeRevision || 0, active.nativeSentRevision || 0) + 1;
    const controlTargets = new Map();
    let controlIndex = 0;
    const tabGroupIds = new Map();
    let tabGroupIndex = 0;
    const tabGroupId = tabList => {
      if (!tabList || tabGroupIndex >= 9999) return "";
      if (!tabGroupIds.has(tabList)) tabGroupIds.set(tabList,"tg_"+(++tabGroupIndex));
      return tabGroupIds.get(tabList);
    };
    const bindControl = (element, kind) => {
      if (revision > 999999 || controlIndex >= 9999) return null;
      const id = "nc_" + revision + "_" + (++controlIndex);
      controlTargets.set(id, {element, kind});
      return id;
    };
    // 17b4 exceeded the previous 3,200-node budget while the rich reply
    // was still growing; the hard byte/depth/text budgets remain unchanged.
    const textBudget = 300000;
    const ignore = element => element.matches?.(banned) || !!element.closest?.("nav,aside,footer");
    const build = (element, depth, inMath = false, inSvg = false, inCode = false) => {
      if (depth > MAX_NATIVE_DEPTH || nodes >= 30000) {
        active.nativeStructuralFailure = depth > MAX_NATIVE_DEPTH ? "max-depth" : "max-nodes";
        truncated = true;
        return null;
      }
      if (element.nodeType === Node.TEXT_NODE) {
        const raw = String(element.nodeValue || "");
        // Windows v1.0.4 verified that code blocks may carry whitespace-only
        // newline/indent text nodes. Never discard them inside <pre>/<code>.
        if (!raw.trim() && !inCode) return null;
        if (chars + raw.length > textBudget) {
          active.nativeStructuralFailure = "max-text";
          truncated = true;
          return null;
        }
        chars += raw.length; nodes += 1;
        // Standalone text under a MathML container cannot be wrapped in an
        // HTML span: the service rejects foreign HTML inside MathML.
        return {tag:inMath ? "mtext" : inSvg ? "tspan" : "span",text:raw};
      }
      if (!(element instanceof Element)) return null;
      const tag = element.tagName.toLowerCase();
      // Generated-image cards AND charts can be rendered inside inert-looking
      // <button> wrappers. Never copy control labels, action attributes, or
      // handlers. Preserve the actual IMG and TOP-LEVEL SVG roots in DOM order.
      // An earlier predicate matched every SVG descendant (path/g/rect), thus
      // duplicating graphics and exhausting the 40-candidate cap before a
      // later image. Apply visibility before the bounded match limit.
      const semanticRole=String(element.getAttribute?.("role") || "").toLowerCase();
      if (semanticRole === "tab") {
        const tabList=element.closest?.("[role='tablist']");
        const selectedRaw=element.getAttribute?.("aria-selected");
        const label=controlLabel(element) || plainText(element).replace(/\s+/g," ").trim();
        if (!tabList || !label || label.length>160
            || !["true","false"].includes(selectedRaw)
            || (tag==="button" && element.form)
            || element.closest?.("nav,aside,footer,header,form,[role='menu'],[role='dialog']")) return null;
        const group=tabGroupId(tabList);
        if (!group) return null;
        if(chars+label.length>textBudget) {
          active.nativeStructuralFailure="interactive-tab-label-budget";
          truncated=true;
          return null;
        }
        chars+=label.length;
        nodes+=1;
        const disabled=Boolean(element.disabled || element.matches?.(":disabled")
          || element.getAttribute?.("aria-disabled")==="true");
        const interactionId=bindControl(element,"tab");
        if(!interactionId)return null;
        return {
          tag:"button",
          interaction_id:interactionId,
          style:safeStyle(element),
          control:{kind:"tab",label,disabled,selected:selectedRaw==="true",group},
        };
      }
      if (tag === "button") {
        if (element.closest?.("nav,aside,footer,header,form,[role='menu'],[role='dialog']"))
          return null;
        const pressedRaw=element.getAttribute?.("aria-pressed");
        if (pressedRaw==="true" || pressedRaw==="false") {
          const label=controlLabel(element) || plainText(element).replace(/\s+/g," ").trim();
          const computed=getComputedStyle(element);
          if (!label || label.length>160 || element.form || computed.display==="none" || computed.visibility==="hidden")
            return null;
          if(chars+label.length>textBudget) {
            active.nativeStructuralFailure="interactive-toggle-label-budget";
            truncated=true;
            return null;
          }
          chars+=label.length;
          nodes+=1;
          const disabled=Boolean(element.disabled || element.matches?.(":disabled")
            || element.getAttribute?.("aria-disabled")==="true");
          const interactionId=bindControl(element,"toggle");
          if(!interactionId)return null;
          return {
            tag:"button",
            interaction_id:interactionId,
            style:safeStyle(element),
            control:{kind:"toggle",label,disabled,pressed:pressedRaw==="true"},
          };
        }
        const label=controlLabel(element) || plainText(element).replace(/\s+/g," ").trim().slice(0,80);
        if (/^(?:copy(?: code)?|复制(?:代码)?)$/i.test(label)) {
          const text=copyCodeForButton(element);
          if(text) {
            // The server counts the visible control label, not the duplicate
            // clipboard payload (the source code node owns the text budget).
            if(chars+label.length>textBudget) {
              active.nativeStructuralFailure="interactive-copy-label-budget";
              truncated=true;
              return null;
            }
            chars+=label.length;
            nodes+=1;
            return {tag:"button",control:{kind:"copy",
              label:/复制/.test(label)?"复制代码":"Copy code",text}};
          }
        }
        const graphical = composedFind(element,node=>{
          const type=node.tagName?.toLowerCase();
          const raster=(type==="img" || type==="canvas") && !node.closest?.("svg");
          const vector=type==="svg" && node.namespaceURI===svgNS
            && !node.parentElement?.closest?.("svg");
          if (!raster && !vector) return false;
          const box=node.getBoundingClientRect?.();
          if (!box || box.width<(vector?120:40) || box.height<(vector?80:40)
              || node.closest?.("nav,aside,footer,[data-message-author-role='user']"))
            return false;
          const style=getComputedStyle(node);
          return style.display!=="none" && style.visibility!=="hidden";
        },41);
        if (graphical.length>40) {
          active.nativeStructuralFailure="button-graphic-cap";
          truncated=true;
          return null;
        }
        // Keep accounting for omitted canvases even when their wrapper
        // is not a recognized graphical button (hidden/tiny/unsafe).
        const canvases=composedFind(element,node=>
          node.tagName?.toLowerCase()==="canvas",41);
        if (canvases.length>40) {
          active.nativeStructuralFailure="button-canvas-cap";
          truncated=true;
          return null;
        }
        for (const canvas of canvases) {
          if (!graphical.includes(canvas)) {
            active.nativeCanvasOmitted++;
            active.nativeGraphicsUnsupported++;
          }
        }
        if (!graphical.length) return null;
        const children=[];
        for(const media of graphical){
          const mapped=build(media,depth+1);
          if(mapped)children.push(mapped);
          if(truncated)break;
        }
        if(!children.length || truncated)return null;
        nodes+=1;
        return {tag:"figure",children};
      }
      if(tag==="canvas"||tag==="iframe"){
        if(tag==="canvas"){
          const raster=captureCanvas(element);
          if (raster) { nodes++; return raster; }
          active.nativeCanvasOmitted++;
        }
        active.nativeGraphicsUnsupported++;
        return null;
      }
      if (ignore(element)) return null;
      if (element.closest?.("form")) return null;
      const controlStyle=getComputedStyle(element);
      if(controlStyle.display==="none" || controlStyle.visibility==="hidden") return null;
      if(tag==="input") {
        const kind=String(element.type || element.getAttribute?.("type") || "text").toLowerCase();
        const label=controlLabel(element);
        if(!label) return null;
        if(chars+label.length>textBudget) {
          active.nativeStructuralFailure="interactive-input-label-budget";
          truncated=true;
          return null;
        }
        chars+=label.length;
        const disabled=Boolean(element.disabled || element.matches?.(":disabled"));
        let control=null;
        if(kind==="checkbox") control={kind,label,disabled,checked:Boolean(element.checked)};
        else if(kind==="radio") {
          const group=String(element.name || element.getAttribute?.("name") || "");
          if(!/^[A-Za-z0-9_-]{1,64}$/.test(group)) return null;
          control={kind,label,disabled,checked:Boolean(element.checked),group};
        } else if(kind==="range") {
          const number=(property,attribute,fallback)=>{
            const raw=element[property] ?? element.getAttribute?.(attribute);
            return raw===null || raw===undefined || raw==="" ? fallback : Number(raw);
          };
          const min=number("min","min",0),max=number("max","max",100),
            step=number("step","step",1),value=Number(element.value);
          if(![min,max,step,value].every(Number.isFinite) ||
             Math.abs(min)>1000000 || Math.abs(max)>1000000 ||
             Math.abs(step)>1000000 || Math.abs(value)>1000000 ||
             min>=max || step<=0 || value<min || value>max) return null;
          control={kind,label,disabled,min,max,step,value};
        } else return null;
        nodes+=1;
        const interactionId = bindControl(element,kind);
        return {tag:"input",...(interactionId?{interaction_id:interactionId}:{}),style:safeStyle(element),control};
      }
      if(tag==="select") {
        const selectLabel=controlLabel(element);
        if(element.multiple || !selectLabel) return null;
        const options=[...(element.options || [])];
        if(!options.length || options.length>64) return null;
        const children=[];
        for(const option of options) {
          const value=String(option.value ?? option.getAttribute?.("value") ?? "");
          const label=String(option.label || plainText(option)).replace(/\s+/g," ").trim();
          if(!safeOptionText(value) || !safeOptionText(label) || !label) return null;
          chars+=label.length;
          children.push({tag:"option",text:label,control:{
            kind:"option",value,disabled:Boolean(option.disabled || option.matches?.(":disabled")),selected:Boolean(option.selected),
          }});
        }
        const selected=String(element.value ?? "");
        if(!safeOptionText(selected) || !options.some(option=>String(option.value ?? "")===selected)) return null;
        if(chars+selectLabel.length>textBudget) {
          active.nativeStructuralFailure="interactive-select-text-budget";
          truncated=true;
          return null;
        }
        chars+=selectLabel.length;
        nodes+=children.length+1;
        const interactionId = bindControl(element,"select");
        return {tag:"select",...(interactionId?{interaction_id:interactionId}:{}),style:safeStyle(element),control:{
          kind:"select",label:selectLabel,disabled:Boolean(element.disabled || element.matches?.(":disabled")),selected,
        },children};
      }
      if(tag==="option") return null;
      const rect = element.getBoundingClientRect?.();
      const computed = getComputedStyle(element);
      if (computed.display === "none" || computed.visibility === "hidden") return null;
      const svgElement=element.namespaceURI===svgNS;
      if(svgElement&&!svgTags.has(tag)||inSvg&&!svgElement){
        active.nativeGraphicsUnsupported++;
        return null;
      }
      if(svgElement&&tag==="svg"&&!inSvg){
        if(!rect||rect.width<120||rect.height<80)return null;
        active.nativeSvgChartsCaptured++;
      }
      const mathElement = element.namespaceURI === mathNamespace;
      const legacyMathDiv = mathElement && tag === "div";
      const mathTextOnly = mathElement && [...element.childNodes]
        .every(n => n.nodeType === Node.TEXT_NODE);
      // Earlier frozen fixtures used MathML-namespaced div as a fallback for
      // unsupported MathML. Map only these inert nodes, never arbitrary HTML.
      const mapped = svgElement && svgTags.has(tag) ? tag : mathElement && mathTags.has(tag) ? tag
        : legacyMathDiv ? (mathTextOnly ? "mtext" : "mrow")
        : mathElement ? (mathTextOnly ? "mtext" : "mrow")
        : inMath ? "mtext"
        : tags.has(tag) ? tag : "div";
      // ChatGPT may render a generated code block through an editor view,
      // with its visible lines under contenteditable=true. The ordinary
      // sanitizer deliberately excludes editable inputs, which silently
      // dropped the entire Python block in req_5692be71 while keeping the
      // "Python" card title. Only read explicitly recognized editor *display*
      // lines INSIDE the already verified assistant reply scope. Never
      // include a generic form/input/editor or copy executable markup.
      const codeEditor = tag === "div" && !!element.matches?.(".cm-editor,.monaco-editor");
      if (codeEditor) {
        if (element.closest?.(
          "form,nav,aside,footer,[role='dialog'],[role='menu'],[data-message-author-role='user']"
        )) return null;
        const cmLines = [...(element.querySelectorAll?.(".cm-content .cm-line") || [])];
        const monacoLines = cmLines.length ? [] :
          [...(element.querySelectorAll?.(".view-lines .view-line") || [])];
        const lines = cmLines.length ? cmLines : monacoLines;
        // Monaco is commonly viewport-virtualized. Do not claim a full
        // 1:1 snapshot from only currently visible scrolled lines.
        const declaredTotal = Number(
          element.querySelector?.("[aria-rowcount]")?.getAttribute?.("aria-rowcount") || 0
        );
        // Both Monaco and CodeMirror 6 can render only viewport lines.
        // A CodeMirror cm-gap is explicit evidence of offscreen omissions.
        const cmVirtual = cmLines.length > 0 &&
          Boolean(element.querySelector?.(".cm-gap"));
        // Monaco without a reliable total-line count is not proven complete.
        // Only accept it when every declared line is present, or when the
        // scrollable source region fits in the visible viewport.
        const monacoViewport = element.querySelector?.(".monaco-scrollable-element");
        const fitsViewport = Boolean(monacoViewport &&
          Number.isFinite(monacoViewport.scrollHeight) &&
          Number.isFinite(monacoViewport.clientHeight) &&
          monacoViewport.scrollHeight <= monacoViewport.clientHeight + 2);
        const monacoVirtual = monacoLines.length > 0 &&
          !((declaredTotal > 0 && declaredTotal <= monacoLines.length) ||
            fitsViewport);
        const trustedState = active.nativeEditorDocs?.get(element);
        if ((!trustedState || !["codemirror-state-v297","react-owned-code-v302"].includes(trustedState.source)) &&
            (!lines.length || lines.length > 10000 || cmVirtual || monacoVirtual)) {
          active.nativeStructuralFailure = cmVirtual || monacoVirtual
            ? "code-editor-virtualized" : "code-editor-content-unavailable";
          truncated = true;
          return null;
        }
        const text = ["codemirror-state-v297","react-owned-code-v302"].includes(trustedState?.source)
          ? trustedState.text : lines.map(line=>String(line.textContent || "")).join("\n");
        if (typeof text !== "string" || !text.trim() || text.length > 150000 ||
            chars + text.length > textBudget) {
          active.nativeStructuralFailure = "code-editor-text-budget";
          truncated = true;
          return null;
        }
        chars += text.length;nodes += 3;
        active.nativeCodeEditorCaptured = Number(active.nativeCodeEditorCaptured || 0) + 1;
        // Preserve line breaks and spaces in an inert <pre><code> subtree.
        // The server separately validates this same safe tag allowlist.
        return {tag:"pre",style:safeStyle(element),children:[{
          tag:"code",children:[{tag:"span",text}],
        }]};
      }
      if (tag === "img") {
        if (!rect || rect.width < 40 || rect.height < 40) return null;
        if (images >= 40) {
          active.nativeStructuralFailure = "max-images";
          truncated = true;
          return null;
        }
        nodes += 1;
        const result={tag:"img",media_index:images++,style:safeStyle(element)};
        const sourceHash = active.nativeImageDigests?.get(element);
        if (/^[a-f0-9]{64}$/.test(sourceHash || "")) result.source_sha256 = sourceHash;
        // Numeric intrinsic dimensions only. No DOM src, URL, pixel bytes or
        // signed tokens enter this optional matching hint.
        const nw=Number(element.naturalWidth),nh=Number(element.naturalHeight);
        if(Number.isInteger(nw)&&Number.isInteger(nh)
          && nw>0&&nw<=16384&&nh>0&&nh<=16384){
          result.intrinsic_width=nw;result.intrinsic_height=nh;
        }
        return result;
      }
      if(svgElement && (tag==="title" || tag==="desc")) {
        const raw=plainText(element);
        if(!raw) return null;
        if(chars+raw.length>textBudget) {
          active.nativeStructuralFailure="max-text";
          truncated=true;
          return null;
        }
        chars+=raw.length;nodes+=1;
        return {tag:mapped,text:raw};
      }
      if ((mathTokens.has(mapped) || (svgElement && (tag==="text"||tag==="tspan"))) && [...element.childNodes]
          .every(n => n.nodeType === Node.TEXT_NODE)) {
        const raw = String(element.textContent || Array.from(element.childNodes, n => n.nodeValue || "").join(""));
        if (chars + raw.length > textBudget) {
          active.nativeStructuralFailure = "max-text";
          truncated = true;
          return null;
        }
        chars += raw.length; nodes += 1;
        const out = {tag:mapped,style:safeStyle(element)};
        if(mathElement){
          const mathAttrs=safeMathAttributes(element);
          if(Object.keys(mathAttrs).length)out.math_attrs=mathAttrs;
        }
        if(svgElement){
          const attributes=svgSafeAttributes(element,computed);
          if(Object.keys(attributes).length)out.attrs=attributes;
        }
        if (raw) out.text = raw;
        return out;
      }
      const attributes=svgElement?svgSafeAttributes(element,computed):{};
      const children = [];
      const sourceChildren=composedChildren(element);
      active.nativeStructuralMaxChildren = Math.max(
        Number(active.nativeStructuralMaxChildren || 0), sourceChildren.length);
      for (const child of sourceChildren) {
        if (nodes >= 30000 || children.length >= MAX_NATIVE_CHILDREN) {
          active.nativeStructuralFailure = nodes >= 30000 ? "max-nodes" : "max-children";
          truncated = true;
          break;
        }
        const value = build(child, depth + 1, inMath || mathElement, inSvg || svgElement, inCode || tag==="pre" || tag==="code");
        if (value) children.push(value);
      }
      // Empty table cells are structural: deleting them shifts all subsequent
      // columns. These are present in real ChatGPT rich tables, not blank UI.
      if (!children.length && !["hr","br","td","th"].includes(mapped) &&
          !(svgElement && ["path","line","rect","circle","ellipse","polyline","polygon"].includes(mapped))) return null;
      nodes += 1;
      const result = {tag:mapped,style:safeStyle(element)};
      if (semanticRole === "tablist" && tabGroupIds.has(element)) {
        result.control={kind:"tablist",group:tabGroupIds.get(element)};
      }
      if(mapped==="details") {
        result.control={kind:"disclosure",open:Boolean(element.open)};
        const interactionId = bindControl(element,"disclosure");
        if (interactionId) result.interaction_id=interactionId;
      }
      if(mathElement){
        const mathAttrs=safeMathAttributes(element);
        if(Object.keys(mathAttrs).length)result.math_attrs=mathAttrs;
      }
      if (mapped === "a") {
        const href = safeExternalLinkUrl(element.href || element.getAttribute?.("href") || "");
        if (href) result.href = href;
      }
      if (mapped==="td" || mapped==="th") {
        // Preserve browser-normalized table spans, not arbitrary DOM attrs.
        // Only bounds that the server and safe renderer also understand.
        const col=Number(element.colSpan),row=Number(element.rowSpan);
        if (Number.isInteger(col)&&col>1&&col<=256)result.col_span=col;
        if (Number.isInteger(row)&&row>=0&&row<=256&&row!==1)result.row_span=row;
      }
      if(svgElement && Object.keys(attributes).length)result.attrs=attributes;
      if (children.length) result.children = children;
      return result;
    };
    // v1 evidence bridge: the ordinary candidate is often a nested paragraph
    // in the newly rendered rich ChatGPT card. Reuse only a verified fresh
    // assistant-heading sibling scope, never a whole main/history container.
    const boundaryResolver = globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.replyRoots;
    const replyRegion = typeof boundaryResolver === "function"
      ? boundaryResolver(active) : null;
    // In the real extension the boundary resolver is installed in the same
    // manifest as this observer. If it returns null (e.g. the candidate is
    // outside <main>) the OLD fallback serialized candidate.closest(turn)
    // and could publish a partial/unrelated assistant card as a verified final
    // native reply. Null, unknown scopes and empty roots MUST fail closed.
    // An absent legacy test harness may still use its former local fallback;
    // production's present resolver must prove scope before publication.
    if (typeof boundaryResolver === "function"
        && (!replyRegion || ![
          "roleless_reply_siblings",
          "verified_reply_container",
          "verified_native_assistant_selection",
        ].includes(replyRegion.scope))) {
      active.nativeStructuralFailure = replyRegion?.scope === "candidate_only"
        ? "reply-scope-unverified" : "reply-boundary-unavailable";
      return null;
    }
    const scopedRoots = (replyRegion?.scope === "roleless_reply_siblings"
      || (replyRegion?.scope === "verified_reply_container"
          || replyRegion?.scope === "verified_native_assistant_selection"))
      ? replyRegion.roots : null;
    if (typeof boundaryResolver === "function"
        && (!Array.isArray(scopedRoots) || !scopedRoots.length
            || scopedRoots.some(node=>!(node instanceof Element) || !node.isConnected))) {
      active.nativeStructuralFailure = "reply-scope-roots-invalid";
      return null;
    }
    let root = null;
    if (scopedRoots?.length) {
      const children = [];
      for (const scopeNode of scopedRoots) {
        const mapped = build(scopeNode, 1);
        if (mapped) children.push(mapped);
        if (truncated) break;
      }
      if (children.length && !truncated) {
        nodes += 1;
        root = {tag:"div",children};
      }
    } else {
      root = build(reply, 0);
    }
    // A partially serialized tree would silently replace the complete
    // canonical Markdown in MV3C. Reject it rather than publish a lie.
    // The standard OpenAI response continues unaffected.
    if (!root || truncated) {
      if (!active.nativeStructuralFailure)
        active.nativeStructuralFailure = "no-safe-complete-reply-root";
      return null;
    }
    const revisions = nativeControlTargetsByRequest.get(active.requestId) || new Map();
    revisions.set(revision, controlTargets);
    while (revisions.size > 8) revisions.delete(revisions.keys().next().value);
    nativeControlTargetsByRequest.set(active.requestId, revisions);
    nativeControlLatestRevision.set(active.requestId, revision);
    if (scopedRoots?.length && (controlTargets.size || canvasRasters) && !active.nativeGraphicsUnsupported) {
      nativeControlCaptureContexts.set(active.requestId,{active,revision,roots:[...scopedRoots],root,canvasRasters,imageCount:images});
    } else nativeControlCaptureContexts.delete(active.requestId);
    while (nativeControlTargetsByRequest.size > 24) {
      const oldest = nativeControlTargetsByRequest.keys().next().value;
      nativeControlTargetsByRequest.delete(oldest);
      nativeControlLatestRevision.delete(oldest);
      nativeControlCaptureContexts.delete(oldest);
    }
    return {version:1, revision, root};
  }

  const MAX_RAW_BYTES = 6 * 1024 * 1024;
  const CHUNK_BYTES = 64 * 1024;
  // Every chunk must receive the server's own ACK, not just a local socket
  // enqueue. The final ACK must also confirm a committed, validated tree.
  async function deliverChunks(active, tree, bytes, transfer) {
    if (!globalThis.crypto?.subtle || !globalThis.btoa) return null;
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    const sha256 = [...digest].map(x=>x.toString(16).padStart(2,"0")).join("");
    const count = Math.ceil(bytes.length / CHUNK_BYTES);
    if (count < 1 || count > 96) return null;
    let reply;
    for (let index = 0; index < count; index++) {
      if (active.nativeTransferInFlight !== transfer) return null;
      const slice = bytes.subarray(index * CHUNK_BYTES,(index + 1) * CHUNK_BYTES);
      const data = btoa(String.fromCharCode(...slice));
      reply = await chrome.runtime.sendMessage({type:"chat2api.native.ack",event:{
        type:"chat.native.chunk",request_id:active.requestId,revision:tree.revision,
        index,count,byte_length:bytes.length,sha256,data,
      }});
      if (reply?.ok !== true) return reply;
    }
    return reply;
  }

  const IDENTITY_MIMES = new Set(["image/jpeg","image/png","image/webp","image/gif"]);
  // The media archive accepts originals up to 20 MiB. A 4 MiB hash cap
  // silently left otherwise-archived originals without byte identity proof.
  const MAX_IDENTITY_BYTES = 20 * 1024 * 1024;
  const HASH_HEX = /^[a-f0-9]{64}$/;
  async function hashVisibleRaster(img, signal = undefined) {
    const url=String(img.currentSrc || img.getAttribute?.("src") || "");
    if (!url) return "";
    try {
      if (/^https:\/\//i.test(url)) {
        // Existing background allowlist enforces sender/tab and trusted
        // OpenAI/CDN hosts. It never returns image bytes to this observer.
        const result=await chrome.runtime.sendMessage({
          type:"chat2api.media.hash-v2",url,
        });
        return result?.ok && HASH_HEX.test(result.sha256) ? result.sha256 : "";
      }
      if (!/^(blob:|data:image\/)/i.test(url) || !globalThis.crypto?.subtle) return "";
      // Do not fetch an oversized inline base64 URL even if the response
      // omits Content-Length. Images beyond the archive limit cannot match.
      if (/^data:image\//i.test(url) && url.length > 4*Math.ceil(MAX_IDENTITY_BYTES/3)+128)
        return "";
      // Blob/data are resolved only in this already-authorized ChatGPT tab.
      // No original URL, pixel byte buffer or token is sent to telemetry.
      const response=await fetch(url,{credentials:"omit",redirect:"error",...(signal?{signal}:{})});
      const size=Number(response.headers.get("content-length") || 0);
      if (!response.ok || size > MAX_IDENTITY_BYTES) return "";
      const blob=await response.blob();
      if (!IDENTITY_MIMES.has(blob.type.toLowerCase())
          || !blob.size || blob.size>MAX_IDENTITY_BYTES) return "";
      const digest=new Uint8Array(await crypto.subtle.digest(
        "SHA-256",await blob.arrayBuffer()));
      return [...digest].map(x=>x.toString(16).padStart(2,"0")).join("");
    } catch (_) { return ""; }
  }
  async function resolveImageIdentities(active) {
    const candidate=active.lastCandidateNode;
    const scoped=globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.replyRoots?.(active);
    const roots=(scoped?.scope==="roleless_reply_siblings"
       || (scoped?.scope==="verified_reply_container" || scoped?.scope==="verified_native_assistant_selection")) && scoped.roots?.length
      ? scoped.roots : [candidate];
    const images=[];
    // Only count images which the safe native serializer could actually
    // publish: composedFind's match LIMIT must apply AFTER eligibility.
    // Before v342, 40 tiny/hidden icons exhausted traversal before a later
    // true generated image could even be considered for SHA256 matching.
    const eligible=node=>{
      if (!(node instanceof Element) || node.tagName?.toLowerCase()!=="img")
        return false;
      if (node.closest?.(
        "form,nav,aside,footer,header,[role='dialog'],[role='menu'],[data-message-author-role='user']"
      )) return false;
      const box=node.getBoundingClientRect?.();
      if (!box || box.width<40 || box.height<40) return false;
      const style=getComputedStyle(node);
      return style.display!=="none" && style.visibility!=="hidden";
    };
    for (const root of roots) {
      if (!(root instanceof Element) || !root.isConnected) continue;
      const candidates=composedFind(root,eligible,41);
      for (const img of candidates) {
        if (images.includes(img)) continue;
        if (images.length>=40) break;
        images.push(img);
      }
      if (images.length>=40) break;
    }
    const digests=new Map();
    // One slow/never-resolving image hash used to stall ALL later four-wide
    // batches until the 3.5s finality deadline. This starved real original
    // slots later in a long reply. Run 8 bounded concurrent workers; when
    // one image finishes, it immediately proceeds to the next slot.
    // Never accept results after the deadline or published snapshot.
    // Abort only auxiliary blob/data fetches, NEVER the ChatGPT page fetch.
    const deadline=Date.now()+3500;
    const controller=typeof AbortController==="function"
      ?new AbortController():null;
    let accepting=true,next=0,timer=null;
    const work=async()=>{
      while(accepting && Date.now()<deadline){
        const index=next++;
        if(index>=images.length)return;
        const img=images[index];
        try{
          const digest=await hashVisibleRaster(img,controller?.signal);
          if(accepting && Date.now()<=deadline && HASH_HEX.test(digest))
            digests.set(img,digest);
        }catch(_){/* Only verified original bytes may be mapped. */}
      }
    };
    if(images.length){
      try{
        const workers=Array.from({length:Math.min(8,images.length)},()=>work());
        const completed=await Promise.race([
          Promise.allSettled(workers).then(()=>true),
          new Promise(resolve=>{
            timer=setTimeout(()=>resolve(false),Math.max(1,deadline-Date.now()));
          }),
        ]);
        accepting=false;
        if(!completed)controller?.abort();
      }finally{
        accepting=false;
        if(timer!==null)clearTimeout(timer);
      }
    }
    active.nativeImageDigests=digests;
    active.nativeIdentityVerifiedCount=digests.size;
    active.nativeIdentityCandidateCount=images.length;
  }

  const EDITOR_PROBE_ATTR = "data-chat2api-native-code-probe-v297";
  const VERIFIED_EDITOR_SCOPES = new Set([
    "verified_reply_container", "verified_native_assistant_selection",
    "roleless_reply_siblings",
  ]);
  async function resolveVirtualEditorDocs(active) {
    // Only a request-owned verified assistant root may reveal its editor
    // content. This runs in ISOLATED; it cannot read the page JS's cmView.
    // Chrome's SW retrieves the complete CodeMirror state in MAIN world,
    // tied to this exact sender tab and a short-lived random DOM marker.
    active.nativeEditorDocs = new Map();
    active.nativeEditorStateCandidateCount = 0;
    active.nativeEditorStateResolvedCount = 0;
    active.nativeEditorStateFailure = "";
    active.nativeEditorProbeScanned=0;
    active.nativeEditorProbeAttachments=0;
    active.nativeEditorProbeVisibleLines=0;
    active.nativeEditorProbeVirtualized=false;
    active.nativeEditorProbeRetries=0;
    active.nativeEditorReactFiberHosts=0;
    active.nativeEditorReactCandidateCount=0;
    active.nativeEditorReactAnchorCount=0;
    active.nativeEditorReactHookNodes=0;
    active.nativeEditorReactArrayNodes=0;
    active.nativeEditorReactDocumentNodes=0;
    active.nativeEditorReactRecovered=0;
    const region = globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.replyRoots?.(active);
    if (!VERIFIED_EDITOR_SCOPES.has(region?.scope) ||
        !Array.isArray(region?.roots)) return;
    const seen = new Set(), editors = [];
    for (const root of region.roots) {
      if (!(root instanceof Element) || !root.isConnected) continue;
      const found = [
        ...(root.matches?.(".cm-editor") ? [root] : []),
        ...(root.querySelectorAll?.(".cm-editor") || []),
      ];
      for (const editor of found) {
        if (!editor.isConnected || seen.has(editor) || editor.closest?.(
          "form,nav,aside,footer,[role='dialog'],[role='menu'],[data-message-author-role='user']"
        )) continue;
        seen.add(editor);
        editors.push(editor);
      }
    }
    active.nativeEditorStateCandidateCount = editors.length;
    if (editors.length > 8) {
      active.nativeEditorStateFailure = "code-editor-count-over-budget";
      return;
    }
    if (!globalThis.crypto?.randomUUID) {
      active.nativeEditorStateFailure = "editor-random-probe-unavailable";
      return;
    }
    // Work in bounded batches rather than 8 sequential 2.5-second waits.
    // A rich reply with several CodeMirror cards must not add 20 seconds
    // to a completed request's final publication path.
    for (let start=0; start<editors.length; start+=4) {
      await Promise.allSettled(editors.slice(start,start+4).map(async editor=>{

      const probe = crypto.randomUUID().replace(/-/g, "").toLowerCase();
      if (!/^[a-f0-9]{32}$/.test(probe) ||
          typeof editor.setAttribute !== "function") return;
      editor.setAttribute(EDITOR_PROBE_ATTR,probe);
      let timer;
      try {
        let reply=null;
        // The final ChatGPT code card may hydrate just after the terminal
        // observer first sees .cm-editor. Retry ONLY a missing attached
        // EditorView, at bounded delays, without scrolling, clicks or
        // repairing the model's reply from virtualized DOM lines.
        for (let attempt=0;attempt<3;attempt++) {
          if (!editor.isConnected || editor.getAttribute?.(EDITOR_PROBE_ATTR)!==probe) {
            reply={ok:false,reason:"editor-owner-scope-disconnected"};
            break;
          }
          let readTimer;
          try {
            reply=await Promise.race([
              chrome.runtime.sendMessage({
                type:"chat2api.native.editor.read-v297",
                request_id:active.requestId,probe,
              }),
              new Promise(resolve=>{readTimer=setTimeout(
                ()=>resolve({ok:false,reason:"editor-main-world-timeout"}),2500);
              }),
            ]);
          } finally {
            if (readTimer) clearTimeout(readTimer);
          }
          const bounded=(key,max)=>Number.isInteger(reply?.[key])
            && reply[key]>=0 && reply[key]<=max ? reply[key]:0;
          active.nativeEditorProbeScanned=Math.max(active.nativeEditorProbeScanned,
            bounded("scan_count",4096));
          active.nativeEditorProbeAttachments=Math.max(active.nativeEditorProbeAttachments,
            bounded("attachment_count",4096));
          active.nativeEditorProbeVisibleLines=Math.max(active.nativeEditorProbeVisibleLines,
            bounded("dom_lines",4096));
          active.nativeEditorProbeVirtualized ||= reply?.virtual_gap===true;
          active.nativeEditorReactFiberHosts=Math.max(active.nativeEditorReactFiberHosts,
            bounded("react_fiber_hosts",32));
          active.nativeEditorReactCandidateCount=Math.max(active.nativeEditorReactCandidateCount,
            bounded("react_candidate_count",32));
          active.nativeEditorReactAnchorCount=Math.max(active.nativeEditorReactAnchorCount,
            bounded("react_anchor_count",8));
          active.nativeEditorReactHookNodes=Math.max(active.nativeEditorReactHookNodes,
            bounded("react_hook_nodes",32));
          active.nativeEditorReactArrayNodes=Math.max(active.nativeEditorReactArrayNodes,
            bounded("react_array_nodes",32));
          active.nativeEditorReactDocumentNodes=Math.max(active.nativeEditorReactDocumentNodes,
            bounded("react_document_nodes",32));
          if (reply?.ok || reply?.reason!=="editor-view-not-found" || attempt===2)
            break;
          active.nativeEditorProbeRetries++;
          await new Promise(resolve=>setTimeout(resolve,attempt===0?160:360));
        }
        if (reply?.ok === true &&
            ["codemirror-state-v297","react-owned-code-v302"].includes(reply.source) &&
            typeof reply.text === "string" && reply.text.trim() &&
            reply.length === reply.text.length && reply.text.length <= 150000 &&
            active.requestId && editor.isConnected) {
          active.nativeEditorDocs.set(editor,{
            source:reply.source,text:reply.text,
          });
          active.nativeEditorStateResolvedCount++;
          if (reply.source==="react-owned-code-v302")
            active.nativeEditorReactRecovered++;
        } else {
          active.nativeEditorStateFailure = String(
            reply?.reason || "editor-full-state-unavailable"
          ).slice(0,60);
        }
      } catch (_) {
        active.nativeEditorStateFailure = "editor-state-transport-error";
      } finally {
        if (timer) clearTimeout(timer);
        if (editor.getAttribute?.(EDITOR_PROBE_ATTR) === probe)
          editor.removeAttribute?.(EDITOR_PROBE_ATTR);
      }
      }));
    }
  }

  async function capture(active, final = false) {
    if (!active?.requestId || !active.submitStartedAt) return;
    const now = Date.now();
    // A final capture can supersede an unacknowledged progress revision.
    // Only its own transfer may publish capture state or send more chunks.
    if (active.nativeTransferInFlight?.final) {
      if (final) await active.nativeTransferInFlight.done;
      return;
    }
    if (!final && (active.nativeFinalCaptureStarted || active.nativeTransferInFlight
        || now - Number(active.nativeLastAt || 0) < (active.nativeHeavySnapshot ? 6500 : 900))) return;
    const transfer = {final};
    active.nativeTransferInFlight = transfer;
    active.nativeLastAt = now;
    transfer.done = captureRevision(active, transfer);
    await transfer.done;
  }

  async function captureRevision(active, transfer) {
    const {final} = transfer;
    active.nativeFinalCaptureCommitted = false;
    if (final) {
      active.nativeFinalCaptureStarted = true;
      active.nativeChunkComplete = false;
      active.nativeTransportAckError = "";
      // Never reuse a prepared proof from an earlier incremental revision
      // or a different request / previous assistant selection.
      active.nativePreparedEditorMerge = null;
      active.nativePreparedEditorReason = "";
    }
    try {
      if (final) {
        await resolveImageIdentities(active);
        await resolveVirtualEditorDocs(active);
        // The read-only full CodeMirror document and its original DOM card
        // have already been verified together NOW. Stage only their bounded
        // exact Markdown positioning evidence while the DOM is still live:
        // sending the native snapshot/ACK below is asynchronous, and page
        // hydration can detach the card before request-v6's terminal merge.
        const docs=active.nativeEditorDocs;
        if (active.nativeMessageIdentityBound === true
            && docs instanceof Map && docs.size>0
            && docs.size===active.nativeEditorStateCandidateCount) {
          const region=globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__
            ?.replyRoots?.(active);
          const root=region?.scope==="verified_native_assistant_selection"
            && region.roots?.length===1 ? region.roots[0] : null;
          const rich=globalThis.__CHAT2API_RICH_RESPONSE_V69__;
          const messageId=String(root?.getAttribute?.(
            "data-chatgpt-selection-message-id")||"");
          if (root?.isConnected && root===active.nativeVerifiedReplyScope
              && /^[A-Za-z0-9-]{12,100}$/.test(messageId)
              && [...docs.keys()].every(editor=>editor?.isConnected
                && root.contains?.(editor))
              && typeof rich?.prepareVerifiedEditorMarkdown==="function") {
            const prepared=rich.prepareVerifiedEditorMarkdown(root,docs);
            if (prepared?.ok && prepared.integrated===docs.size) {
              active.nativePreparedEditorMerge={
                requestId:active.requestId,
                messageId,
                messageScope:root,
                codeTexts:prepared.codeTexts,
                enriched:prepared.enriched,
                integrated:prepared.integrated,
              };
              active.nativePreparedEditorReason="prepared-verified-message-id";
            } else {
              active.nativePreparedEditorReason=String(
                prepared?.reason||"editor-preparation-unavailable").slice(0,60);
            }
          } else {
            active.nativePreparedEditorReason="prepared-reply-owner-unavailable";
          }
        }
      }
      let tree = snapshot(active);
      // Transiently cleared WebGL buffers may be readable on a later frame.
      // Do not alter the upstream context or accept a blank raster as proof.
      if (final && active.nativeCanvasOmitted && typeof requestAnimationFrame === "function") {
        for (let attempt=0;attempt<2 && active.nativeCanvasOmitted;attempt++) {
          if (active.nativeTransferInFlight !== transfer) return;
          await new Promise(resolve=>{
            let done=false;
            const finish=()=>{if(!done){done=true;resolve();}};
            requestAnimationFrame(finish);
            setTimeout(finish,100);
          });
          tree = snapshot(active);
        }
      }
      if (!tree) {
        active.nativeCaptureFailure = "structural-" +
          String(active.nativeStructuralFailure || "unavailable").slice(0,60);
        return;
      }
      const hash = JSON.stringify(tree.root);
      if (!final && hash === active.nativeLastHash) return;
      const json = JSON.stringify(tree);
      const bytes = new TextEncoder().encode(json);
      if (bytes.byteLength > MAX_RAW_BYTES) {
        active.nativeCaptureFailure = "native-chunk-total-byte-cap";
        return;
      }
      active.nativeHeavySnapshot = bytes.byteLength > 640000;
      active.nativeChunkComplete = false;
      active.nativeTransportAckError = "";
      // Reserve the wire revision before the first asynchronous send. A lost
      // ACK must not let a later, different tree reuse that revision.
      active.nativeSentRevision = tree.revision;
      const chunked = bytes.byteLength > 900000;
      const reply = chunked
        ? await deliverChunks(active, tree, bytes, transfer)
        : await chrome.runtime.sendMessage({type:"chat2api.native.ack",event:{
          type:"chat.native.snapshot",request_id:active.requestId,snapshot:tree,
        }});
      if (active.nativeTransferInFlight !== transfer) return;
      if (reply?.ok === true && reply?.committed === true) {
        active.nativeChunkComplete = true;
        active.nativeFinalCaptureCommitted = final;
        active.nativeLastHash = hash;
        active.nativeRevision = tree.revision;
        active.nativeCaptureFailure = "";
      } else {
        const fallback = chunked ? "native-chunk-not-committed" : "native-snapshot-not-committed";
        active.nativeTransportAckError = String(reply?.error || fallback).slice(0,80);
        active.nativeCaptureFailure = "native-transport-incomplete";
      }
    } catch (_) {
      if (active.nativeTransferInFlight === transfer)
        active.nativeCaptureFailure = "native-transport-failed";
    } finally {
      if (active.nativeTransferInFlight === transfer) active.nativeTransferInFlight = null;
    }
  }

  function applyNativeInteraction(message) {
    const requestId = String(message?.request_id || "");
    const revision = Number(message?.revision);
    const interactionId = String(message?.interaction_id || "");
    if (!/^req_[A-Za-z0-9_-]{8,120}$/.test(requestId)
        || !Number.isInteger(revision) || revision < 1
        || !/^nc_[1-9][0-9]{0,6}_[1-9][0-9]{0,3}$/.test(interactionId)
        || !/^[a-f0-9]{32}$/.test(String(message?.action_id || ""))) {
      return {ok:false,error:"invalid-interaction-envelope"};
    }
    const actionId = String(message?.action_id || "");
    const action = String(message?.action || "");
    const value = message?.value;
    const previousAction = nativeControlAppliedActions.get(actionId);
    if (previousAction) {
      if (previousAction.requestId === requestId
          && previousAction.revision === revision
          && previousAction.interactionId === interactionId
          && previousAction.action === action
          && Object.is(previousAction.value,value)) {
        return {...previousAction.response,duplicate:true};
      }
      return {ok:false,error:"native-interaction-action-id-reused"};
    }
    const revisions = nativeControlTargetsByRequest.get(requestId);
    if (nativeControlLatestRevision.get(requestId) !== revision) {
      return {ok:false,error:"native-interaction-revision-already-consumed"};
    }
    const targets = revisions?.get(revision);
    const target = targets?.get(interactionId);
    const element = target?.element;
    if (!element || !element.isConnected) return {ok:false,error:"native-control-no-longer-connected"};
    if (element.disabled || element.matches?.(":disabled") || element.getAttribute?.("aria-disabled")==="true")
      return {ok:false,error:"native-control-disabled"};
    // The original node may have been repurposed by the live page since
    // capture. A stale checkbox must never click a newly assigned submit
    // button, or mutate a control that moved into an account/form surface.
    const tag=element.tagName?.toLowerCase();
    const liveType=String(element.type || element.getAttribute?.("type") || "").toLowerCase();
    if (element.form || element.closest?.("form,nav,aside,header,footer,[role='menu'],[role='dialog'],[data-message-author-role='user'],[contenteditable='true']"))
      return {ok:false,error:"native-control-no-longer-in-safe-scope"};
    if ((["checkbox","radio","range"].includes(target.kind) && (tag!=="input" || liveType!==target.kind))
        || (target.kind==="select" && (tag!=="select" || element.multiple))
        || (target.kind==="disclosure" && tag!=="details"))
      return {ok:false,error:"native-control-kind-changed"};
    try {
      if (target.kind === "disclosure" && action === "set_open" && typeof value === "boolean") {
        element.open = value;
      } else if (target.kind === "checkbox" && action === "set_checked" && typeof value === "boolean") {
        if (Boolean(element.checked) !== value) element.click();
      } else if (target.kind === "radio" && action === "set_checked" && value === true) {
        if (!element.checked) element.click();
      } else if (target.kind === "range" && action === "set_value"
          && typeof value === "number" && Number.isFinite(value)) {
        const min = Number(element.min || 0), max = Number(element.max || 100);
        const step = Number(element.step || 1);
        const units = (value - min) / step;
        if (!Number.isFinite(min) || !Number.isFinite(max) || !Number.isFinite(step)
            || min >= max || step <= 0 || value < min || value > max
            || Math.abs(units - Math.round(units)) > 1e-6) {
          return {ok:false,error:"native-range-value-out-of-bounds"};
        }
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
        if (setter) setter.call(element, String(value));
        else element.value = String(value);
        element.dispatchEvent(new Event("input",{bubbles:true,composed:true}));
        element.dispatchEvent(new Event("change",{bubbles:true,composed:true}));
      } else if (target.kind === "select" && action === "set_value" && typeof value === "string") {
        const option = [...(element.options || [])].find(item =>
          String(item.value) === value && !item.disabled && !item.closest?.("optgroup[disabled]"));
        if (!option) return {ok:false,error:"native-select-option-not-allowed"};
        const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
        if (setter) setter.call(element, value);
        else element.value = value;
        element.dispatchEvent(new Event("input",{bubbles:true,composed:true}));
        element.dispatchEvent(new Event("change",{bubbles:true,composed:true}));
      } else if (target.kind === "tab" && action === "set_selected" && value === true) {
        if (element.getAttribute?.("role") !== "tab"
            || (element.tagName?.toLowerCase() === "button" && element.form)
            || !element.closest?.("[role='tablist']")
            || element.getAttribute?.("aria-disabled") === "true"
            || element.getAttribute?.("aria-selected") === null) {
          return {ok:false,error:"native-tab-no-longer-valid"};
        }
        if (element.getAttribute("aria-selected") !== "true") element.click();
      } else if (target.kind === "toggle" && action === "set_pressed" && typeof value === "boolean") {
        const current=element.getAttribute?.("aria-pressed");
        if (element.tagName?.toLowerCase() !== "button"
            || element.form
            || !["true","false"].includes(current)
            || element.getAttribute?.("aria-disabled") === "true") {
          return {ok:false,error:"native-toggle-no-longer-valid"};
        }
        if ((current==="true") !== value) element.click();
      } else {
        return {ok:false,error:"native-control-action-not-allowed"};
      }
      // The server publishes revision+1 after this exact action is ACKed.
      // Keep the same live DOM target map available for the next serialized action.
      revisions.set(revision + 1, targets);
      nativeControlLatestRevision.set(requestId, revision + 1);
      while (revisions.size > 8) revisions.delete(revisions.keys().next().value);
      const response = {ok:true,revision:revision + 1};
      nativeControlAppliedActions.set(actionId,{
        requestId,revision,interactionId,action,value,response,
      });
      while (nativeControlAppliedActions.size > 64) {
        nativeControlAppliedActions.delete(nativeControlAppliedActions.keys().next().value);
      }
      return response;
    } catch (_) {
      return {ok:false,error:"native-control-action-failed"};
    }
  }

  // Read only the actual visible raster originals in the same verified
  // reply. New bytes are bounded independently of canvas pixel previews.
  async function interactionImageEvidence(active,context) {
    const eligible=node=>{
      if (!(node instanceof Element) || node.tagName?.toLowerCase()!=="img" ||
          node.closest?.("form,nav,aside,footer,header,[role='dialog'],[role='menu'],[data-message-author-role='user']")) return false;
      const box=node.getBoundingClientRect?.(),style=getComputedStyle(node);
      return box && box.width>=40 && box.height>=40 && style.display!=="none" && style.visibility!=="hidden";
    };
    const images=[];
    for(const root of context.roots){
      for(const image of composedFind(root,eligible,5)){
        if(!images.includes(image))images.push(image);
        if(images.length>4)return null;
      }
    }
    const digests=new Map(),media=[];
    if(!images.length){active.nativeImageDigests=digests;return media;}
    const prior=new Set(),stack=[context.root];
    while(stack.length){
      const node=stack.pop();
      if(node?.tag==="img" && HASH_HEX.test(node.source_sha256||""))prior.add(node.source_sha256);
      if(Array.isArray(node?.children))stack.push(...node.children);
    }
    const controller=typeof AbortController==="function"?new AbortController():null;
    let timer, accepting=true,total=0;
    const work=Promise.all(images.map(async image=>{
      try{
        const source=String(image.currentSrc || image.getAttribute?.("src") || "");
        if(!source)return false;
        let result;
        if(/^https:\/\//i.test(source)){
          result=await chrome.runtime.sendMessage({type:"chat2api.media.interaction-v1",url:source});
        }else if(/^(blob:|data:image\/)/i.test(source) && globalThis.crypto?.subtle){
          if(/^data:image\//i.test(source) && source.length>4*Math.ceil(MAX_IDENTITY_BYTES/3)+128)return false;
          const response=await fetch(source,{credentials:"omit",redirect:"error",...(controller?{signal:controller.signal}:{})});
          if(!response.ok || Number(response.headers.get("content-length")||0)>MAX_IDENTITY_BYTES)return false;
          const blob=await response.blob();
          const mime=blob.type.toLowerCase();
          if(!IDENTITY_MIMES.has(mime) || !blob.size || blob.size>MAX_IDENTITY_BYTES)return false;
          const bytes=new Uint8Array(await blob.arrayBuffer());
          const hash=new Uint8Array(await crypto.subtle.digest("SHA-256",bytes));
          result={ok:true,sha256:[...hash].map(x=>x.toString(16).padStart(2,"0")).join(""),bytes:bytes.length};
          if(bytes.length<=256*1024 && globalThis.btoa){
            const parts=[];
            for(let i=0;i<bytes.length;i+=16384)parts.push(String.fromCharCode(...bytes.subarray(i,i+16384)));
            result.mime=mime;result.base64=btoa(parts.join(""));
          }
        }
        if(!accepting || !result?.ok || !HASH_HEX.test(result.sha256) || !image.isConnected ||
            source!==String(image.currentSrc || image.getAttribute?.("src") || ""))return false;
        digests.set(image,result.sha256);
        if(!prior.has(result.sha256) && typeof result.base64==="string" && IDENTITY_MIMES.has(result.mime) &&
            Number.isInteger(result.bytes) && result.bytes<=256*1024 &&
            total+result.bytes<=384*1024 && !media.some(m=>m.sha256===result.sha256)){
          total+=result.bytes;
          media.push({sha256:result.sha256,mime:result.mime,base64:result.base64});
        }
        return true;
      }catch(_){return false;}
    }));
    try{
      const results=await Promise.race([work,new Promise(resolve=>{timer=setTimeout(()=>resolve(null),1800);})]);
      accepting=false;
      if(!results || results.some(ok=>!ok) || digests.size!==images.length)return null;
      active.nativeImageDigests=digests;
      return media;
    }finally{
      accepting=false;
      if(timer!==undefined)clearTimeout(timer);
      controller?.abort();
    }
  }

  async function recaptureAfterInteraction(message, response, context) {
    const requestId = String(message.request_id);
    const revision = Number(message.revision);
    // Allow the upstream UI to settle, then re-prove the exact original
    // assistant boundary. Never use a newly selected/latest reply.
    await new Promise(resolve=>setTimeout(resolve,180));
    if (nativeControlLatestRevision.get(requestId)!==revision+1 ||
        nativeControlCaptureContexts.get(requestId)!==context) return;
    const region = globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.replyRoots?.(context.active);
    if (!Array.isArray(region?.roots) || region.roots.length!==context.roots.length ||
        region.roots.some((root,index)=>root!==context.roots[index] || !root.isConnected)) return;
    const revisions = nativeControlTargetsByRequest.get(requestId);
    const fallbackTargets = revisions?.get(revision+1);
    const replay = {...context.active,nativeRevision:revision,nativeSentRevision:revision};
    let tree,media=null;
    try {
      media=await interactionImageEvidence(replay,context);
      const latest=globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.replyRoots?.(context.active);
      if(media!==null && Array.isArray(latest?.roots) && latest.roots.length===context.roots.length &&
          latest.roots.every((root,index)=>root===context.roots[index] && root.isConnected) &&
          nativeControlLatestRevision.get(requestId)===revision+1 &&
          nativeControlCaptureContexts.get(requestId)===context) tree=snapshot(replay);
    } catch (_) {}
    if (!tree || tree.revision!==revision+1 || media===null ||
        replay.nativeGraphicsUnsupported ||
        new TextEncoder().encode(JSON.stringify({snapshot:tree,media})).length>768*1024) {
      if (fallbackTargets) revisions?.set(revision+1,fallbackTargets);
      nativeControlLatestRevision.set(requestId,revision+1);
      nativeControlCaptureContexts.set(requestId,{...context,revision:revision+1});
      return;
    }
    response.snapshot=tree;
    if(media.length)response.media=media;
    response.recapture_status="verified";
  }

  async function refreshCanvasSnapshot(message) {
    const requestId=String(message.request_id || ""), revision=Number(message.revision);
    const actionId=String(message.action_id || "");
    if (!/^req_[A-Za-z0-9_-]{8,120}$/.test(requestId) ||
        !Number.isInteger(revision) || revision<1 || revision>=1000000 ||
        !/^[a-f0-9]{32}$/.test(actionId) ||
        message.interaction_id!=="native_refresh" || message.value!==null)
      return {ok:false,error:"invalid-native-refresh"};
    const previous=nativeControlAppliedActions.get(actionId);
    if (previous) return previous.requestId===requestId && previous.revision===revision &&
      previous.action==="refresh_canvas" ? {...previous.response,duplicate:true} :
      {ok:false,error:"native-interaction-action-id-reused"};
    const context=nativeControlCaptureContexts.get(requestId);
    if (!context?.canvasRasters || context.revision!==revision ||
        nativeControlLatestRevision.get(requestId)!==revision)
      return {ok:false,error:"canvas-refresh-context-unavailable"};
    const sameRoots=()=>{
      const region=globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.replyRoots?.(context.active);
      return Array.isArray(region?.roots) && region.roots.length===context.roots.length &&
        region.roots.every((root,index)=>root===context.roots[index] && root.isConnected);
    };
    if (!sameRoots()) return {ok:false,error:"native-interaction-reply-scope-changed"};
    const oldTargets=nativeControlTargetsByRequest.get(requestId);
    const before=new Map(oldTargets || []);
    const replay={...context.active,nativeRevision:revision,nativeSentRevision:revision};
    const restore=()=>{
      nativeControlTargetsByRequest.set(requestId,before);
      nativeControlLatestRevision.set(requestId,revision);
      nativeControlCaptureContexts.set(requestId,context);
    };
    let tree=null,media=null;
    for(let attempt=0;attempt<3;attempt++){
      if (!sameRoots()) return {ok:false,error:"native-interaction-reply-scope-changed"};
      try {
        media=await interactionImageEvidence(replay,context);
        if(!sameRoots() || nativeControlLatestRevision.get(requestId)!==revision ||
            nativeControlCaptureContexts.get(requestId)!==context)
          return {ok:false,error:"native-refresh-superseded"};
        tree=media!==null?snapshot(replay):null;
      } catch (_) {}
      if (tree && !replay.nativeGraphicsUnsupported) break;
      restore();
      await new Promise(resolve=>setTimeout(resolve,40));
      if (nativeControlLatestRevision.get(requestId)!==revision ||
          nativeControlCaptureContexts.get(requestId)!==context)
        return {ok:false,error:"native-refresh-superseded"};
    }
    const next=nativeControlCaptureContexts.get(requestId);
    if (!tree || tree.revision!==revision+1 || replay.nativeGraphicsUnsupported ||
        !next || next.canvasRasters!==context.canvasRasters || media===null ||
        new TextEncoder().encode(JSON.stringify({snapshot:tree,media})).length>768*1024) {
      restore();
      return {ok:false,error:"canvas-refresh-pixels-unavailable"};
    }
    // Interaction IDs are revision-local, so ignore only their revision
    // prefix when deciding whether an unchanged frame needs a new commit.
    const stable=root=>JSON.stringify(root,(key,value)=>
      key==="interaction_id" && typeof value==="string" ? value.replace(/^nc_\d+_/,"nc_") : value);
    let response;
    if(stable(tree.root)===stable(context.root)){
      restore();
      response={ok:true,revision,recapture_status:"unchanged"};
    }else response={ok:true,revision:revision+1,recapture_status:"verified",snapshot:tree,
      ...(media.length?{media}:{})};
    nativeControlAppliedActions.set(actionId,{requestId,revision,action:"refresh_canvas",response});
    while(nativeControlAppliedActions.size>64)
      nativeControlAppliedActions.delete(nativeControlAppliedActions.keys().next().value);
    return response;
  }

  chrome.runtime.onMessage?.addListener?.((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.native.interaction") return false;
    if (message.action==="refresh_canvas") {
      void refreshCanvasSnapshot(message).then(sendResponse,()=>sendResponse({ok:false,error:"canvas-refresh-failed"}));
      return true;
    }
    const requestId=String(message.request_id || "");
    const context=nativeControlCaptureContexts.get(requestId);
    if (context?.revision===Number(message.revision)) {
      const current=globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.replyRoots?.(context.active);
      const target=nativeControlTargetsByRequest.get(requestId)?.get(context.revision)
        ?.get(String(message.interaction_id || ""))?.element;
      if (!Array.isArray(current?.roots) || current.roots.length!==context.roots.length ||
          current.roots.some((root,index)=>root!==context.roots[index] || !root.isConnected) ||
          !target || !context.roots.some(root=>root===target || root.contains?.(target))) {
        sendResponse({ok:false,error:"native-interaction-reply-scope-changed"});
        return false;
      }
    }
    const response=applyNativeInteraction(message);
    if (response.ok && !response.duplicate && context?.revision===Number(message.revision)) {
      response.recapture_status="unavailable";
      void recaptureAfterInteraction(message,response,context)
        .catch(()=>{}).finally(()=>sendResponse(response));
      return true;
    }
    sendResponse(response);
    return false;
  });

  globalThis[KEY] = Object.freeze({capture,snapshot});
})();
