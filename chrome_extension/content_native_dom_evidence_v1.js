/* Request-scoped, administrator-only visual evidence. It does not change
 * model text, terminal status, page execution, or browser network traffic.
 * Never capture user prompts, sidebar, cookies, URL tokens, input values,
 * canvas pixels, JavaScript, raw HTML, or unrelated conversation turns.
 */
(() => {
  "use strict";
  const KEY = "__CHAT2API_NATIVE_DOM_EVIDENCE_V1__";
  if (globalThis[KEY]) return;
  const MAX_NODES = 1400, MAX_TEXT = 120000, MAX_BYTES = 300000;
  const blocked = "script,style,form,nav,aside,header,footer,textarea,input,select,button,[contenteditable='true'],[role='dialog'],[role='menu'],[data-message-author-role='user']";
  const allowed = new Set("div section article p span h1 h2 h3 h4 h5 h6 strong em ul ol li table thead tbody tr td th pre code figure figcaption img blockquote a hr br".split(" "));
  const n = v => Math.max(-100000, Math.min(100000, Math.round(Number(v) || 0)));
  const clean = s => String(s || "").replace(/[\u0000-\u001f\u007f]/g," ").slice(0,400);
  const display = s => ["block","inline","inline-block","flex","inline-flex","grid","inline-grid","table","table-row","table-cell","list-item"].includes(s) ? s : "other";
  function captureData(active) {
    if (!active?.requestId || !active.submitStartedAt || typeof Element === "undefined") return null;
    const candidate = active.lastCandidateNode;
    if (!(candidate instanceof Element) || !candidate.isConnected) return null;
    const page = candidate.closest("main,[role='main'],#thread");
    if (!page) return null;
    const normalize = el => String(el?.innerText || el?.textContent || "").replace(/\s+/g," ").trim();
    const visible = el => {
      try { const r=el.getBoundingClientRect(),s=getComputedStyle(el);
        return r.width>0 && r.height>0 && s.display!=="none" && s.visibility!=="hidden";
      } catch { return false; }
    };
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
      while(cursor && siblings.length<80) {
        if (cursor.matches(blocked) || cursor.querySelector("h4")?.textContent?.match(/^You said\s*[:：]?$/i)) break;
        if (cursor.tagName==="H4" && /said\s*[:：]?$/i.test(normalize(cursor))) break;
        if (cursor.contains(candidate) || cursor===candidate || normalize(cursor).length>=2 || cursor.querySelector("img")) {
          siblings.push(cursor);
        }
        cursor=cursor.nextElementSibling;
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
    const origin=roots[0].getBoundingClientRect();
    const counts={nodes:0,text_chars:0,images:0,grids:0,flex:0,tables:0,headings:0,links:0};
    let truncated=false;
    const convert=(node,depth)=>{
      if (counts.nodes>=MAX_NODES || depth>24) { truncated=true;return null; }
      if (node.nodeType===Node.TEXT_NODE) {
        const raw=String(node.nodeValue || "");
        if (!raw.trim()) return null;
        if (counts.text_chars+raw.length>MAX_TEXT) {truncated=true;return null;}
        counts.text_chars+=raw.length;counts.nodes++;
        return {tag:"#text",text:raw};
      }
      if (!(node instanceof Element) || node.matches(blocked) || node.closest("form,[contenteditable='true'],[data-message-author-role='user']")) return null;
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
      counts.nodes++;if(layout.display.includes("grid"))counts.grids++;
      if(layout.display.includes("flex"))counts.flex++;
      if(tag==="table")counts.tables++;
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
      for(const child of node.childNodes){ const entry=convert(child,depth+1);
        if(entry)children.push(entry);if(truncated)break; }
      if(children.length)output.children=children;
      return output;
    };
    const rootChildren=[];
    for(const root of roots){const entry=convert(root,0);if(entry)rootChildren.push(entry);if(truncated)break;}
    const report={
      schema:"request-native-dom-evidence-v1",request_id:active.requestId,
      captured_at:new Date().toISOString(),scope,boundary,
      complete:!truncated && scope==="roleless_reply_siblings",
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
  globalThis[KEY]=Object.freeze({capture,captureData});
})();
