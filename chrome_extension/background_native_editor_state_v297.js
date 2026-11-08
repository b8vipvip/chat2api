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
    // CodeMirror v6 attaches cmView to different content/viewport nodes
    // across builds. The previous hard-coded .cm-content.cmView.view path
    // reported editor-document-size even when no state.doc was found.
    // Inspect ONLY the already-scoped editor and its immediate children.
    // No page-wide probing, clipboard actions, scrolling, eval or mutation.
    const surface = node.querySelector?.(".cm-content");
    const candidates = [
      surface, node, node.querySelector?.(".cm-scroller"),
      surface?.firstElementChild, surface?.parentElement,
      node.querySelector?.(".cm-line"),
    ].filter(Boolean);
    let doc = null;
    for (const element of candidates) {
      const cm = element?.cmView;
      const possible = [
        cm?.view, cm?.rootView?.view, cm?.root?.view,
        cm?.editorView, cm,
        element?.view,
      ];
      for (const view of possible) {
        if (!view || typeof view !== "object") continue;
        const candidateDoc = view?.state?.doc;
        if (candidateDoc && typeof candidateDoc === "object") {
          doc = candidateDoc;
          editorViewLocated = true;
          break;
        }
      }
      if (doc) break;
    }
    if (!doc) {
      // Distinguish missing JS-owned EditorView from a too-large document.
      // Do not expose class names, content, urls or page globals.
      return {ok:false,reason:"editor-view-not-found"};
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
    return {ok:true,source:"codemirror-state-v297",text,length:size};
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
    if (!result || result.ok !== true || typeof result.text !== "string" ||
        result.text.length > 150000 || result.length !== result.text.length)
      return {ok:false,reason:String(result?.reason || "editor-main-world-unavailable").slice(0,60)};
    return result;
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
