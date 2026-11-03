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
    boxSizing: ["border-box","content-box"],
    borderCollapse: ["collapse","separate"],
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
    alignSelf: ["auto","start","center","end","stretch","baseline"],
    justifySelf: ["auto","start","center","end","stretch"],
  };
  const allowedLength = new Set(["gap","rowGap","columnGap","padding","marginTop","marginBottom",
    "marginLeft","marginRight","width","minWidth","maxWidth","height","minHeight",
    "maxHeight","borderRadius","borderWidth","letterSpacing","fontSize","lineHeight"]);
  const length = /^(?:0|\d{1,4}(?:\.\d{1,3})?px|\d{1,4}(?:\.\d{1,2})?(?:rem|%))$/;
  const lengthList = (value, count) => value.length <= 80 &&
    value.split(/\s+/).length <= count && value.split(/\s+/).every(part => length.test(part));
  // ChatGPT's wide tables use negative horizontal margins to cancel a
  // matching 395px inner padding. Preserve bounded pixel offsets only.
  const safeNegativeMargin = value => /^-(?:[1-9]\d{0,3})(?:\.\d{1,2})?px$/.test(value) &&
    Math.abs(Number.parseFloat(value)) <= 1024;
  // Computed row borders are often "0px 0px 1px". Discarding this
  // shorthand makes the browser invent a 3px medium border for solid rows.
  const safeBorderWidths = value => value.length <= 64 &&
    value.split(/\s+/).length <= 4 && value.split(/\s+/).every(part =>
      /^(?:0|\d{1,2}(?:\.\d{1,2})?px)$/.test(part) &&
      Number.parseFloat(part) <= 16);
  // Computed CSS Grid tracks use fractional pixel widths (250.656px).
  // Accept at most six bounded, nonnegative pixel tracks, never raw CSS.
  const gridTrack = /^(?:\d{1,4}(?:\.\d{1,3})?px)(?:\s+\d{1,4}(?:\.\d{1,3})?px){0,5}$/;\n  const color = /^(?:#[0-9a-f]{3,8}|rgba?\([0-9.,\s]+\))$/i;
  const grid = /^(?:\d{1,4}(?:\.\d{1,2})?(?:px|%)|minmax\(\d{1,4}(?:\.\d{1,2})?px,\s*1fr\)|1fr)(?:\s+(?:\d{1,4}(?:\.\d{1,2})?(?:px|%)|minmax\(\d{1,4}(?:\.\d{1,2})?px,\s*1fr\)|1fr)){0,5}$/;
  // Server validates at most 32 style keys per element. Chromium may emit
  // more, so prioritize actual visual styling before optional dimensions.
  const cssList = [...new Set([
    // Preserve table geometry even if the 32-key style budget is full.
    "display","boxSizing","borderCollapse","borderSpacing","borderWidth",
    "marginLeft","marginRight","padding","width","height","minWidth","maxWidth",
    "gridTemplateColumns","gridTemplateRows","flexDirection","flexWrap","justifyContent",
    "alignItems","objectFit","textAlign","color","backgroundColor",
    "borderColor","fontWeight","fontSize","lineHeight","whiteSpace",
    "wordBreak","overflowWrap","gap","rowGap","columnGap",
    "marginTop","marginBottom","minHeight","maxHeight",
    "borderRadius",
    ...Object.keys(styleKeys),...allowedLength,
  ])];
  function safeStyle(element) {
    const css = getComputedStyle(element), obj = {};
    for (const key of cssList) {
      if (Object.keys(obj).length >= 32) break;
      const value = String(css[key] || "").trim();
      if (styleKeys[key]?.includes(value) ||
          allowedLength.has(key) && value.length <= 16 && length.test(value) ||
          key === "padding" && lengthList(value,4) ||
          key === "borderSpacing" && lengthList(value,2) ||
          (key === "marginLeft" || key === "marginRight") && safeNegativeMargin(value) ||
          key === "borderWidth" && safeBorderWidths(value) ||
          ["color","backgroundColor","borderColor"].includes(key) && value.length <= 48 && color.test(value) ||
          key === "gridTemplateColumns" && value.length <= 120 && (grid.test(value) || gridTrack.test(value)) ||
          key === "gridTemplateRows" && value.length <= 120 && gridTrack.test(value)) {
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
  function snapshot(active) {
    if (active) { active.nativeStructuralFailure = ""; active.nativeStructuralMaxChildren = 0; }
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
    const build = (element, depth) => {
      if (depth > MAX_NATIVE_DEPTH || nodes >= 30000) {
        active.nativeStructuralFailure = depth > MAX_NATIVE_DEPTH ? "max-depth" : "max-nodes";
        truncated = true;
        return null;
      }
      if (element.nodeType === Node.TEXT_NODE) {
        const raw = String(element.nodeValue || "");
        if (!raw.trim()) return null;
        if (chars + raw.length > textBudget) {
          active.nativeStructuralFailure = "max-text";
          truncated = true;
          return null;
        }
        chars += raw.length; nodes += 1;
        return {tag:"span",text:raw};
      }
      if (!(element instanceof Element)) return null;
      const tag = element.tagName.toLowerCase();
      // Generated-image cards are often rendered INSIDE inert-looking
      // <button> wrappers. Discard their click action and control text, but
      // preserve only the visible raster descendants inside this request's
      // already-verified assistant reply scope.
      if (tag === "button") {
        if (element.closest?.("nav,aside,footer,header,form,[role='menu'],[role='dialog']"))
          return null;
        const rasters = [...(element.querySelectorAll?.("img") || [])]
          .filter(img => {
            const box=img.getBoundingClientRect?.();
            return box && box.width>=40 && box.height>=40
              && !img.closest?.("nav,aside,footer,[data-message-author-role='user']");
          });
        if (!rasters.length) return null;
        const children=[];
        for(const img of rasters.slice(0,40)){
          const mapped=build(img,depth+1);
          if(mapped)children.push(mapped);
          if(truncated)break;
        }
        if(!children.length || truncated)return null;
        nodes+=1;
        return {tag:"figure",children};
      }
      if (ignore(element)) return null;
      const rect = element.getBoundingClientRect?.();
      const computed = getComputedStyle(element);
      if (computed.display === "none" || computed.visibility === "hidden") return null;
      let mapped = tags.has(tag) ? tag : "div";
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
      const children = [];
      active.nativeStructuralMaxChildren = Math.max(
        Number(active.nativeStructuralMaxChildren || 0), element.childNodes.length);
      for (const child of element.childNodes) {
        if (nodes >= 30000 || children.length >= MAX_NATIVE_CHILDREN) {
          active.nativeStructuralFailure = nodes >= 30000 ? "max-nodes" : "max-children";
          truncated = true;
          break;
        }
        const value = build(child, depth + 1);
        if (value) children.push(value);
      }
      // Empty cells still occupy a column; dropping them shifts every later
      // cell and can make text spill into the following paragraph.
      // A visible but unfilled Grid region is meaningful layout, even when
      // it contains no approved text or raster children. The 0db39fd4
      // fixture includes a 423px gallery reservation that was collapsed,
      // shifting every later section. Preserve only bounded in-scope boxes.
      const emptyGridSpacer = mapped === "div" && computed.display === "grid" &&
        rect && rect.width >= 24 && rect.height >= 12 && rect.height <= 2048 &&
        rect.width <= 3000 && children.length === 0;
      // Empty 17px block with 16px top margin after a table in the
      // fa937767 sample; dropping it shifts all following sections 33px.
      // Limit this to visible, contentless, bounded blocks within reply scope.
      const emptyBlockSpacer = mapped === "div" && computed.display === "block" &&
        rect && rect.width >= 240 && rect.width <= 3000 &&
        rect.height >= 12 && rect.height <= 48 &&
        children.length === 0 &&
        ((/^\d{1,2}(?:\.\d{1,3})?px$/.test(computed.marginTop) &&
          parseFloat(computed.marginTop) >= 8) ||
          // A 17px separator after an OL and before an H2 remains even
          // without a positive margin in the 0db39fd4 tail.
          (element.previousElementSibling?.matches?.("ol,ul") &&
           element.nextElementSibling?.matches?.("h1,h2,h3,h4")));
      if (!children.length && !["hr","br","td","th"].includes(mapped) &&
          !emptyGridSpacer && !emptyBlockSpacer) return null;
      nodes += 1;
      const result = {tag:mapped,style:safeStyle(element)};
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
      || replyRegion?.scope === "verified_reply_container")
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
  const MAX_IDENTITY_BYTES = 4 * 1024 * 1024;
  const HASH_HEX = /^[a-f0-9]{64}$/;
  async function hashVisibleRaster(img) {
    const url=String(img.currentSrc || img.getAttribute?.("src") || "");
    if (!url || !globalThis.crypto?.subtle) return "";
    try {
      if (/^https:\/\//i.test(url)) {
        // Existing background allowlist enforces sender/tab and trusted
        // OpenAI/CDN hosts. It never returns image bytes to this observer.
        const result=await chrome.runtime.sendMessage({
          type:"chat2api.media.hash-v2",url,
        });
        return result?.ok && HASH_HEX.test(result.sha256) ? result.sha256 : "";
      }
      if (!/^(blob:|data:image\/)/i.test(url)) return "";
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
       || scoped?.scope==="verified_reply_container") && scoped.roots?.length
      ? scoped.roots : [candidate];
    const images=[];
    for (const root of roots) {
      if (!(root instanceof Element) || !root.isConnected) continue;
      const candidates=root.tagName?.toLowerCase()==="img"
        ? [root] : [...(root.querySelectorAll?.("img") || [])];
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
    // the terminal. Never make a stalled CDN or blob fetch delay OpenAI
    // completion for 40 * 6.5 seconds. At most 3.5 seconds total, four
    // concurrent fetches; unverified positions remain explicitly unmatched.
    const deadline=Date.now()+3500;
    for (let start=0;start<images.length && Date.now()<deadline;start+=4) {
      const batch=images.slice(start,start+4);
      let timer;
      try {
        const remaining=Math.max(1,deadline-Date.now());
        const result=await Promise.race([
          Promise.all(batch.map(hashVisibleRaster)),
          new Promise(resolve=>{timer=setTimeout(()=>resolve(null),remaining);}),
        ]);
        if (!result) break;
        batch.forEach((img,index)=>{
          if (HASH_HEX.test(result[index])) digests.set(img,result[index]);
        });
      } finally {if(timer)clearTimeout(timer);}
    }
    active.nativeImageDigests=digests;
    active.nativeIdentityVerifiedCount=digests.size;
    active.nativeIdentityCandidateCount=images.length;
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
      if (final) await resolveImageIdentities(active);
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
