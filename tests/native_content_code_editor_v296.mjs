// A verified native assistant code card must not lose its Python body simply
// because a CodeMirror display uses contenteditable=true internally.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const code=readFileSync("chrome_extension/content_native_content_v1.js","utf8");
class Element {
  constructor(tagName, children=[]) {
    this.tagName=tagName;this.childNodes=children;this.isConnected=true;
    this.parentElement=null;
    for(const c of children)if(c instanceof Element)c.parentElement=this;
  }
  getBoundingClientRect(){return {width:768,height:400};}
  matches(){return false;}
  closest(){return null;}
  getAttribute(){return null;}
  querySelectorAll(){return [];}
  querySelector(){return null;}
}
const lines=["import math","","def check(value):","    return math.isfinite(value)"];
const editor=new Element("DIV",[]);
editor.matches=s=>s.includes(".cm-editor") || s === ".cm-editor,.monaco-editor";
editor.querySelectorAll=s=>s===".cm-content .cm-line"
  ? lines.map(text=>({textContent:text})) : [];
const root=new Element("DIV",[editor]);
const ctx={Element,Node:{TEXT_NODE:3},Date,setTimeout,TextEncoder,
  getComputedStyle:()=>({display:"block",visibility:"visible",whiteSpace:"pre"}),
  chrome:{runtime:{sendMessage:async()=>({ok:true,committed:true})}},
};
runInNewContext(code,ctx);
const active={requestId:"req_editor_code_restored",submitStartedAt:Date.now(),
  lastCandidateNode:root};
const snapshot=ctx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot(active);
assert.ok(snapshot);
assert.equal(active.nativeCodeEditorCaptured,1);
const rendered=snapshot.root.children[0];
assert.equal(rendered.tag,"pre");
assert.equal(rendered.children[0].tag,"code");
assert.equal(rendered.children[0].children[0].text,lines.join("\n"));
assert.ok(!JSON.stringify(snapshot).includes("<script"));
const virtualized=new Element("DIV",[]);
virtualized.matches=editor.matches;
virtualized.querySelectorAll=s=>s===".cm-content .cm-line"
  ? [] : s===".view-lines .view-line" ? [{textContent:"line1"}] : [];
virtualized.querySelector=s=>s==="[aria-rowcount]"
  ? {getAttribute:()=>String(500)} : null;
const partial={requestId:"req_monaco_incomplete",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[virtualized])};
assert.equal(ctx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot(partial),null);
assert.equal(partial.nativeStructuralFailure,"code-editor-virtualized");
const cmGap=new Element("DIV",[]);
cmGap.matches=editor.matches;
cmGap.querySelectorAll=editor.querySelectorAll;
cmGap.querySelector=selector=>selector===".cm-gap"?{}:null;
const skippedCm={requestId:"req_cm_gap_incomplete",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[cmGap])};
assert.equal(ctx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot(skippedCm),null);
assert.equal(skippedCm.nativeStructuralFailure,"code-editor-virtualized");
console.log("Verified editor code lines included; virtualized code rejected safely");
