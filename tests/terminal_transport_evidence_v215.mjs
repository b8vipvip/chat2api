import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  async function emit(event) {");
const end=source.indexOf("\n  async function diagnostic(",start);
assert.ok(start>0&&end>start);
function harness(results) {
  const messages=[];
  const active={requestId:"req_terminal_v215",mediaCaptureLastInlined:0,mediaCaptureLastEmbedded:0};
  const context={
    state:{active}, globalThis:{__CHAT2API_PROBE_SUPERVISOR_V1__:{capture:async()=>({})}},
    assistantOnlyReplyText:(_,text)=>({text, pending:false,stripped:false}),
    chrome:{runtime:{sendMessage:async payload=>{
      messages.push(payload);
      return {ok:results.shift()??false};
    }}},
    setTimeout,Promise,String,Number,Array,Error,
  };
  vm.runInNewContext(source.slice(start,end)+"\nthis.emit=emit;",context);
  return {emit:context.emit,messages};
}
{
  const h=harness([false,true]);
  await h.emit({type:"chat.completed",request_id:"req_terminal_v215",text:"verified answer"});
  assert.equal(h.messages.length,2);
  assert.equal(h.messages[0].event.diagnostics.terminal_worker_send_attempt,1);
  assert.equal(h.messages[1].event.diagnostics.terminal_worker_send_attempt,2);
}
{
  const h=harness([false,false,false]);
  await assert.rejects(
    h.emit({type:"chat.error",request_id:"req_terminal_v215",error:"request failed"}),
    /not queued by background WebSocket/);
  assert.equal(h.messages.length,3);
}
{
  const h=harness([false]);
  await h.emit({type:"chat.diagnostics",request_id:"req_terminal_v215",diagnostics:{stage:"live"}});
  assert.equal(h.messages.length,1,"read-only nonterminal event does not retry");
}
for (const filename of ["chrome_extension/content_probe_supervisor_v1.js","chrome_extension/content_request_v6.js"]) {
  assert.ok(readFileSync(filename,"utf8").includes("request"));
}
console.log("sole owner terminal delivery proof verified");
