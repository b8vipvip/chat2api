import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/content_native_content_v1.js","utf8");
const ALL_CODE="import math\n\ndef check(value):\n    return math.isfinite(value)";
class Element {
  constructor(tagName,children=[]) {
    this.tagName=tagName;this.childNodes=children;this.parentElement=null;
    this.isConnected=true;this.attrs=new Map();
    for(const child of children) if(child instanceof Element) child.parentElement=this;
  }
  closest(){return null;}
  matches(){return false;}
  getBoundingClientRect(){return {width:800,height:500};}
  querySelector(){return null;}
  querySelectorAll(){return [];}
  setAttribute(key,value){this.attrs.set(key,value);}
  getAttribute(key){return this.attrs.get(key)||null;}
  removeAttribute(key){this.attrs.delete(key);}
}
const editor=new Element("DIV");
editor.matches=selector=>selector===".cm-editor,.monaco-editor";
editor.querySelectorAll=selector=>selector===".cm-content .cm-line"
  ? [{textContent:"import math"}] : [];
editor.querySelector=selector=>selector===".cm-gap"?{}:null;
const root=new Element("DIV",[editor]);
root.querySelectorAll=selector=>selector===".cm-editor"?[editor]:[];
let mode="full";
const sent=[];
const ctx={
  Element,Node:{TEXT_NODE:3},Date,setTimeout,clearTimeout,TextEncoder,
  crypto:{randomUUID:()=>"aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"},
  getComputedStyle:()=>({display:"block",visibility:"visible",whiteSpace:"pre"}),
  __CHAT2API_NATIVE_DOM_EVIDENCE_V1__:{replyRoots:()=>({
    scope:"verified_native_assistant_selection",roots:[root],
  })},
  chrome:{runtime:{sendMessage:async message=>{
    sent.push(message);
    if(message.type==="chat2api.native.editor.read-v297")return mode==="full"?
      {ok:true,source:"codemirror-state-v297",text:ALL_CODE,length:ALL_CODE.length}:
      {ok:false,reason:"editor-main-world-unavailable"};
    if(message.type==="chat2api.native.ack")return {ok:true,committed:true};
    throw Error("unrecognized request");
  }}},
};
runInNewContext(source,ctx);
const observer=ctx.__CHAT2API_NATIVE_CONTENT_V1__;
const active={requestId:"req_"+"b".repeat(32),submitStartedAt:Date.now(),
  lastCandidateNode:root};
await observer.capture(active,true);
assert.equal(active.nativeEditorStateCandidateCount,1);
assert.equal(active.nativeEditorStateResolvedCount,1);
assert.equal(active.nativeFinalCaptureCommitted,true);
assert.equal(editor.getAttribute("data-chat2api-native-code-probe-v297"),null,
  "temporary MAIN-world selector must not stay on the real page");
assert.equal(sent.filter(x=>x.type==="chat2api.native.editor.read-v297").length,1);
const event=sent.find(x=>x.type==="chat2api.native.ack").event;
assert.equal(event.type,"chat.native.snapshot");
const nodes=[event.snapshot.root];
let codeNode=null;
while(nodes.length){
  const node=nodes.pop();
  if(node.tag==="pre")codeNode=node;
  nodes.push(...(node.children||[]));
}
assert.ok(codeNode);
assert.equal(codeNode.children[0].children[0].text,ALL_CODE);
mode="unavailable";sent.length=0;
const failed={requestId:"req_"+"c".repeat(32),submitStartedAt:Date.now(),
  lastCandidateNode:root};
await observer.capture(failed,true);
assert.equal(failed.nativeFinalCaptureCommitted,false);
assert.equal(failed.nativeCaptureFailure,"structural-code-editor-virtualized");
assert.equal(sent.some(x=>x.type==="chat2api.native.ack"),false);
assert.equal(editor.getAttribute("data-chat2api-native-code-probe-v297"),null);
console.log("v297 verified final CodeMirror document recovered; failure remains fail-closed");
