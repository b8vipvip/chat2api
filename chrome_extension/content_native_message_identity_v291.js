/* Shared Windows historical / request-scoped ChatGPT native assistant identity.
 * Pure, read-only DOM reasoning: no terminal, no capture and no page-content
 * export. History selection is NOT request ownership. The request-scoped
 * resolver additionally requires a pre-submit baseline and current network.
 */
(() => {
  "use strict";
  const KEY = "__CHAT2API_NATIVE_MESSAGE_IDENTITY_V291__";
  if (globalThis[KEY]) return;
  const SEL = "[data-chatgpt-selection-message-id]";
  const WRAP = "[data-chatgpt-search-message-ids]";
  const HEAD = "h4[data-conversation-role='assistant']";
  const MD = "[data-markdown-text-style='assistant-message']";
  const USER = "[data-message-author-role='user'],[data-conversation-role='user'],[data-testid='user-message'],h4[data-conversation-role='user']";
  const INVALID = "form,textarea,input,[contenteditable='true'],[role='dialog']";
  const ID = /^[A-Za-z0-9-]{12,100}$/;
  const defaultVisible = node => {
    try {
      const r=node?.getBoundingClientRect?.(),s=getComputedStyle(node);
      return Boolean(r && r.width>0 && r.height>0
        && s.display!=="none" && s.visibility!=="hidden");
    } catch (_) { return false; }
  };
  function inspect(selection, visible=defaultVisible) {
    if (!selection?.isConnected || !visible(selection)) return null;
    const id=String(selection.getAttribute?.("data-chatgpt-selection-message-id")||"");
    if (!ID.test(id)) return null;
    const wrap=selection.closest?.(WRAP);
    if (!wrap?.isConnected || !wrap.contains?.(selection)
        || !String(wrap.getAttribute?.("data-chatgpt-search-message-ids")||"")
          .split(/\s+/).includes(id)
        || wrap.querySelector?.(USER) || selection.querySelector?.(USER)
        || selection.closest?.(INVALID) || wrap.closest?.(INVALID))
      return null;
    const heading=wrap.querySelector?.(HEAD);
    const markdown=selection.querySelector?.(MD);
    if (!heading?.isConnected || !visible(heading)
        || !markdown?.isConnected || !selection.contains?.(markdown)
        || !visible(markdown)) return null;
    const dil=markdown.querySelector?.("[data-dil-message-id]");
    const source=markdown.querySelector?.("[data-dil-source-message-id]");
    if ((dil && dil.getAttribute?.("data-dil-message-id")!==id)
        || (source && source.getAttribute?.("data-dil-source-message-id")!==id))
      return null;
    return {id,node:selection,heading};
  }
  function collect(root=globalThis.document, visible=defaultVisible) {
    if (!root?.querySelectorAll) return [];
    const result=[],seen=new Set();
    // A single shared identity check is used in the Windows read-only probe
    // and in the real Worker, never copied as diverging selectors.
    for (const selection of root.querySelectorAll(SEL)) {
      const entry=inspect(selection,visible);
      if (entry && !seen.has(entry.node)) {
        seen.add(entry.node);result.push(entry);
      }
    }
    return result;
  }
  function baseline(root=globalThis.document,visible=defaultVisible) {
    // Snapshot ALL pre-submit selection identities, including hidden or
    // half-hydrated historical nodes. Inspecting only currently valid/visible
    // Markdown would misclassify a historical node as fresh when it finishes
    // hydrating *after* the API dispatch.
    const nodes=new Set(),ids=new Set();
    for(const node of root?.querySelectorAll?.(SEL) || []){
      nodes.add(node);
      const id=String(node.getAttribute?.("data-chatgpt-selection-message-id")||"");
      if(ID.test(id))ids.add(id);
    }
    return {ids,nodes};
  }
  // Diagnose WHY a long rich reply failed identity matching, without
  // exposing IDs, prompt text, DOM markup, URLs or attribute values.
  // The helper remains read-only; all counters are capped before logging.
  function rejectionFacts(active,root,visible=defaultVisible) {
    const keys=[
      "selection_nodes","historical_nodes","fresh_nodes","invalid_id_nodes",
      "hidden_fresh","bad_wrapper","missing_assistant_heading",
      "missing_markdown","user_contaminated","dil_mismatch",
      "outside_main","eligible_fresh","rich_controls_inside","scan_truncated"
    ];
    const facts=Object.fromEntries(keys.map(key=>[key,0]));
    const increment=key=>{facts[key]=Math.min(32,facts[key]+1);};
    let inspected=0;
    for (const node of root?.querySelectorAll?.(SEL) || []) {
      if (inspected++>=256) {facts.scan_truncated=1;break;}
      increment("selection_nodes");
      const id=String(node.getAttribute?.("data-chatgpt-selection-message-id")||"");
      if(!ID.test(id)){increment("invalid_id_nodes");continue;}
      if(active?.baselineNativeMessageIds?.has(id)
          || active?.baselineNativeSelectionNodes?.has(node)){
        increment("historical_nodes");continue;
      }
      increment("fresh_nodes");
      if(node.querySelector?.(INVALID))increment("rich_controls_inside");
      if(!node.isConnected || !visible(node)){increment("hidden_fresh");continue;}
      const wrap=node.closest?.(WRAP);
      if(!wrap?.isConnected || !wrap.contains?.(node)
          || !String(wrap.getAttribute?.("data-chatgpt-search-message-ids")||"")
             .split(/\\s+/).includes(id)
          || node.closest?.(INVALID) || wrap.closest?.(INVALID)){
        increment("bad_wrapper");continue;
      }
      if(wrap.querySelector?.(USER) || node.querySelector?.(USER)){
        increment("user_contaminated");continue;
      }
      const heading=wrap.querySelector?.(HEAD);
      if(!heading?.isConnected || !visible(heading)){
        increment("missing_assistant_heading");continue;
      }
      const md=node.querySelector?.(MD);
      if(!md?.isConnected || !node.contains?.(md) || !visible(md)){
        increment("missing_markdown");continue;
      }
      const dil=md.querySelector?.("[data-dil-message-id]");
      const source=md.querySelector?.("[data-dil-source-message-id]");
      if((dil && dil.getAttribute?.("data-dil-message-id")!==id)
          ||(source && source.getAttribute?.("data-dil-source-message-id")!==id)){
        increment("dil_mismatch");continue;
      }
      if(!node.closest?.("main,[role='main'],#thread")){
        increment("outside_main");continue;
      }
      increment("eligible_fresh");
    }
    return facts;
  }

  function resolveRequest(active, candidate=null, root=globalThis.document,
                          visible=defaultVisible) {
    const fail=(reason,count=0)=>({ok:false,reason,candidate_count:Math.min(32,count)});
    if (!active?.requestId || typeof active.baselineNativeMessageIds?.has!=="function"
        || typeof active.baselineNativeSelectionNodes?.has!=="function"
        || typeof active.baselineRolelessHeadings?.has!=="function"
        || !active.baselineCapturedAt || !active.submitStartedAt
        || active.baselineCapturedAt>active.submitStartedAt
        || active.networkStreamSeen!==true
        || Number(active.networkObservedAt||0)<active.submitStartedAt
        || active.cancelled || active.options?.voice===true
        || active.options?.mode==="voice")
      return fail("preconditions");
    const pageCandidate=candidate?.isConnected ? candidate : null;
    // All IDs are on the same request-owned browser tab; never accept
    // a last historical assistant selection or an old heading.
    const fresh=collect(root,visible).filter(item=>
      !active.baselineNativeMessageIds.has(item.id)
      && !active.baselineNativeSelectionNodes.has(item.node)
      && !active.baselineRolelessHeadings.has(item.heading)
      && item.node.closest?.("main,[role='main'],#thread"));
    if (fresh.length!==1)
      return {...fail(fresh.length ? "ambiguous-new-assistant-ids" : "no-new-assistant-id",fresh.length),
        facts:rejectionFacts(active,root,visible)};
    const selected=fresh[0];
    const owner=pageCandidate?.closest?.(SEL);
    // If request-v6 already has an independently selected assistant
    // identity, do not silently replace it with a different reply.
    if (owner && owner!==selected.node) {
      // request-v6 can retain the old final assistant paragraph while a new
      // assistant card is being hydrated. If that *exact* old selection, its
      // ID, and its heading were all present before submit, its candidate is
      // historical evidence, not a competing answer. Otherwise fail closed.
      const old=inspect(owner,visible);
      const historical=old
        && active.baselineNativeMessageIds.has(old.id)
        && active.baselineNativeSelectionNodes.has(owner)
        && active.baselineRolelessHeadings.has(old.heading);
      if (!historical) return fail("candidate-id-mismatch",fresh.length);
    }
    // Never export UUID or assistant body in logging: return a *live*
    // DOM node to the same tab and only bounded status/count telemetry.
    return {ok:true,reason:"verified-native-message-id",candidate_count:1,
      node:selected.node,id:selected.id,heading:selected.heading};
  }
  const structuralTags=new Set([
    "h1","h2","h3","h4","h5","h6","p","table","pre","img","svg",
    "canvas","blockquote","ul","ol","math"
  ]);
  function structuralManifest(scope,maxNodes=30000) {
    // Phase-2 regression input: order/type only. This is NOT visual parity
    // and never reads text, URLs, images or serialized HTML.
    if (!scope?.querySelectorAll) return {ok:false,reason:"no-scope"};
    const ordered=[];
    for (const node of scope.querySelectorAll(
      "h1,h2,h3,h4,h5,h6,p,table,pre,img,svg,canvas,blockquote,ul,ol,math")) {
      if (ordered.length>=maxNodes)
        return {ok:false,reason:"structural-overflow",count:ordered.length};
      const tag=String(node.tagName||"").toLowerCase();
      if (!structuralTags.has(tag)
          || node.closest?.(USER+",nav,aside,header,footer,form,[role='dialog']"))
        continue;
      // A nested math/code cell is owned by its outer structural block.
      if ((tag==="svg" && node.parentElement?.closest?.("svg"))
          || (tag==="p" && node.parentElement?.closest?.("table")))
        continue;
      ordered.push(tag);
    }
    return {ok:true,ordered,count:ordered.length,
      images:ordered.filter(tag=>tag==="img").length,
      tables:ordered.filter(tag=>tag==="table").length};
  }
  globalThis[KEY]=Object.freeze({
    version:291,inspect,collect,baseline,resolveRequest,structuralManifest,
  });
})();
