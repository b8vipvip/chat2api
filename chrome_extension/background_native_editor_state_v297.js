/* Read-only authenticated Chrome MAIN-world CodeMirror document extraction.
 * A content script in ISOLATED cannot inspect CodeMirror's JS-owned .cmView.
 * Never paste page code into the extension, never dispatch editor transactions.
 * A per-request verified assistant scope marks one editor with a short-lived
 * random DOM probe. Browser scripting runs a *packaged function*, not eval.
 */
(() => {
  "use strict";
  const KIND = "chat2api.native.editor.read-v297";
  const PROBE_ATTR = "data-chat2api-native-code-probe-v297";
  const REQUEST = /^req_[a-f0-9]{32}$/;
  const TOKEN = /^[a-f0-9]{32}$/;
  const HOSTS = new Set(["chatgpt.com", "www.chatgpt.com", "chat.openai.com"]);
  function trustedTab(tab) {
    if (!Number.isInteger(tab?.id)) return false;
    try {
      const url = new URL(tab.url || tab.pendingUrl || "");
      return url.protocol === "https:" && HOSTS.has(url.hostname);
    } catch (_) { return false; }
  }
  function readCodeMirrorDocument(probe) {
    // Executed by Chrome scripting in the ChatGPT MAIN world. Return only
    // the inert bounded text and counts; never send page globals or HTML.
    const node = document.querySelector(
      '[data-chat2api-native-code-probe-v297="' + probe + '"]'
    );
    if (!node || !node.isConnected || !node.matches?.(".cm-editor") ||
        node.closest?.(
          "form,nav,aside,footer,[role='dialog'],[role='menu'],[data-message-author-role='user']"
        )) return {ok:false,reason:"untrusted-code-editor"};
    if (!node.closest?.("main,[role='main'],[data-testid^='conversation-turn'],#thread"))
      return {ok:false,reason:"editor-outside-conversation"};
    // Only the verified request-owned CodeMirror editor is inspected. A
    // large virtualized editor may mount cmView below the first 180 nodes;
    // accept CM6's rootView.view reference, not rendered cm-line snippets.
    const MAX_EDITOR_SCAN=4096;
    const surface=node.querySelector?.(".cm-content");
    const foundDocs=new Set(), seen=new Set();
    let attachments=0,scanned=0,scanBudget=false;
    function inspectNode(element) {
      if (!element || seen.has(element)) return;
      seen.add(element);
      let cm;
      try { cm=element.cmView; } catch (_) { return; }
      if (!cm || typeof cm!=="object") return;
      attachments++;
      let possible;
      try {
        possible=[
          cm.view,cm.rootView?.view,cm.root?.view,
          cm.parent?.rootView?.view,cm.editorView,
          element.view,
        ];
      } catch (_) {return;}
      for (const view of possible) {
        let candidateDoc;
        try {candidateDoc=view?.state?.doc;} catch (_) {continue;}
        if (candidateDoc && typeof candidateDoc==="object")
          foundDocs.add(candidateDoc);
      }
    }
    for (const element of [
      surface,node,node.querySelector?.(".cm-scroller"),
      surface?.firstElementChild,surface?.parentElement,
      node.querySelector?.(".cm-line"),
    ]) inspectNode(element);
    if (foundDocs.size===0) {
      const queue=[node];
      for (let head=0;head<queue.length;head++) {
        if (scanned>=MAX_EDITOR_SCAN) {scanBudget=true;break;}
        const element=queue[head];
        scanned++;
        inspectNode(element);
        if (foundDocs.size>1) break;
        // Open shadow roots belong to this same editor, not to other turns.
        for (const root of [element?.shadowRoot,element]) {
          for (const child of root?.children || []) {
            if (queue.length>=MAX_EDITOR_SCAN+1) {scanBudget=true;break;}
            queue.push(child);
          }
        }
      }
    }
    // CodeMirror may have been detached after render. The real v301 case
    // scanned the whole editor (158 nodes), saw 36 viewport lines plus a
    // .cm-gap, but had ZERO cmView attachments. In that case try a *single*
    // corroborated code string from the React owner of this SAME code card.
    // This never reads a page-wide app store or walks other assistant turns.
    function readReactOwnerCode() {
      const proof={react_fiber_hosts:0,react_candidate_count:0,
        react_anchor_count:0,react_props_scanned:0};
      const visible=[...(node.querySelectorAll?.(".cm-content .cm-line") || [])]
        .slice(0,4096).map(line=>String(line.textContent||"").trim());
      const long=visible.filter(line=>line.length>=16 && line.length<=320);
      const distinct=[...new Set(long)];
      if (distinct.length<3) return {proof,reason:"editor-react-visible-anchors-missing"};
      const anchors=[distinct[0],distinct[Math.floor(distinct.length/2)],
        distinct[distinct.length-1]];
      const normalized=value=>String(value).replace(/\r\n?/g,"\n");
      const gap=Boolean(node.querySelector?.(".cm-gap"));
      const matched=new Set();
      const watched=new Set();
      const acceptedNames=new Set(["code","source","value","text","content",
        "doc","initialCode","initialValue","codeText","sourceCode","defaultCode",
        "defaultValue","children"]);
      const nestedNames=new Set(["data","block","editor","document","props","node"]);
      function witness(value) {
        if (typeof value!=="string" || value.length<80 || value.length>150000)
          return false;
        const candidate=normalized(value);
        if (/(?:^|\n)\s*(?:\x60{3}|~{3})/.test(candidate)) return false;
        const rows=candidate.split("\n").map(line=>line.trim());
        if (gap && rows.length<=visible.length) return false;
        let before=-1;
        for (const anchor of anchors) {
          const index=rows.indexOf(anchor);
          if (index<0 || index<=before || rows.lastIndexOf(anchor)!==index)
            return false;
          before=index;
        }
        matched.add(candidate);
        return true;
      }
      function inspectProps(props,depth=0) {
        if (!props || typeof props!=="object" || watched.has(props)
            || proof.react_props_scanned>=180) return;
        watched.add(props);
        proof.react_props_scanned++;
        let descriptors;
        try {descriptors=Object.getOwnPropertyDescriptors(props);}
        catch (_) {return;}
        for (const [key,desc] of Object.entries(descriptors).slice(0,64)) {
          if (!Object.prototype.hasOwnProperty.call(desc,"value")) continue;
          if (acceptedNames.has(key)) witness(desc.value);
          if (depth<2 && nestedNames.has(key) &&
              desc.value && typeof desc.value==="object")
            inspectProps(desc.value,depth+1);
        }
      }
      const visitedFibers=new Set();
      let host=node;
      for (let height=0;height<6 && host;height++,host=host.parentElement) {
        // Stop at the assistant turn boundary, never inspect its siblings.
        if (host.matches?.('[data-message-author-role="user"]')) break;
        let names=[];
        try {names=Object.getOwnPropertyNames(host).filter(
          key=>key.startsWith("__reactFiber$") || key.startsWith("__reactProps$"));
        } catch (_) {continue;}
        if (names.length) proof.react_fiber_hosts++;
        for (const name of names.slice(0,4)) {
          let owner;
          try {owner=Object.getOwnPropertyDescriptor(host,name)?.value;}
          catch (_) {continue;}
          if (name.startsWith("__reactProps$")) {inspectProps(owner);continue;}
          for (let fiber=owner,hops=0;fiber && hops<10;fiber=fiber.return,hops++) {
            if (visitedFibers.has(fiber)) break;
            visitedFibers.add(fiber);
            inspectProps(fiber.memoizedProps);
            inspectProps(fiber.pendingProps);
          }
        }
        if (host.matches?.('[data-message-author-role="assistant"],[data-testid^="conversation-turn"]'))
          break;
      }
      proof.react_candidate_count=Math.min(32,matched.size);
      if (matched.size!==1)
        return {proof,reason:matched.size>1
          ? "editor-react-code-ambiguous" : "editor-react-code-unverified"};
      proof.react_anchor_count=anchors.length;
      const text=matched.values().next().value;
      return {ok:true,source:"react-owned-code-v302",text,length:text.length,proof};
    }
    let reactFallback=null;
    if (foundDocs.size===0 && !scanBudget) reactFallback=readReactOwnerCode();
    const diagnostic={
      scan_count:Math.min(MAX_EDITOR_SCAN,scanned),
      attachment_count:Math.min(MAX_EDITOR_SCAN,attachments),
      surface_found:Boolean(surface),
      scan_budget:scanBudget,
      ...(reactFallback?.proof || {}),
    };
    if (foundDocs.size>1)
      return {ok:false,reason:"editor-state-ambiguous",...diagnostic};
    const doc=foundDocs.values().next().value;
    if (!doc && reactFallback?.ok === true)
      return {ok:true,source:reactFallback.source,text:reactFallback.text,
        length:reactFallback.length,...diagnostic};
    if (!doc) {
      // The viewport is diagnostic only, never an authoritative full source.
      diagnostic.dom_lines=Math.min(MAX_EDITOR_SCAN,
        node.querySelectorAll?.(".cm-line")?.length || 0);
      diagnostic.virtual_gap=Boolean(node.querySelector?.(".cm-gap"));
      return {ok:false,reason:scanBudget
        ? "editor-attachment-scan-budget" : "editor-view-not-found",
        ...diagnostic};
    }
    const size = doc.length;
    if (!Number.isInteger(size) || size <= 0)
      return {ok:false,reason:"editor-document-empty"};
    if (size > 150000)
      return {ok:false,reason:"editor-document-too-large"};
    let text;
    try {
      // Full immutable editor state, not the viewport's .cm-line elements.
      text = typeof doc.sliceString === "function" ? doc.sliceString(0,size)
        : typeof doc.toString === "function" ? doc.toString() : null;
    } catch (_) { return {ok:false,reason:"editor-document-read"}; }
    if (typeof text !== "string" || text.length !== size || !text.trim())
      return {ok:false,reason:"editor-document-not-complete"};
    return {ok:true,source:"codemirror-state-v297",text,length:size,...diagnostic};
  }
  async function inspect(message,sender) {
    const tab = sender?.tab;
    if (!trustedTab(tab) || sender.frameId !== 0 ||
        !REQUEST.test(String(message.request_id || "")) ||
        !TOKEN.test(String(message.probe || "")))
      return {ok:false,reason:"untrusted-request-origin"};
    const results = await chrome.scripting.executeScript({
      target:{tabId:tab.id},world:"MAIN",func:readCodeMirrorDocument,
      args:[message.probe],
    });
    const result = results?.[0]?.result;
    // Bounded, content-free evidence lets a real request ZIP distinguish
    // missing CodeMirror attachment from a scan-budget or stale-view case.
    const stats={};
    for (const key of ["scan_count","attachment_count","dom_lines",
      "react_fiber_hosts","react_candidate_count","react_anchor_count",
      "react_props_scanned"]) {
      const value=result?.[key];
      if (Number.isInteger(value) && value>=0 && value<=4096)
        stats[key]=value;
    }
    for (const key of ["surface_found","scan_budget","virtual_gap"]) {
      if (typeof result?.[key]==="boolean") stats[key]=result[key];
    }
    if (!result || result.ok !== true || typeof result.text !== "string" ||
        result.text.length > 150000 || result.length !== result.text.length)
      return {ok:false,reason:String(result?.reason || "editor-main-world-unavailable").slice(0,60),...stats};
    return {...result,...stats};
  }
  chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
    if (message?.type !== KIND) return false;
    inspect(message,sender).then(sendResponse)
      .catch(()=>sendResponse({ok:false,reason:"editor-read-transport-error"}));
    return true;
  });
  globalThis.__CHAT2API_NATIVE_EDITOR_STATE_V297__ = Object.freeze({
    version:297,kind:KIND,
  });
})();
