import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const code=fs.readFileSync(new URL("../chrome_extension/model_evidence_main_v144.js",import.meta.url),"utf8");

async function capture(objects, headers={}) {
  const emitted=[];
  const body=objects.map(value=>"data: "+JSON.stringify(value)+"\n\n").join("")+"data: [DONE]\n\n";
  const ctx={
    URL, Request, TextDecoder,
    Response, location:{href:"https://chatgpt.com/"},
    document:{documentElement:{setAttribute(){}}},
    fetch:async()=>new Response(body,{status:200,headers}),
    postMessage(message){emitted.push(message);},
    addEventListener(){},
    globalThis:null,
  };
  ctx.globalThis=ctx;
  vm.runInNewContext(code,ctx);
  await ctx.fetch("https://chatgpt.com/backend-api/conversation",{
    method:"POST",body:JSON.stringify({model:"gpt-6-astra-wm"}),
  });
  for(let i=0;i<12&&!emitted.some(e=>e.phase==="evidence"&&e.terminal);i++){
    await new Promise(resolve=>setImmediate(resolve));
  }
  const terminal=emitted.filter(e=>e.phase==="evidence"&&e.terminal).at(-1);
  assert.ok(terminal,"terminal network evidence must be emitted");
  return terminal;
}

const onlyDefaults=await capture([
  {message:{metadata:{default_model_slug:"gpt-5.6-sol-wm",model_slug:"gpt-5.6-sol"}}},
  {profile:{default_model_slug:"gpt-6-astra-wm"}},
]);
assert.equal(onlyDefaults.served_model,null);
assert.equal(onlyDefaults.default_model,null);
assert.equal(onlyDefaults.model_conflict,false,
  "conflicting default profiles must not create served-model conflict");
assert.equal(onlyDefaults.default_model_conflict,true);

const explicitServed=await capture([
  {served_model_slug:"gpt-6-astra-wm",default_model_slug:"gpt-5.6-sol-wm"},
  {profile:{default_model_slug:"gpt-6-astra-wm"}},
]);
assert.equal(explicitServed.served_model,"gpt-6-astra");
assert.equal(explicitServed.default_model_conflict,true);
assert.equal(explicitServed.model_conflict,false);

const mismatch=await capture([
  {served_model_slug:"gpt-6-astra-wm"},
  {resolved_model_slug:"gpt-5.6-sol-wm",default_model_slug:"gpt-6-astra-wm"},
]);
assert.equal(mismatch.served_model,null);
assert.equal(mismatch.model_conflict,true);

const headerProof=await capture([
  {default_model_slug:"gpt-5.6-sol-wm"},
  {profile:{default_model_slug:"gpt-6-astra-wm"}},
],{"x-openai-model":"gpt-6-astra-wm"});
assert.equal(headerProof.served_model,"gpt-6-astra");
assert.equal(headerProof.model_conflict,false);
assert.equal(headerProof.default_model_conflict,true);

// The page-world stream parser is only the first half of the model evidence
// contract. Verify the isolated Worker bridge can clear previously published
// served/default/reasoning facts after an authoritative later conflict.
const isolatedCode=fs.readFileSync(
  new URL("../chrome_extension/content_model_evidence_v144.js",import.meta.url),"utf8");
const isolatedEmits=[], messageListeners=[], bridgeRequests=[];
const isolatedContext={
  Date, setTimeout, clearTimeout, postMessage(){},
  chrome:{
    runtime:{
      onMessage:{addListener(fn){bridgeRequests.push(fn)}},
      sendMessage:async msg=>{isolatedEmits.push(msg);return {ok:true}},
    },
    storage:{
      local:{get:async()=>({}),set:async()=>{}},
      session:{set:async()=>{}},
    },
  },
  addEventListener(name,handler){if(name==="message")messageListeners.push(handler)},
};
isolatedContext.globalThis=isolatedContext;
vm.runInNewContext(isolatedCode,isolatedContext);
assert.equal(messageListeners.length,1);
assert.equal(bridgeRequests.length,1);
const issueRequest=(requestId,model="gpt-6")=>bridgeRequests[0]({
  type:"chat2api.request",requestId,
  options:{model_authority:{requested_model:model,routed_model:model,
    transport_model:model}},
});
const dispatch=(stream_id,phase,extra={})=>messageListeners[0]({
  source:isolatedContext,
  data:{source:"chat2api-model-evidence-v144",stream_id,phase,...extra},
});
const takeLast=async()=>{
  for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve));
  return isolatedEmits.filter(msg=>msg.type==="chat2api.event")
    .at(-1)?.event?.diagnostics?.model_evidence_v144;
};
issueRequest("req_modelconflict123");
dispatch("stream-1","request",{request_model:"gpt-6",request_wire_model:"gpt-6"});
dispatch("stream-1","evidence",{
  served_model:"gpt-6",model_field:"resolved_model_slug",
  default_model:"gpt-5.6-sol",default_model_field:"default_model_slug",
  reasoning:"high",reasoning_field:"reasoning_level",
  evidence_source:"network_response_metadata",
});
let bridged=await takeLast();
assert.equal(bridged.served_model,"gpt-6");
assert.equal(bridged.default_model,"gpt-5.6-sol");
assert.equal(bridged.reasoning,"high");
dispatch("stream-1","evidence",{
  served_model:null,default_model:null,reasoning:null,
  model_conflict:true,default_model_conflict:true,reasoning_conflict:true,
  model_field:null,default_model_field:null,reasoning_field:null,
  evidence_source:"network_response_metadata",
});
bridged=await takeLast();
assert.equal(bridged.served_model,null,
  "later network disagreement must clear previously published served model");
assert.equal(bridged.model_conflict,true);
assert.equal(bridged.model_field,null);
assert.equal(bridged.default_model,null);
assert.equal(bridged.default_model_conflict,true);
assert.equal(bridged.default_model_field,null);
assert.equal(bridged.reasoning,null);
assert.equal(bridged.reasoning_conflict,true);
assert.equal(bridged.reasoning_field,null);
dispatch("stream-1","evidence",{
  served_model:"gpt-6",default_model:"gpt-5.6-sol",
  reasoning:"high",model_conflict:false,
  default_model_conflict:false,reasoning_conflict:false,
});
bridged=await takeLast();
assert.equal(bridged.served_model,null,
  "conflicted stream may never silently resurrect an earlier served model");
assert.equal(bridged.default_model,null);
assert.equal(bridged.reasoning,null);

// The page-world probe emits header-only response metadata before reading
// streaming bytes. The bridge must not ignore it (but errors are no proof).
issueRequest("req_headeronly123");
dispatch("stream-2","request",{request_model:"gpt-6",
  request_wire_model:"gpt-6"});
dispatch("stream-2","response",{ok:true,status:200,
  header_model:"gpt-6-astra-wm"});
bridged=await takeLast();
assert.equal(bridged.served_model,"gpt-6-astra");
assert.equal(bridged.model_field,"response-header");
assert.equal(bridged.evidence_source,"network_response_header");
dispatch("stream-2","evidence",{
  served_model:null,model_conflict:true,model_field:null,
  evidence_source:"network_response_metadata",
});
bridged=await takeLast();
assert.equal(bridged.served_model,null);
assert.equal(bridged.model_conflict,true);

issueRequest("req_headererror123");
dispatch("stream-3","request",{request_model:"gpt-6"});
const beforeError=isolatedEmits.length;
dispatch("stream-3","response",{ok:false,status:403,
  header_model:"gpt-6-astra-wm"});
await takeLast();
assert.equal(isolatedEmits.length,beforeError,
  "failed HTTP responses must not create served-model proof");
console.log("Worker isolated bridge: conflicting evidence clears old model; header-only proof is status-bound");

console.log("GPTWork served-vs-default model evidence port: four Chrome Bridge integration scenarios passed");
