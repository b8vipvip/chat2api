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

const pageAlias=await capture([
  {served_model_slug:"auto",default_model_slug:"default"},
]);
assert.equal(pageAlias.served_model,null,
  "page-routing aliases cannot prove that a concrete upstream model served");
assert.equal(pageAlias.default_model,null);

// Thousands of SSE blocks must be accumulated in linear work and bounded
// memory. The former adapter re-parsed every prior object on each new frame
// (quadratic work), slowing long ChatGPT requests that had no new model fact.
const longStream=Array.from({length:4000},(_,i)=>
  i===0?{served_model_slug:"gpt-6-astra-wm"}
  :i===3999?{resolved_model_slug:"gpt-5.6-sol-wm"}
  :{event:"stream-progress",percent:i%100});
const longConflict=await capture(longStream);
assert.equal(longConflict.served_model,null);
assert.equal(longConflict.model_conflict,true);
assert.equal(longConflict.model_field,null);

// The page-world stream parser is only the first half of the model evidence
// contract. Verify the isolated Worker bridge can clear previously published
// served/default/reasoning facts after an authoritative later conflict.
const isolatedCode=fs.readFileSync(
  new URL("../chrome_extension/content_model_evidence_v144.js",import.meta.url),"utf8");
const isolatedEmits=[], messageListeners=[], bridgeRequests=[], authorityBroadcasts=[];
const storedEvidence=[];
let stallNextEvidenceWrite=false,releaseStalledEvidenceWrite=null;
const isolatedContext={
  Date, setTimeout, clearTimeout, postMessage(message){authorityBroadcasts.push(message);},
  chrome:{
    runtime:{
      onMessage:{addListener(fn){bridgeRequests.push(fn)}},
      sendMessage:async msg=>{isolatedEmits.push(msg);return {ok:true}},
    },
    storage:{
      local:{get:async()=>({}),set:async()=>{}},
      session:{set:async data=>{
        const record=Object.values(data)[0];
        if(stallNextEvidenceWrite && record.served_model==="gpt-6"
          && record.model_conflict!==true) {
          stallNextEvidenceWrite=false;
          await new Promise(resolve=>{releaseStalledEvidenceWrite=resolve});
        }
        storedEvidence.push(record);
      }},
    },
  },
  addEventListener(name,handler){if(name==="message")messageListeners.push(handler)},
};
// Chromium requires event.source === window. Node's vm contextified
// global object is a distinct identity from the raw sandbox argument.
vm.createContext(isolatedContext);
vm.runInContext(isolatedCode,isolatedContext);
const bridgeWindow=vm.runInContext("globalThis",isolatedContext);
assert.equal(messageListeners.length,1);
assert.equal(bridgeRequests.length,1);
const issueRequest=(requestId,model="gpt-6")=>bridgeRequests[0]({
  type:"chat2api.request",requestId,
  options:{model_authority:{requested_model:model,routed_model:model,
    transport_model:model}},
});
const dispatch=(stream_id,phase,extra={})=>messageListeners[0]({
  source:bridgeWindow,
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
await takeLast(); // Drain the initial request-only diagnostic first.
const beforeError=isolatedEmits.length;
dispatch("stream-3","response",{ok:false,status:403,
  header_model:"gpt-6-astra-wm"});
await takeLast();
assert.equal(isolatedEmits.length,beforeError,
  "failed HTTP responses must not create served-model proof");
// Deliberately delay an earlier model proof's session storage write. The
// network parser may publish a conflict while this async write is pending.
// The *last* stored and emitted state must be the latest conflict, never A.
issueRequest("req_asyncconflict123");
dispatch("stream-race","request",{request_model:"gpt-6",
  request_wire_model:"gpt-6"});
await takeLast();
stallNextEvidenceWrite=true;
dispatch("stream-race","evidence",{served_model:"gpt-6",
  model_field:"served_model_slug",
  evidence_source:"network_response_metadata"});
for(let i=0;i<10&&!releaseStalledEvidenceWrite;i++)
  await new Promise(resolve=>setImmediate(resolve));
assert.equal(typeof releaseStalledEvidenceWrite,"function",
  "first model storage write must be held in-flight");
dispatch("stream-race","evidence",{served_model:null,
  model_conflict:true,model_field:null,
  evidence_source:"network_response_metadata"});
releaseStalledEvidenceWrite();
bridged=await takeLast();
assert.equal(bridged.served_model,null,
  "older async model proof cannot overwrite a later conflict");
assert.equal(bridged.model_conflict,true);

  assert.equal(storedEvidence.at(-1).model_conflict,true);
  
// A request's model authority must not leak into the next native ChatGPT
// conversation. The exact stream's completion clears only its own active
// request; a late older stream must not clear a newer request's authority.
issueRequest("req_terminal_clear123");
dispatch("stream-terminal","request",{request_model:"gpt-6",
  request_wire_model:"gpt-6"});
await takeLast();
const beforeTerminalBroadcasts=authorityBroadcasts.length;
dispatch("stream-terminal","done");
await takeLast();
assert.equal(authorityBroadcasts.length,beforeTerminalBroadcasts,
  "SSE EOF alone must not clear authority before request-v6 finishes tool turns");
messageListeners[0]({
  source:bridgeWindow,
  data:{source:"chat2api-model-authority-v144",
    request_id:"req_terminal_clear123",authority:null,terminal:true},
});
const clearBroadcast=authorityBroadcasts.slice(beforeTerminalBroadcasts)
  .find(message=>message.source==="chat2api-model-authority-v144"
    && message.authority===null);
assert.ok(clearBroadcast,"request-v6 terminal must broadcast cleared model authority");
assert.equal(clearBroadcast.request_id,"req_terminal_clear123");
assert.equal(clearBroadcast.terminal,true);

// If an older stream ends after a newer request has started, it must not
// clear the newer request's model authority. Only a matching request-v6
// terminal can release the active authority.
issueRequest("req_old_stream123");
dispatch("stream-old","request",{request_model:"gpt-6"});
await takeLast();
issueRequest("req_new_stream123");
dispatch("stream-new","request",{request_model:"gpt-5.6-sol"});
await takeLast();
const beforeOldDone=authorityBroadcasts.length;
dispatch("stream-old","done");
await takeLast();
messageListeners[0]({
  source:bridgeWindow,
  data:{source:"chat2api-model-authority-v144",
    request_id:"req_old_stream123",authority:null,terminal:true},
});
assert.equal(authorityBroadcasts.length,beforeOldDone,
  "late terminal from an older request must not clear newer authority");
messageListeners[0]({
  source:bridgeWindow,
  data:{source:"chat2api-model-authority-v144",
    request_id:"req_new_stream123",authority:null,terminal:true},
});
assert.equal(authorityBroadcasts.length,beforeOldDone+1,
  "matching request-v6 terminal must clear its own authority");

// A new request with no explicit model authority must explicitly clear the
// prior main-world rule rather than inherit the previous request's model.
bridgeRequests[0]({type:"chat2api.request",requestId:"req_unscoped123"});
const unscopedBroadcast=authorityBroadcasts.at(-1);
assert.equal(unscopedBroadcast.source,"chat2api-model-authority-v144");
assert.equal(unscopedBroadcast.request_id,"req_unscoped123");
assert.equal(unscopedBroadcast.authority,null);

// Feed the actual clear message emitted by the isolated bridge into the
// MAIN-world observer. Its next native conversation request must remain
// untouched instead of being rewritten with the previous API model.
async function verifyMainWorldAuthorityClear(clearMessage) {
  const emitted=[], listeners=[], sentBodies=[];
  const mainContext={
    URL, Request, TextDecoder, Response,
    location:{href:"https://chatgpt.com/"},
    document:{documentElement:{setAttribute(){}}},
    fetch:async(_input,init)=>{
      sentBodies.push(JSON.parse(String(init?.body||"{}")));
      return new Response("data: [DONE]\n\n",{status:200});
    },
    postMessage(message){emitted.push(message);},
    addEventListener(name,handler){if(name==="message")listeners.push(handler);},
    globalThis:null,
  };
  mainContext.globalThis=mainContext;
  vm.runInNewContext(code,mainContext);
  assert.equal(listeners.length,1);
  const deliver=message=>listeners[0]({
    source:mainContext,
    data:{source:"chat2api-model-authority-v144",...message},
  });
  deliver({
    request_id:"req_terminal_clear123",
    authority:{requested_model:"gpt-5.6-sol",routed_model:"gpt-5.6-sol",
      transport_model:"gpt-5.6-sol-wm"},
  });
  await mainContext.fetch("https://chatgpt.com/backend-api/conversation",{
    method:"POST",body:JSON.stringify({model:"gpt-6"}),
  });
  for(let i=0;i<12&&!emitted.some(e=>e.phase==="done");i++)
    await new Promise(resolve=>setImmediate(resolve));
  assert.equal(sentBodies[0].model,"gpt-5.6-sol-wm",
    "active request authority should rewrite its own conversation request");
  // A stale terminal from a prior request must not clear the current model.
  deliver({request_id:"req_older_request123",authority:null,terminal:true});
  await mainContext.fetch("https://chatgpt.com/backend-api/conversation",{
    method:"POST",body:JSON.stringify({model:"gpt-6"}),
  });
  assert.equal(sentBodies[1].model,"gpt-5.6-sol-wm",
    "an older request's terminal must not clear the newer request's authority");
  deliver(clearMessage);
  await mainContext.fetch("https://chatgpt.com/backend-api/conversation",{
    method:"POST",body:JSON.stringify({model:"gpt-6"}),
  });
  assert.equal(sentBodies[2].model,"gpt-6",
    "after the matching request terminal, later native ChatGPT requests must not inherit its model");
}
await verifyMainWorldAuthorityClear(clearBroadcast);
console.log("Worker isolated bridge: terminal request releases only its own model authority");
console.log("Main-world observer: completed API request does not rewrite later native ChatGPT conversations");

console.log("Worker isolated bridge: stale async storage/publish cannot overwrite model conflict");

console.log("Worker isolated bridge: conflicting evidence clears old model; header-only proof is status-bound");

console.log("GPTWork served-vs-default model evidence port: four Chrome Bridge integration scenarios passed");
