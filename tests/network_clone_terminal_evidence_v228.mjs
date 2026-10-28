import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {TextDecoder, TextEncoder} from "node:util";

const isolated = readFileSync("chrome_extension/content_network_stream_recovery_v55.js","utf8");
const main = readFileSync("chrome_extension/network_stream_main_v55.js","utf8");
const tick = () => new Promise(resolve=>setTimeout(resolve,0));

async function isolatedSession(){
  const frames=[], handlers=[];
  const active={requestId:"req_eof_228",cancelled:false,
    networkTerminalTransportErrorAt:0,networkStreamEndedWithoutTerminalAt:0};
  const ctx={
    Date,Number,Map,Math,String,globalThis:null,
    __CHAT2API_REQUEST_CONTENT_V6__:{active},
    chrome:{runtime:{sendMessage:async item=>(frames.push(item.event),{ok:true})}},
    addEventListener:(event,callback)=>{if(event==="message")handlers.push(callback)},
  };
  ctx.globalThis=ctx;
  vm.runInNewContext(isolated,ctx);
  function send(stream_id,phase,other={}){
    handlers[0]({source:ctx,data:{source:"chat2api-network-stream-v55",
      stream_id,phase,...other}});
  }
  send("stream-old","response",{event_stream:true,status:200});
  send("stream-old","assistant-snapshot",{text:"first old answer",sequence:1,parser_source:"json-patch"});
  await tick();
  send("stream-new","response",{event_stream:true,status:200});
  send("stream-new","assistant-snapshot",{text:"verified current answer",sequence:1,parser_source:"json-patch"});
  await tick();
  // Explicit proof was verified by the parser at EOF; isolated observer
  // receives the confirmation only in the final done frame.
  send("stream-new","done",{completion_hint:true,sequence:3,chunks:2,bytes:123});
  for(let i=0;i<8;i++)await tick();
  assert.equal(active.networkTerminalText,"verified current answer");
  assert.equal(active.networkTerminalTransportStreamId,"stream-new");
  assert.ok(active.networkTerminalTransportDoneAt>0);
  assert.equal(active.networkTerminalTransportErrorAt,0,
    "EOF verified terminal proof cannot be ordered after an error decision");
  assert.equal(active.networkStreamEndedWithoutTerminalAt,0);
  // Old stream errors must not overwrite newer successfully verified output.
  send("stream-old","error",{error:"signal is aborted without reason",error_name:"AbortError",
    failure_stage:"read",signal_aborted:true});
  for(let i=0;i<8;i++)await tick();
  assert.equal(active.networkTerminalTransportErrorAt,0);
  assert.equal(active.networkTerminalTransportStreamId,"stream-new");
  assert.equal(active.networkStreamEndedWithoutTerminalAt,0,
    "stale stream errors cannot invalidate a later verified EOF");
  assert.ok(frames.some(f=>f?.diagnostics?.network_stream_failure_stage==="read"));
  assert.ok(frames.some(f=>f?.diagnostics?.network_stream_error_name==="AbortError"));
  assert.ok(frames.some(f=>f?.diagnostics?.network_stream_abort_signal===true));
  const finalDiag=frames.find(f=>f?.diagnostics?.network_stream_phase==="done-with-verified-eof-proof");
  assert.ok(finalDiag);
  assert.equal(finalDiag.diagnostics.generating_observed,false);
  assert.equal(frames.filter(f=>["chat.completed","chat.error","chat.snapshot","chat.delta"].includes(f?.type)).length,0);

  send("unverified","response",{event_stream:true,status:200});
  send("unverified","assistant-snapshot",{text:"partial answer",sequence:1});
  await tick();
  send("unverified","done",{completion_hint:false,chunks:1,bytes:10});
  for(let i=0;i<8;i++)await tick();
  assert.ok(active.networkTerminalTransportErrorAt>0,
    "bare EOF without assistant marker cannot falsely complete");
}

async function mainWorldSession(kind){
  const posts=[], encoder = new TextEncoder();
  const signal={aborted:kind==="clone"};
  const reader={async read(){const err=new Error("signal is aborted without reason");
      err.name="AbortError";throw err;},releaseLock(){}};
  const response={ok:true,status:200,body:{locked:false},bodyUsed:false,
    headers:{get:()=> "text/event-stream"},
    clone(){
      if(kind==="clone") {const e=new Error("body already used");e.name="TypeError";throw e;}
      if(kind==="get-reader")return {body:{getReader(){throw new Error("reader locked")}}};
      return {body:{getReader:()=>reader}};
    }};
  const ctx={TextDecoder,TextEncoder,structuredClone,URL,Date,Math,setTimeout,
    location:{href:"https://chatgpt.com/"},document:{documentElement:{setAttribute(){}}},
    postMessage:m=>posts.push(m),fetch:async()=>response};
  ctx.globalThis=ctx;
  vm.runInNewContext(main,ctx);
  await ctx.fetch("https://chatgpt.com/backend-api/f/conversation",{method:"POST",signal});
  for(let i=0;i<8;i++)await tick();
  const error=posts.find(f=>f.phase==="error");
  assert.ok(error,kind+" must have an explicit diagnostic");
  assert.equal(error.failure_stage,kind==="get-reader"?"get-reader":kind==="clone"?"clone":"read");
  assert.equal(error.signal_aborted,kind==="clone");
  assert.equal(error.error_name,kind==="clone"?"TypeError":kind==="get-reader"?"Error":"AbortError");
  assert.ok(!posts.some(f=>f.phase==="assistant-complete"));
  assert.ok(!posts.some(f=>f.phase==="done"));
  assert.equal(encoder.encode("ok").length,2);
}

await isolatedSession();
for(const kind of ["clone","get-reader","read"])await mainWorldSession(kind);
console.log("v228 terminal-at-EOF ordering, stale stream isolation and clone error diagnostics passed");