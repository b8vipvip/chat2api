import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const networkSource = fs.readFileSync(new URL("../chrome_extension/content_network_stream_recovery_v55.js", import.meta.url), "utf8");
const requestSource = fs.readFileSync(new URL("../chrome_extension/content_request_v6.js", import.meta.url), "utf8");
const listeners = [];
const sent = [];
const active = { requestId: "req_sse_127", cancelled: false };
const context = {
  chrome: {runtime: {
    sendMessage: async message => { sent.push(message); return {ok:true}; },
    onMessage: {addListener(){},removeListener(){}},
  }},
  addEventListener: (type, callback) => { if(type === "message") listeners.push(callback); },
  console, setTimeout, clearTimeout, Date,
};
context.globalThis = context;
context.__CHAT2API_REQUEST_CONTENT_V6__ = {active};
vm.createContext(context);
vm.runInContext(networkSource, context);
const fire = async d => {
  listeners[0]({source:context,data:{source:"chat2api-network-stream-v55",stream_id:"test-stream",...d}});
  for(let i=0;i<6;i++) await tick();
};
await fire({phase:"response",status:200,event_stream:true});
await fire({phase:"assistant-snapshot",sequence:1,parser_source:"assistant-message",text:"[\"Old partial rendering\", \"stale\"]"});
assert.equal(active.networkObservedText,'["Old partial rendering", "stale"]');
// Same-stream authoritative patch replaces the draft instead of appending it.
const corrected = JSON.stringify(Array.from({length:10}, (_,i)=>"完整回复"+i));
await fire({phase:"assistant-snapshot",sequence:2,parser_source:"json-patch",text:corrected});
assert.equal(active.networkObservedText,corrected);
await fire({phase:"assistant-snapshot",sequence:1,parser_source:"json-patch",text:"STALE"});
assert.equal(active.networkObservedText,corrected,"late out-of-order events must not roll back a newer snapshot");
await fire({phase:"assistant-snapshot",sequence:3,parser_source:"assistant-message",text:"UNRELATED"});
assert.equal(active.networkObservedText,corrected,"non-authoritative candidate must not overwrite the assistant answer");
await fire({phase:"assistant-complete",sequence:4,completion_hint:true,terminal_evidence_source:"assistant-end-turn-patch",text:corrected});
assert.equal(active.networkTerminalText,corrected);
assert.equal(active.networkTerminalSequence,4);
assert.equal(active.networkTerminalEvidenceSource,"assistant-end-turn-patch");
// A later, verified patch after an early assistant-end-turn must be kept,
// and the newest text must replace (not append to) the terminal payload.
const lateComplete = JSON.stringify(Array.from({length:10}, (_,i)=>"最终版本"+i));
await fire({phase:"assistant-snapshot",sequence:5,parser_source:"json-patch",text:lateComplete});
assert.equal(active.networkObservedText,lateComplete);
assert.equal(active.networkTerminalText,lateComplete);
assert.equal(active.networkTerminalSequence,5);
await fire({phase:"done",sequence:6,chunks:9,bytes:7000,completion_hint:true});
assert.equal(active.networkTerminalText,lateComplete);
assert.ok(sent.some(row=>row.event?.diagnostics?.network_stream_phase === "done-after-terminal"));

context.__CHAT2API_REQUEST_CONTENT_V6__ = undefined;
// request-v6 registers its contract only once and does not touch the DOM until dispatch.
vm.runInContext(requestSource, context);
const contract = context.__CHAT2API_REQUEST_CONTENT_V6__?.contract;
const tenPrompt = "输出严格JSON数组，恰好10个字符串；占位符不能改动。";
assert.equal(contract.requiresStrictJsonTen(tenPrompt),true);
assert.equal(contract.validJsonTen(corrected),true);
assert.equal(contract.validJsonTen("[\n确认是否支持使用。\"\n]"),false);
assert.equal(contract.validJsonTen(JSON.stringify(["only one"])),false);
assert.equal(contract.requiresStrictJsonTen("普通聊天"),false);
console.log("Authoritative SSE rewrite, stale snapshot protection, and strict ten-string validation passed");
