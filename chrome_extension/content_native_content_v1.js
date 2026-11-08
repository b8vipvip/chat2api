/* Non-Voice native rich reply observer. No script/HTML/CSS or source URLs leave the tab.
 * Only the same request-v6 assistant candidate is eligible. This observer may
 * fail without changing chat.completed, delta, cancellation or window lifetime.
 */
(() => {
  "use strict";
  const KEY = "__CHAT2API_NATIVE_CONTENT_V1__";
  if (globalThis[KEY]) return;
  const tags = new Set("div p span strong em h1 h2 h3 h4 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br".split(" "));
  // Do not serialize MathML as HTML div/span descendants; its parent structure
  // and unit labels require the MathML namespace through the server and UI.
  const mathTags = new Set("math semantics mrow mi mo mn msub mfrac mtext mover munder munderover msup msubsup msqrt mroot".split(" "));
  const mathTokens = new Set("mi mo mn mtext".split(" "));
  const mathNamespace = "http://www.w3.org/1998/Math/MathML";
  // SVG is vector data, not raw HTML. Only inert geometry/axis labels and
  // internal clip references can leave the trusted assistant reply scope.
  const svgNS="http://www.w3.org/2000/svg";
  // Chrome 156+ emits these CSS Color 4 forms on ChatGPT cards/charts.
  // Accept ONLY bounded numeric oklab/oklch and color(srgb), never CSS
  // variables, relative colors, nested functions or external url() values.
  const modernColor=/^(?:(?:oklab|oklch)\(\s*[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+(?:\s*\/\s*\d*\.?\d+)?\s*\)|color\(srgb\s+\d*\.?\d+\s+\d*\.?\d+\s+\d*\.?\d+(?:\s*\/\s*\d*\.?\d+)?\s*\))$/i;

  const svgTags=new Set("svg g path line rect circle ellipse polyline polygon text tspan defs clippath".split(" "));
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
  const banned = "button,script,style,form,nav,header,footer,aside,input,textarea,select,iframe,canvas,[role='menu'],[contenteditable='true']";
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
    let nodes = 0, chars = 0, images = 0;
    let truncated = false;
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
      if (tag === "button") {
        if (element.closest?.("nav,aside,footer,header,form,[role='menu'],[role='dialog']"))
          return null;
        const graphical = composedFind(element,node=>{
          const type=node.tagName?.toLowerCase();
          const raster=type==="img" && !node.closest?.("svg");
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
        // Canvas pixels are still unsupported. Count canvases nested in a
        // button as missing media, rather than silently reporting no gaps.
        const canvases=composedFind(element,node=>
          node.tagName?.toLowerCase()==="canvas",41);
        active.nativeCanvasOmitted+=canvases.length;
        active.nativeGraphicsUnsupported+=canvases.length;
        if (canvases.length>40) {
          active.nativeStructuralFailure="button-canvas-cap";
          truncated=true;
          return null;
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
        if(tag==="canvas")active.nativeCanvasOmitted++;
        active.nativeGraphicsUnsupported++;
        return null;
      }
      if (ignore(element)) return null;
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
    const replyRegion = globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.replyRoots?.(active);
    // An explicitly unverified or overflowed reply boundary is never
    // promoted to a complete native answer merely because the paragraph
    // itself can be serialized. The canonical response remains authoritative.
    if (replyRegion?.scope === "candidate_only") {
      active.nativeStructuralFailure = "reply-scope-unverified";
      return null;
    }
    const scopedRoots = (replyRegion?.scope === "roleless_reply_siblings"
      || (replyRegion?.scope === "verified_reply_container" || replyRegion?.scope === "verified_native_assistant_selection"))
      ? replyRegion.roots : null;
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
    return {version:1,revision:(active.nativeRevision || 0) + 1,root};
  }

  const MAX_RAW_BYTES = 6 * 1024 * 1024;
  const CHUNK_BYTES = 64 * 1024;
  // Every chunk must receive the server's own ACK, not just a local socket
  // enqueue. The final ACK must also confirm a committed, validated tree.
  async function deliverChunks(active, tree, bytes, final) {
    if (!globalThis.crypto?.subtle || !globalThis.btoa) return false;
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    const sha256 = [...digest].map(x=>x.toString(16).padStart(2,"0")).join("");
    const count = Math.ceil(bytes.length / CHUNK_BYTES);
    if (count < 1 || count > 96) return false;
    for (let index = 0; index < count; index++) {
      const slice = bytes.subarray(index * CHUNK_BYTES,(index + 1) * CHUNK_BYTES);
      const data = btoa(String.fromCharCode(...slice));
      const reply = await chrome.runtime.sendMessage({type:"chat2api.native.ack",event:{
        type:"chat.native.chunk",request_id:active.requestId,revision:tree.revision,
        index,count,byte_length:bytes.length,sha256,data,
      }});
      if (reply?.ok !== true || (index === count - 1 && reply?.committed !== true)) {
        active.nativeTransportAckError = String(reply?.error || "native-chunk-not-committed").slice(0,80);
        return false;
      }
      // Do not let the page's old reply be replaced by a partial generation.
      if (active.requestId !== tree.requestId && tree.requestId) return false;
    }
    active.nativeChunkComplete = true; // Last chunk was validated and published by server.
    return true;
  }


  const IDENTITY_MIMES = new Set(["image/jpeg","image/png","image/webp","image/gif"]);
  // The media archive accepts originals up to 20 MiB. A 4 MiB hash cap
  // silently left otherwise-archived originals without byte identity proof.
  const MAX_IDENTITY_BYTES = 20 * 1024 * 1024;
  const HASH_HEX = /^[a-f0-9]{64}$/;
  async function hashVisibleRaster(img) {
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
      const response=await fetch(url,{credentials:"omit",redirect:"error"});
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
    for (const root of roots) {
      if (!(root instanceof Element) || !root.isConnected) continue;
      const candidates=composedFind(root,
        node=>node.tagName?.toLowerCase()==="img",40);
      for (const img of candidates) {
        if (!(img instanceof Element) || images.includes(img)) continue;
        const box=img.getBoundingClientRect?.();
        if (!box || box.width<40 || box.height<40) continue;
        if (img.closest?.("aside,nav,footer,[data-message-author-role='user']")) continue;
        images.push(img);
        if (images.length===40) break;
      }
      if (images.length===40) break;
    }
    const digests=new Map();
    // The request-v6 controller awaits final native.capture before emitting
    // the terminal. Keep the existing 3.5-second aggregate budget/four-wide
    // concurrency, but do NOT discard a fast image's hash because a sibling
    // fetch stalls. Promise.all + batch timeout previously lost *every* proof
    // from that batch on one slow CDN URL. Late results must not mutate the
    // published snapshot after the deadline.
    const deadline=Date.now()+3500;
    for (let start=0;start<images.length && Date.now()<deadline;start+=4) {
      const batch=images.slice(start,start+4);
      let accepting=true, timer;
      try {
        const jobs=batch.map(async img=>{
          try {
            const digest=await hashVisibleRaster(img);
            if (accepting && Date.now()<=deadline && HASH_HEX.test(digest))
              digests.set(img,digest);
          } catch (_) { /* An unavailable original must stay unverified. */ }
        });
        const remaining=Math.max(1,deadline-Date.now());
        await Promise.race([
          Promise.allSettled(jobs),
          new Promise(resolve=>{timer=setTimeout(resolve,remaining);}),
        ]);
      } finally {
        accepting=false;
        if(timer)clearTimeout(timer);
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
    if (final && active.nativeTransferInFlight) {
      // A prior live incremental transfer must not interleave chunks with
      // the terminal snapshot under the same revision. Keep a hard bound.
      const deadline=Date.now()+30000;
      while (active.nativeTransferInFlight && Date.now()<deadline)
        await new Promise(resolve=>setTimeout(resolve,100));
    }
    if (active.nativeTransferInFlight) {
      active.nativeCaptureFailure="earlier-chunk-transfer-not-finished";
      return;
    }
    if (!final && now - Number(active.nativeLastAt || 0) <
          (active.nativeHeavySnapshot ? 6500 : 900)) return;
    active.nativeTransferInFlight = true;
    active.nativeLastAt = now;
    // An earlier incremental revision is not proof that the final DOM tree
    // was committed. Avoid exporting stale true when final capture fails.
    if (final) {
      active.nativeChunkComplete = false;
      active.nativeFinalCaptureCommitted = false;
    }
    try {
      if (final) {
        await resolveImageIdentities(active);
        await resolveVirtualEditorDocs(active);
      }
      const tree = snapshot(active);
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
      const transmit = async () => {
        if (bytes.byteLength <= 900000) {
          const reply = await chrome.runtime.sendMessage({type:"chat2api.native.ack",event:{
            type:"chat.native.snapshot",request_id:active.requestId,snapshot:tree,
          }});
          if (reply?.ok === true && reply?.committed === true) {
            active.nativeChunkComplete = true;
            return true;
          }
          active.nativeTransportAckError = String(reply?.error || "native-snapshot-not-committed").slice(0,80);
          return false;
        }
        return deliverChunks(active,tree,bytes,final);
      };
      const result = await transmit();
      if (result) {
        if (final) active.nativeFinalCaptureCommitted = true;
        active.nativeLastHash = hash;
        active.nativeRevision = tree.revision;
        active.nativeCaptureFailure = "";
        active.nativeTransportAckError = "";
      } else {
        active.nativeCaptureFailure = "native-transport-incomplete";
      }
    } catch (_) {
      active.nativeCaptureFailure = "native-transport-failed";
    } finally {
      active.nativeTransferInFlight = false;
    }
  }
  globalThis[KEY] = Object.freeze({capture,snapshot});
})();
