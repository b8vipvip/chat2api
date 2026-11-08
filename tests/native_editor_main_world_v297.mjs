import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/background_native_editor_state_v297.js","utf8");
let listener,executions=0,docValue="import math\n\ndef check(value):\n    return math.isfinite(value)";
const cmDoc={
  get length(){return docValue.length;},
  sliceString(start,end){return docValue.slice(start,end);},
};
let editorPresent=true, editorAttachment="content";
const editor={
  isConnected:true,
  matches:selector=>selector===".cm-editor",
  closest:selector=>selector.includes("main") ? {} : null,
  querySelector:selector=>selector===".cm-content"
    ? editorAttachment==="content" ? {cmView:{view:{state:{doc:cmDoc}}}} : {}
    : selector===".cm-scroller" && editorAttachment==="scroller"
      ? {cmView:{rootView:{view:{state:{doc:cmDoc}}}}} : null,
};
const page={
  querySelector:selector=>selector.includes("data-chat2api-native-code-probe-v297") &&
    editorPresent ? editor : null,
};
const chrome={runtime:{onMessage:{addListener(fn){listener=fn;}}},
  scripting:{executeScript:async({target,world,func,args})=>{
    executions++;
    assert.equal(target.tabId,42);
    assert.equal(world,"MAIN");
    assert.equal(args[0].length,32);
    return [{result:func(args[0])}];
  }},
};
const ctx={chrome,URL,document:page};
runInNewContext(source,ctx);
assert.equal(ctx.__CHAT2API_NATIVE_EDITOR_STATE_V297__.version,297);
const trusted={tab:{id:42,url:"https://chatgpt.com/c/abc"},frameId:0};
const request={type:"chat2api.native.editor.read-v297",
  request_id:"req_"+"a".repeat(32),probe:"1".repeat(32)};
const call=(msg=request,sender=trusted)=>new Promise(resolve=>{
  const handled=listener(msg,sender,resolve);
  assert.equal(handled,true);
});
const value=await call();
assert.equal(value.ok,true);
assert.equal(value.source,"codemirror-state-v297");
assert.equal(value.text,docValue);
assert.equal(value.length,docValue.length);
assert.equal(executions,1);
// Regression: the 2026-11-08 real request had editor-document-size because
// the exact .cm-content.cmView.view attachment was not always populated.
editorAttachment="scroller";
const reattached=await call();
assert.equal(reattached.ok,true);
assert.equal(reattached.text,docValue);
editorAttachment="absent";
const missing=await call();
assert.equal(missing.ok,false);
assert.equal(missing.reason,"editor-view-not-found");
editorAttachment="content";
const unauthorized=await call(request,{tab:{id:42,url:"https://evil.example/"},frameId:0});
assert.equal(unauthorized.ok,false);
assert.equal(executions,3);
const subframe=await call(request,{...trusted,frameId:1});
assert.equal(subframe.ok,false);
assert.equal(executions,3);
docValue="x".repeat(150001);
const oversized=await call();
assert.equal(oversized.ok,false);
assert.equal(oversized.reason,"editor-document-too-large");
docValue="a\n";editorPresent=false;
const notFound=await call();
assert.equal(notFound.ok,false);
console.log("v297 MAIN-world full CodeMirror document; bounded, tab-scoped and read-only");
