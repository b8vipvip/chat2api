import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const guardStart=source.indexOf("  function hasUntrustedReplyInput(scope) {");
const guardEnd=source.indexOf("  // f7c8:",guardStart);
const rootStart=source.indexOf("  function canonicalEditorReplyRoot(active,");
const rootEnd=source.indexOf("  async function emit(event) {",rootStart);
assert.ok(guardStart>0 && guardEnd>guardStart
  && rootStart>guardEnd && rootEnd>rootStart,
  "both read-only guards must be owned by request-v6");
const scope={Map};
runInNewContext(source.slice(guardStart,guardEnd)+
  source.slice(rootStart,rootEnd)+
  "\nthis.pick=canonicalEditorReplyRoot;",scope);
const select=scope.pick;
function node(tag,parent=null,opts={}) {
  const n={
    tagName:tag.toUpperCase(),parentElement:parent,children:[],
    isConnected:true,kind:opts.kind||"",
    contains(target) {
      for(let p=target;p;p=p.parentElement)if(p===this)return true;
      return false;
    },
    matches(selector) {
      if(this.kind==="page" && selector.includes("main"))return true;
      if(this.kind==="editable" && selector.includes("[contenteditable]"))return true;
      if(this.kind==="user" && selector.includes("data-message-author-role"))return true;
      return false;
    },
    closest(selector) {
      for(let p=this;p;p=p.parentElement) {
        if(p.kind==="editor" && selector.includes(".cm-editor"))return p;
        if(p.kind==="user" && selector.includes("data-message-author-role"))return p;
      }
      return null;
    },
    querySelectorAll(selector) {
      const found=[],pending=[...this.children];
      while(pending.length) {
        const item=pending.shift();
        if(item.kind==="editable" && selector.includes("contenteditable"))found.push(item);
        if(item.kind==="form" && selector.includes("form"))found.push(item);
        pending.push(...item.children);
      }
      return found;
    },
    querySelector(selector) {
      const found=[],pending=[...this.children];
      while(pending.length) {
        const item=pending.shift();
        if(item.kind==="user" && selector.includes("data-message-author-role"))
          found.push(item);
        pending.push(...item.children);
      }
      return found[0]||null;
    },
  };
  if(parent)parent.children.push(n);
  return n;
}
const page=node("main",null,{kind:"page"});
const reply=node("div",page);
const candidate=node("p",reply);
const card=node("section",reply);
const editor=node("div",card,{kind:"editor"});
node("div",editor,{kind:"editable"});
const docs=new Map([[editor,{source:"react-owned-code-v302",text:"import os\nprint(os.name)"}]]);
const active={lastCandidateNode:candidate,nativeVerifiedReplyScope:reply,nativeEditorDocs:docs};
assert.equal(select(active),reply,
  "full code-card sibling must be included when reply root was positively verified");
assert.equal(select({...active,nativeVerifiedReplyScope:null},reply),reply,
  "verified native same-request Message ID is another authorized reply root");
assert.equal(select({...active,nativeVerifiedReplyScope:null}),null,
  "nested paragraph cannot be promoted to whole reply without verified root");
assert.equal(select({...active,nativeVerifiedReplyScope:page}),null,
  "main/history root must NEVER replace an assistant scope");
assert.equal(select({...active,nativeVerifiedReplyScope:null},page),null,
  "fallback native selector cannot widen to whole chat page");
const otherReply=node("div",page);
assert.equal(select({...active,nativeVerifiedReplyScope:null},otherReply),null,
  "another assistant turn cannot own this request's editor");
const foreign=node("div",otherReply,{kind:"editor"});
assert.equal(select({...active,nativeEditorDocs:new Map([[foreign,{}]])}),null,
  "unrelated editor cannot piggyback on a verified reply root");
const editable=node("div",reply,{kind:"editable"});
assert.equal(select(active),null,
  "genuine untrusted editable form input inside reply is never trusted");
reply.children=reply.children.filter(child=>child!==editable);
editor.isConnected=false;
assert.equal(select(active),null,"detached editor source cannot be reused after navigation");
editor.isConnected=true;
assert.equal(select(active),reply);

assert.ok(source.includes("canonicalEditorReplyRoot(active,nativeSelectionRoot)"));
assert.ok(source.includes("rich.mergeVerifiedEditorMarkdown(event.text,\n                codeRoot,active.nativeEditorDocs)"));
assert.ok(!source.includes("rich.mergeVerifiedEditorMarkdown(event.text,\n              active.lastCandidateNode"));
console.log("v309 canonical editor splice uses verified same-request whole assistant scope, not a nested paragraph");
