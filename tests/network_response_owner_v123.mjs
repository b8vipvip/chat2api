import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const src=fs.readFileSync(new URL("../chrome_extension/content_network_stream_recovery_v55.js",import.meta.url),"utf8");
const outbound=[];
const handlers=[];
const v5Active={requestId:"req-demo",cancelled:false,submitStartedAt:5};
const v6Active={requestId:"req-demo",cancelled:false,submitStartedAt:5};
const v5={active:v5Active};
const v6={active:v6Active,listener:()=>{}};
const ctx={
  Date,Number,String,Boolean,Map,Math,Promise,
  chrome:{runtime:{sendMessage:async m=>{outbound.push(m);return{ok:true};}}},
  addEventListener:(name,fn)=>{if(name==="message") handlers.push(fn);},
  __CHAT2API_REQUEST_CONTENT_V5__:v5,
  __CHAT2API_REQUEST_CONTENT_V6__:v6
};
ctx.globalThis=ctx;
vm.createContext(ctx);
vm.runInContext(src,ctx);
const page=vm.runInContext("globalThis",ctx);
function push(detail){handlers[0]({source:page,data:{source:"chat2api-network-stream-v55",...detail}});}
const tick=()=>new Promise(r=>setTimeout(r,0));
push({phase:"response",stream_id:"s1",status:200,event_stream:true});
for(let i=0;i<4;i++)await tick();
push({phase:"assistant-snapshot",stream_id:"s1",text:"真正回复",sequence:1});
for(let i=0;i<4;i++)await tick();
push({phase:"assistant-complete",stream_id:"s1",text:"真正回复",sequence:2,completion_hint:true,terminal_evidence_source:"sse-protocol-complete"});
for(let i=0;i<8;i++)await tick();
assert.equal(v6Active.networkObservedText,"真正回复","v6 receives correlated network snapshot");
assert.ok(outbound.some(x=>x.event?.diagnostics?.network_evidence_active_owner==="request-v6"),
  "diagnostic explicitly identifies canonical network response owner");
assert.ok(outbound.some(x=>x.event?.diagnostics?.network_legacy_active_conflict===true),
  "legacy/v6 object collisions remain visible for field diagnostics");
assert.equal(v6Active.networkTerminalText,"真正回复","v6 receives network terminal");
assert.equal(v5Active.networkObservedText,undefined,"stale v5 active never owns evidence");
assert.equal(v5Active.networkTerminalText,undefined,"stale v5 active never owns terminal");
assert.equal(outbound.filter(x=>x.event?.type==="chat.completed").length,0,"observer never completes response itself");
v6.active=null;
push({phase:"response",stream_id:"s2",status:200,event_stream:true});
push({phase:"assistant-snapshot",stream_id:"s2",text:"stale reply",sequence:1});
for(let i=0;i<8;i++)await tick();
assert.equal(v5Active.networkObservedText,undefined,"v5 must not recover ownership once v6 exists");
assert.ok(!outbound.some(x=>x.event?.text==="stale reply"),"inactive v6 cannot leak output");
console.log("v6 is unique network evidence owner across stale v5 collisions");
