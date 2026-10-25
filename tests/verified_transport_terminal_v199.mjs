import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext,createContext,runInContext} from "node:vm";
import {test} from "node:test";

const ownerSource=readFileSync(new URL("../chrome_extension/content_request_v6.js", import.meta.url),"utf8");
const observerSource=readFileSync(new URL("../chrome_extension/content_network_stream_recovery_v55.js",import.meta.url),"utf8");
const helperStart=ownerSource.indexOf("  function confirmedTransportTerminal(active) {");
const helperEnd=ownerSource.indexOf("  async function reconcileResponsesTerminal",helperStart);
assert.ok(helperStart>=0&&helperEnd>helperStart,"request-v6 has single transport terminal gate");
const confirmedTransportTerminal=runInNewContext(ownerSource.slice(helperStart,helperEnd)+"\nconfirmedTransportTerminal");
assert.equal((ownerSource.match(/&& confirmedTransportTerminal\(active\)/g)||[]).length,2,
  "both SSE completion and rendered DOM fallback require the same clean transport proof");
assert.match(ownerSource,/network_terminal_transport: "clean-same-stream-eof-v199"/);

const flush=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
function observer(){
  let onMessage=null;
  const events=[];
  const active={requestId:"req_test_v199",cancelled:false,options:{response_protocol:"chat-completions"},
    networkTerminalAt:0,networkTerminalTransportDoneAt:0,
    networkTerminalTransportErrorAt:0,networkTerminalStreamId:""};
  const ctx={
    console, Date,
    chrome:{runtime:{sendMessage:async msg=>{events.push(msg.event);return {ok:true};}}},
    addEventListener:(name,cb)=>{if(name==="message")onMessage=cb;},
  };
  ctx.globalThis=ctx;
  ctx.__CHAT2API_REQUEST_CONTENT_V6__={active};
  const context=createContext(ctx);
  runInContext(observerSource,context);
  // vm's contextified globalThis is not strictly equal to the host sandbox
  // object. Browser window postMessage uses the contextified source.
  const sourceWindow=runInContext("globalThis",context);
  async function push(phase,more={}){
    onMessage({source:sourceWindow,data:{source:"chat2api-network-stream-v55",
      stream_id:"stream-1",phase,...more}});
    await flush();
  }
  return {active,events,push};
}
async function begin(row){
  await row.push("response",{event_stream:true,status:200});
  await row.push("assistant-snapshot",{text:"## 山间骑行驿站\n##",parser_source:"json-patch",sequence:3});
  await row.push("assistant-complete",{text:"## 山间骑行驿站\n##",
    terminal_evidence_source:"sse-protocol-complete",completion_hint:true,sequence:4});
  assert.ok(row.active.networkTerminalAt>0,"early protocol marker remains provisional");
  assert.equal(confirmedTransportTerminal(row.active),false);
}
test("TX03 603KB/926-chunk abort after provisional protocol-complete NEVER certifies success",async()=>{
  const row=observer();
  await begin(row);
  await row.push("chunk",{chunks:926,bytes:603413});
  await row.push("error",{chunks:926,bytes:603413,error:"signal is aborted without reason"});
  assert.equal(row.active.networkTerminalTransportDoneAt,0);
  assert.ok(row.active.networkTerminalTransportErrorAt>0);
  assert.equal(confirmedTransportTerminal(row.active),false);
  assert.equal(row.active.networkTerminalTransportErrorReason,"signal is aborted without reason");
  assert.equal(row.events.filter(x=>x.type==="chat.completed").length,0,"observer has no terminal ownership");
});
test("clean same-stream EOF after assistant terminal enables request-v6 only",async()=>{
  const row=observer();
  await begin(row);
  await row.push("done",{completion_hint:true,chunks:5,bytes:1800});
  assert.ok(row.active.networkTerminalTransportDoneAt>=row.active.networkTerminalAt);
  assert.equal(row.active.networkTerminalTransportStreamId,"stream-1");
  assert.equal(confirmedTransportTerminal(row.active),true);
  assert.equal(row.events.filter(x=>x.type==="chat.completed").length,0);
});
test("transport EOF lacking assistant terminal cannot promote partial text",async()=>{
  const row=observer();
  await row.push("response",{event_stream:true,status:200});
  await row.push("assistant-snapshot",{text:"partial",parser_source:"json-patch",sequence:2});
  await row.push("done",{completion_hint:false,chunks:3,bytes:600});
  assert.ok(row.active.networkTerminalTransportErrorAt>0);
  assert.equal(confirmedTransportTerminal(row.active),false);
});
test("stale or different stream cannot validate terminal and error revokes proof",()=>{
  const active={networkTerminalAt:100,networkTerminalTransportDoneAt:101,
    networkTerminalStreamId:"stream-A",networkTerminalTransportStreamId:"stream-B",
    networkTerminalTransportErrorAt:0};
  assert.equal(confirmedTransportTerminal(active),false);
  active.networkTerminalTransportStreamId="stream-A";
  assert.equal(confirmedTransportTerminal(active),true);
  active.networkTerminalTransportErrorAt=102;
  assert.equal(confirmedTransportTerminal(active),false);
});
