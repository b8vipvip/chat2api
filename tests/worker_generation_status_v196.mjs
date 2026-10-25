import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const source = fs.readFileSync(new URL("../chrome_extension/content_generation_liveness_v49.js", import.meta.url), "utf8");
let mainText="正在分析";
let stage="working";
let emitted=[];
const fakeNode=text=>({
  innerText:text, textContent:text, disabled:false,
  getBoundingClientRect:()=>({width:25,height:20}),
});
const v6={active:{
  requestId:"req_current", submitStartedAt:Date.now()-4000,
  networkStreamSeen:true, promptNormalized:"请帮我审查项目的模块",
}};
const doc = {
  querySelectorAll(selector) {
    if(selector.includes("reasoning-summary")) return stage==="summary" ? [fakeNode("正在梳理模块间依赖")] : [];
    if(selector.includes("thinking-summary")) return [];
    if(selector.includes("thinking")) return stage==="working" ? [fakeNode(mainText)] : [];
    return [];
  },
};
const sandbox={
  document:doc, Date, console,
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
  chrome:{runtime:{sendMessage:async x=>emitted.push(x)}},
  setInterval:()=>1,
  __CHAT2API_REQUEST_CONTENT_V6__:v6,
  __CHAT2API_REQUEST_CONTENT_V5__:{active:null},
  __CHAT2API_REQUEST_STALL_GUARD_V34__:{track:null},
};
vm.runInNewContext(source,sandbox);
const state = sandbox.__CHAT2API_GENERATION_LIVENESS_V49__;
assert.ok(state);
await state.tick();
assert.equal(emitted.length,1);
assert.equal(emitted[0].event.request_id,"req_current");
assert.equal(emitted[0].event.diagnostics.visible_reasoning_status,"正在分析");
assert.equal(emitted[0].event.diagnostics.visible_reasoning_status_kind,"working");
stage="summary";
state.lastEmitAt=0;
await state.tick();
assert.equal(emitted.length,2);
assert.equal(emitted[1].event.diagnostics.visible_reasoning_status_kind,"reasoning");
assert.equal(emitted[1].event.diagnostics.visible_reasoning_status,"正在梳理模块间依赖");
stage="working"; mainText="这里是用户提示词的长文本";
state.lastEmitAt=0;
await state.tick();
assert.equal(emitted.length,3);
assert.equal(emitted[2].event.diagnostics.visible_reasoning_status,undefined);
v6.active=null;
state.lastEmitAt=0;
await state.tick();
assert.equal(emitted.length,3);
console.log("worker native progress/summary scope validated");
