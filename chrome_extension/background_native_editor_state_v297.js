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
    const diagnostic={
      scan_count:Math.min(MAX_EDITOR_SCAN,scanned),
      attachment_count:Math.min(MAX_EDITOR_SCAN,attachments),
      surface_found:Boolean(surface),
      scan_budget:scanBudget,
    };
    if (foundDocs.size>1)
      return {ok:false,reason:"editor-state-ambiguous",...diagnostic};
    const doc=foundDocs.values().next().value;
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
    for (const key of ["scan_count","attachment_count","dom_lines"]) {
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
