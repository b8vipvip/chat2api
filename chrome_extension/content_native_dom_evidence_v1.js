/* Request-scoped, administrator-only visual evidence. It does not change
 * model text, terminal status, page execution, or browser network traffic.
 * Never capture user prompts, sidebar, cookies, URL tokens, input values,
 * canvas pixels, JavaScript, raw HTML, or unrelated conversation turns.
 */
(() => {
  "use strict";
  const KEY = "__CHAT2API_NATIVE_DOM_EVIDENCE_V1__";
  if (globalThis[KEY]) return;
  // 3a882b exceeded the old 6,400-node forensic limit before reaching
  // its last tables/images. Keep bounded private evidence, without exporting
  // source URLs or unrelated turns.
  const MAX_NODES = 30000, MAX_TEXT = 192000, MAX_BYTES = 3 * 1024 * 1024;
  const blocked = "script,style,form,nav,aside,header,footer,textarea,input,select,button,[contenteditable='true'],[role='dialog'],[role='menu'],[data-message-author-role='user']";
  const allowed = new Set("div section article p span h1 h2 h3 h4 h5 h6 strong em ul ol li table thead tbody tr td th pre code figure figcaption img blockquote a hr br".split(" "));
  const n = v => Math.max(-100000, Math.min(100000, Math.round(Number(v) || 0)));
  const clean = s => String(s || "").replace(/[\u0000-\u001f\u007f]/g," ").slice(0,400);
  const display = s => ["block","inline","inline-block","flex","inline-flex","grid","inline-grid","table","table-row","table-cell","list-item"].includes(s) ? s : "other";
  // Match the production native-delivery observer's composed DOM walk.
  // Only OPEN shadows within a verified reply are traversed; slots use the
  // rendered assigned nodes rather than also duplicating the light children.
  function composedChildren(el) {
    if (!(el instanceof Element)) return [...(el?.childNodes||[])];
    if (el.tagName?.toLowerCase()==="slot") {
      try {
        const assigned=el.assignedNodes?.({flatten:true});
        if (assigned?.length) return [...assigned];
      } catch (_) {}
    }
    const shadow=el.shadowRoot;
    return shadow?.mode==="open"
      ? [...(shadow.childNodes||[])] : [...(el.childNodes||[])];
  }
  function composedFind(root,predicate,limit=1) {
    const stack=[{node:root,depth:0}],seen=new Set(),found=[];
    let visited=0;
    while(stack.length && found.length<limit && visited<3000){
      const {node,depth}=stack.pop();
      if(!(node instanceof Element)||seen.has(node))continue;
      seen.add(node);visited++;
      if(node!==root && node.matches?.(
        "nav,aside,header,footer,form,[role='menu'],[role='dialog'],[contenteditable='true'],[data-message-author-role='user']"))continue;
      if(predicate(node))found.push(node);
      if(depth>=64)continue;
      const children=composedChildren(node);
      for(let i=children.length-1;i>=0;i--)stack.push({node:children[i],depth:depth+1});
    }
    return found;
  }
  function visible(el) {
    try {
      const r=el.getBoundingClientRect(),s=getComputedStyle(el);
      return (r.width>0 && r.height>0 || el.shadowRoot?.mode==="open")
        && s.display!=="none" && s.visibility!=="hidden";
    } catch { return false; }
  }
  function replyRoots(active) {
    // Reuse the EXACT read-only Message ID identity resolver also invoked by
    // Windows historical diagnosis and request-v6. This route additionally
    // proves a new assistant selection in the current request's tab/baseline;
    // the diagnostic collector only proves a historical assistant identity.
    const shared=globalThis.__CHAT2API_NATIVE_MESSAGE_IDENTITY_V291__
      ?.resolveRequest?.(active,active?.lastCandidateNode,document,visible);
    if (shared?.ok && shared.node?.isConnected)
      return {roots:[shared.node],scope:"verified_native_assistant_selection",
        boundary:"same_request_shared_assistant_message_id_v291"};
    const candidate = active.lastCandidateNode;
    if (!(candidate instanceof Element) || !candidate.isConnected) return null;
    const page = candidate.closest("main,[role='main'],#thread");
    if (!page) return null;
    // request-v6 can positively bind the final reply to a fresh completion
    // toolbar without an explicit "ChatGPT said" header. Prefer that exact
    // already-verified non-page scope rather than a partial paragraph.
    const verified = active.nativeVerifiedReplyScope;
    if (verified instanceof Element && verified.isConnected &&
        verified !== page && verified.contains(candidate) &&
        !verified.matches("main,[role='main'],#thread,[data-testid='conversation-turns']") &&
        !verified.querySelector("[data-message-author-role='user'],[data-testid='user-message']")) {
      return {roots:[verified],scope:"verified_reply_container",
        boundary:"same_request_verified_finish_action"};
    }
    // Legacy roleless sibling fallback remains read-only. The previous
    // duplicate Message ID parser is intentionally removed: its rule set
    // must not diverge from the Windows and request-v6 shared resolver.
    const normalize = el => String(el?.innerText || el?.textContent || "").replace(/\s+/g," ").trim();
    // Require a freshly created ChatGPT assistant heading from the same
    // request when the role-less desktop UI is used.
    const headings = [...page.querySelectorAll("h4")].filter(h =>
      h.isConnected && visible(h) &&
      !active.baselineRolelessHeadings?.has(h) &&
      /^ChatGPT said\s*[:：]?$/i.test(normalize(h)));
    const heading = headings.at(-1);
    let roots=[], scope="candidate_only", boundary="candidate_not_scoped";
    if (heading?.parentElement) {
      const siblings=[], parent=heading.parentElement;
      let cursor=heading.nextElementSibling;
      // The old 80-sibling loop silently promoted a clipped assistant turn.
      // Keep a strict bounded scan, and mark overflow unverified instead.
      while(cursor && siblings.length<4096) {
        // A full-sized SVG or Canvas preview may be the only content of a
        // generated chart button. Treat it as a reply graphic so the roleless
        // scan does not mistake that button for the next UI toolbar and clip
        // every later assistant paragraph. Tiny control icons stay excluded.
        const visualButton=cursor.tagName==="BUTTON" &&
          composedFind(cursor,node=>{
            const tag=node.tagName?.toLowerCase();
            if(tag!=="img" && tag!=="svg" && tag!=="canvas")return false;
            if(tag==="svg" && node.parentElement?.closest?.("svg"))return false;
            const r=node.getBoundingClientRect?.();
            const min=tag==="img"?40:120;
            return r && r.width>=min && r.height>=(tag==="img"?40:80)
              && visible(node);
          }).length>0;
        if ((cursor.matches(blocked) && !visualButton) ||
            cursor.querySelector("h4")?.textContent?.match(/^You said\s*[:：]?$/i)) break;
        if (cursor.tagName==="H4" && /said\s*[:：]?$/i.test(normalize(cursor))) break;
        // Charts may contain no DOM text or <img>; the old sibling filter
        // silently omitted standalone SVG/Canvas while preserving adjacent
        // tables and paragraphs. Only admit substantial *visible* graphics
        // under this newly identified assistant heading, never page controls.
        const graphical=composedFind(cursor,node=>{
          const tag=node.tagName?.toLowerCase();
          if(tag!=="svg" && tag!=="canvas")return false;
          const r=node.getBoundingClientRect?.();
          return r&&r.width>=120&&r.height>=80&&visible(node);
        }).length>0;
        if(cursor.matches?.("[data-message-author-role='user'],[data-testid='user-message'],form")
            || cursor.querySelector?.("[data-message-author-role='user'],[data-testid='user-message'],form,textarea,[contenteditable='true']"))
          break;
        if (cursor.contains(candidate) || cursor===candidate ||
            normalize(cursor).length>=2 || graphical ||
            composedFind(cursor,node=>node.tagName?.toLowerCase()==="img" && visible(node)).length>0) {
          siblings.push(cursor);
        }
        cursor=cursor.nextElementSibling;
      }
      if (siblings.length>=4096 && cursor) {
        return {roots:[candidate],scope:"candidate_only",boundary:"reply-sibling-cap-reached"};
      }
      if (siblings.length && siblings.some(s => s===candidate || s.contains(candidate) || candidate.contains(s))) {
        roots=siblings; scope="roleless_reply_siblings";
        boundary=cursor ? "next_reply_or_nonreply_element" : "end_of_reply_parent";
      }
    }
    if (!roots.length) {
      // Fallback is explicitly marked incomplete instead of pretending the
      // selected descendant is the whole assistant response.
      roots=[candidate]; scope="candidate_only"; boundary="candidate_partial_unverified";
    }
    return {roots,scope,boundary};
  }
  function captureData(active) {
    const boundaryInfo=replyRoots(active);
    if(!boundaryInfo)return null;
    const {roots,scope,boundary}=boundaryInfo;
    const origin=roots[0].getBoundingClientRect();
    const counts={nodes:0,text_chars:0,images:0,grids:0,flex:0,tables:0,headings:0,links:0,svg:0,svg_large:0,canvas:0,canvas_large:0};
    let truncated=false;
    const convert=(node,depth)=>{
      if (counts.nodes>=MAX_NODES || depth>64) { truncated=true;return null; }
      if (node.nodeType===Node.TEXT_NODE) {
        const raw=String(node.nodeValue || "");
        if (!raw.trim()) return null;
        if (counts.text_chars+raw.length>MAX_TEXT) {truncated=true;return null;}
        counts.text_chars+=raw.length;counts.nodes++;
        return {tag:"#text",text:raw};
      }
      if (!(node instanceof Element) ||
          (node.tagName !== "BUTTON" && node.matches(blocked)) ||
          node.closest("form,[contenteditable='true'],[data-message-author-role='user']")) return null;
      if (!visible(node)) return null;
      const name=node.tagName.toLowerCase(),tag=allowed.has(name)?name:"div";
      const style=getComputedStyle(node),rect=node.getBoundingClientRect();
      const layout={
        x:n(rect.left-origin.left),y:n(rect.top-origin.top),w:n(rect.width),h:n(rect.height),
        display:display(style.display),
        flow:clean(style.flexDirection).slice(0,16),
        columns:clean(style.gridTemplateColumns).slice(0,160),
        gap:n(parseFloat(style.gap)),font:n(parseFloat(style.fontSize)),
        weight:n(style.fontWeight),align:clean(style.textAlign).slice(0,16),
      };
      if (name === "button") {
        // Match the formal native-delivery observer's request-owned media
        // boundary. <button> may wrap a large SVG chart or Canvas, not just
        // IMG. Preserve only inert geometry/facts, not controls, hrefs, text
        // labels, original URLs or Canvas pixel bytes.
        if (node.closest("nav,aside,footer,header,form,[role='menu'],[role='dialog']")) return null;
        const visuals=composedFind(node,child=>{
          const tag=child.tagName?.toLowerCase();
          if(tag!=="img" && tag!=="svg" && tag!=="canvas")return false;
          if(tag==="svg" && child.parentElement?.closest?.("svg"))return false;
          if(tag==="img" && child.closest("svg"))return false;
          const box=child.getBoundingClientRect?.();
          return box && box.width>=(tag==="img"?40:120)
            && box.height>=(tag==="img"?40:80) && visible(child)
            && !child.closest("nav,aside,footer,[data-message-author-role='user']");
        },41);
        if (visuals.length>40) { truncated=true;return null; }
        const children=[];
        for (const media of visuals) {
          const entry=convert(media,depth+1);
          if (entry) children.push(entry);
          if (truncated) break;
        }
        if (!children.length || truncated) return null;
        counts.nodes++;
        return {tag:"figure",layout,children};
      }
      counts.nodes++;if(layout.display.includes("grid"))counts.grids++;
      if(layout.display.includes("flex"))counts.flex++;
      if(tag==="table")counts.tables++;
      if(name==="svg"){counts.svg++;if(rect.width>=120&&rect.height>=80)counts.svg_large++;}
      if(name==="canvas"){counts.canvas++;if(rect.width>=120&&rect.height>=80)counts.canvas_large++;}
      if(/^h[1-6]$/.test(tag))counts.headings++;
      const output={tag,layout};
      if(tag==="img") {
        counts.images++;
        output.image={natural_width:n(node.naturalWidth),natural_height:n(node.naturalHeight),
          loaded:node.complete && node.naturalWidth>0,
          source_type:/^blob:/.test(node.currentSrc||node.src)?"blob":
            /^data:/.test(node.currentSrc||node.src)?"data":
            /^https:/.test(node.currentSrc||node.src)?"https":"other"};
        return output;
      }
      if(tag==="a") {counts.links++;output.link=true;} // never copy href/token
      const children=[];
      for(const child of composedChildren(node)){ const entry=convert(child,depth+1);
        if(entry)children.push(entry);if(truncated)break; }
      if(children.length)output.children=children;
      return output;
    };
    const rootChildren=[];
    for(const root of roots){const entry=convert(root,0);if(entry)rootChildren.push(entry);if(truncated)break;}
    const report={
      schema:"request-native-dom-evidence-v1",request_id:active.requestId,
      captured_at:new Date().toISOString(),scope,boundary,
      complete:!truncated && (scope==="roleless_reply_siblings" || scope==="verified_reply_container" || scope==="verified_native_assistant_selection"),
      counts,root:{tag:"reply",children:rootChildren},
    };
    const encoded=JSON.stringify(report);
    if(encoded.length>MAX_BYTES) {
      return {...report,complete:false,root:null,reason:"max_json_bytes"};
    }
    if(truncated){report.root=null;report.reason="capture_limit_exceeded";}
    return report;
  }
  async function capture(active,phase) {
    if (!active?.requestId) return;
    try {
      const data=captureData(active);
      if(!data)return;
      const response=chrome.runtime.sendMessage({type:"chat2api.event",event:{
        type:"chat.native.dom_evidence",request_id:active.requestId,phase,report:data,
      }});
      await Promise.race([response.catch(()=>{}),new Promise(r=>setTimeout(r,240))]);
    } catch (_) { /* diagnostic observer must not delay terminal */ }
  }
  globalThis[KEY]=Object.freeze({capture,captureData,replyRoots});
})();
