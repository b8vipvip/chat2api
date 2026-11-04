/* Resolve ChatGPT's semantic conversation markers to one message-owned DOM scope.
 * No text similarity, page-position guessing, CSS classes or pixel constants.
 */
(() => {
  "use strict";
  const KEY="__CHAT2API_NATIVE_MESSAGE_IDENTITY_V1__";
  if(globalThis[KEY])return;
  const USER="[data-message-author-role='user'],[data-conversation-role='user'],[data-testid='user-message']";
  const USER_UNIT="[data-chatgpt-search-unit-key$=':user'],[data-content-search-unit-key$=':user']";
  const SELECTION="[data-chatgpt-selection-message-id]";
  const WRAPPER="[data-chatgpt-search-message-ids]";
  const MARKDOWN="[data-markdown-text-style='assistant-message']";
  function visible(node){
    try{const r=node.getBoundingClientRect(),s=getComputedStyle(node);
      return r.width>0&&r.height>0&&s.display!=="none"&&s.visibility!=="hidden";
    }catch(_){return false;}
  }
  function isUser(node){
    if(!(node instanceof Element))return false;
    if(node.matches(USER)||node.matches(USER_UNIT)||node.closest(USER_UNIT))return true;
    if(node.querySelector(USER)||node.querySelector(USER_UNIT))return true;
    const wrapper=node.closest(WRAPPER);
    return Boolean(wrapper?.querySelector("h4[data-conversation-role='user']"));
  }
  function resolveAssistantSelection(node){
    if(!(node instanceof Element))return null;
    const selection=node.matches(SELECTION)?node:node.closest(SELECTION);
    if(!selection||!selection.isConnected||!visible(selection)||isUser(selection))return null;
    const id=String(selection.getAttribute("data-chatgpt-selection-message-id")||"");
    const wrapper=selection.closest(WRAPPER);
    const ids=String(wrapper?.getAttribute("data-chatgpt-search-message-ids")||"").split(/\s+/);
    if(!id||!wrapper||!ids.includes(id)||isUser(wrapper))return null;
    if(!wrapper.querySelector("h4[data-conversation-role='assistant']"))return null;
    const markdown=selection.querySelector(MARKDOWN);
    if(!markdown||!visible(markdown))return null;
    const dil=[...markdown.querySelectorAll("[data-dil-message-id]")];
    const source=[...markdown.querySelectorAll("[data-dil-source-message-id]")];
    if(dil.length&&!dil.some(node=>node.getAttribute("data-dil-message-id")===id))return null;
    if(source.length&&!source.some(node=>node.getAttribute("data-dil-source-message-id")===id))return null;
    return selection;
  }
  function assistantMessages(doc=document){
    const result=[],seen=new Set();
    const add=node=>{
      if(!(node instanceof Element))return;
      const native=resolveAssistantSelection(node)
        ||[...(node.querySelectorAll?.(SELECTION)||[])].map(resolveAssistantSelection).find(Boolean);
      const target=native||node;
      if(!target.isConnected||!visible(target)||isUser(target)||seen.has(target))return;
      seen.add(target);result.push(target);
    };
    for(const heading of doc.querySelectorAll("h4[data-conversation-role='assistant']")){
      const wrapper=heading.closest(WRAPPER);
      if(!wrapper)continue;
      for(const selection of wrapper.querySelectorAll(SELECTION))add(selection);
    }
    for(const node of doc.querySelectorAll("[data-message-author-role='assistant']"))add(node);
    return result;
  }
  function userMessages(doc=document){
    const result=[],seen=new Set();
    for(const selector of ["[data-chatgpt-search-unit-key$=':user']",
      "[data-content-search-unit-key$=':user']","[data-message-author-role='user']",
      "h4[data-conversation-role='user']"]){
      for(const node of doc.querySelectorAll(selector)){
        const target=node.closest(USER_UNIT)||node.closest(
          "article[data-testid^='conversation-turn'],[data-testid^='conversation-turn'],article[data-message-id]")||node;
        if(!isUser(target)||!target.isConnected||!visible(target)||seen.has(target))continue;
        seen.add(target);result.push(target);
      }
    }
    return result;
  }
  function turnOf(node){
    return node?.closest?.(SELECTION)
      ||node?.closest?.(USER_UNIT)
      ||node?.closest?.("article[data-testid^='conversation-turn'],[data-testid^='conversation-turn'],article[data-message-id]")
      ||node||null;
  }
  function messageId(node){
    const turn=turnOf(node);
    return String(turn?.getAttribute?.("data-chatgpt-selection-message-id")
      ||turn?.getAttribute?.("data-message-id")||turn?.dataset?.messageId
      ||turn?.id||turn?.getAttribute?.("data-testid")||"");
  }
  globalThis[KEY]=Object.freeze({visible,isUser,resolveAssistantSelection,
    assistantMessages,userMessages,turnOf,messageId});
})();
