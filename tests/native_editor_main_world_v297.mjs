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
  children:[],shadowRoot:null,
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
assert.equal(missing.attachment_count,0);
assert.equal(missing.surface_found,true);
// Real virtualized code cards may attach the CM6 rootView after the
// previous 180-node scan limit: only the marked editor's descendants
// are eligible, no page-wide search or viewport-line reconstruction.
editorAttachment="late";
editor.children=Array.from({length:260},(_,i)=>({
  children:[],...(i===259?{cmView:{rootView:{view:{state:{doc:cmDoc}}}}}:{}),
}));
const late=await call();
assert.equal(late.ok,true);
assert.equal(late.text,docValue);
assert.ok(late.scan_count>=261, "late attachment must actually be scanned");
assert.equal(late.attachment_count,1);
editor.children=[];
editorAttachment="shadow";
editor.shadowRoot={children:[{
  children:[],cmView:{rootView:{view:{state:{doc:cmDoc}}}},
}]};
const shadow=await call();
assert.equal(shadow.ok,true);
assert.equal(shadow.text,docValue);
editor.shadowRoot=null;
editorAttachment="content";
editor.cmView={view:{state:{doc:{length:4,sliceString:()=>"evil"}}}};
const ambiguous=await call();
assert.equal(ambiguous.ok,false);
assert.equal(ambiguous.reason,"editor-state-ambiguous");
delete editor.cmView;
editorAttachment="content";
const expectedExecutions=executions;
const unauthorized=await call(request,{tab:{id:42,url:"https://evil.example/"},frameId:0});
assert.equal(unauthorized.ok,false);
assert.equal(executions,expectedExecutions);
const subframe=await call(request,{...trusted,frameId:1});
assert.equal(subframe.ok,false);
assert.equal(executions,expectedExecutions);
docValue="x".repeat(150001);
const oversized=await call();
assert.equal(oversized.ok,false);
assert.equal(oversized.reason,"editor-document-too-large");
docValue="a\n";editorPresent=false;
const notFound=await call();
assert.equal(notFound.ok,false);

// v302 production req_dd63b5bd: all 158 editor nodes were searched,
// cmView attachments=0, viewport had 36 lines and an explicit .cm-gap.
// A full source may live only on this SAME card's React component props.
docValue="import math\n\ndef annual_profit(income, expense):\n    result = income - expense\n    if result <= 0: raise ValueError('invalid')\n    return math.floor(result)\n\nprint(annual_profit(100, 50))";
editorPresent=true;
editorAttachment="no-view";
const shown=["import math","def annual_profit(income, expense):",
  "    if result <= 0: raise ValueError('invalid')"];
editor.querySelectorAll=selector=>selector===".cm-content .cm-line"
  ? shown.map(textContent=>({textContent})) : [];
const formerQuery=editor.querySelector;
editor.querySelector=selector=>selector===".cm-gap" ? {} :
  selector===".cm-content" ? {} : formerQuery(selector);
const parent={children:[],parentElement:null,
  matches:()=>false};
parent["__reactFiber$test"]={memoizedProps:{code:docValue},return:null};
editor.parentElement=parent;
const react=await call();
assert.equal(react.ok,true);
assert.equal(react.source,"react-owned-code-v302");
assert.equal(react.text,docValue);
assert.equal(react.length,docValue.length);
assert.equal(react.react_fiber_hosts,1);
assert.equal(react.react_candidate_count,1);
assert.equal(react.react_anchor_count,3);
assert.equal(react.attachment_count,0);
parent["__reactFiber$test"].memoizedProps={
  code:docValue,source:docValue+"\n// contradictory owner text",
};
const conflicting=await call();
assert.equal(conflicting.ok,false);
assert.equal(conflicting.reason,"editor-view-not-found",
  "contradictory React code must never be claimed as full source");
assert.equal(conflicting.react_candidate_count,2);
parent["__reactFiber$test"].memoizedProps={code:docValue};
editor.querySelectorAll=selector=>selector===".cm-content .cm-line"
  ? [{textContent:"import math"}] : [];
const insufficient=await call();
assert.equal(insufficient.ok,false);
assert.equal(insufficient.react_candidate_count,0);
editor.parentElement=null;
editor.querySelectorAll=()=>[];
editor.querySelector=formerQuery;

console.log("v297 MAIN-world full CodeMirror document; bounded, tab-scoped and read-only");
