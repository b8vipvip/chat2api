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
        || wrap.querySelector?.(USER) || selection.querySelector?.(USER+","+INVALID))
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
      return fail(fresh.length ? "ambiguous-new-assistant-ids" : "no-new-assistant-id",fresh.length);
    const selected=fresh[0];
    const owner=pageCandidate?.closest?.(SEL);
    // If request-v6 already has an independently selected assistant
    // identity, do not silently replace it with a different reply.
    if (owner && owner!==selected.node)
      return fail("candidate-id-mismatch",fresh.length);
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
