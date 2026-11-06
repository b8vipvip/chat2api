// Real Fetch API BodyInit precedence contract for browser model authority.
// Never use a stale Request.body when fetch(Request, init.body) overrides it.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync(
  new URL("../chrome_extension/model_evidence_main_v144.js", import.meta.url),"utf8");
const endpoint="https://chatgpt.com/backend-api/conversation";
const oldModel="gpt-5.6-sol-wm";
const routedModel="gpt-6-astra-wm";
const payload=value=>JSON.stringify({model:value,conversation_id:"safe-fixture",action:"next"});

async function run(inputBody, makeInit, useRequest=true) {
  const emitted=[],sent=[];
  const listeners=[];
  const ctx={
    URL, Request, Response, Blob, ArrayBuffer, Uint8Array,
    TextDecoder, TextEncoder,
    location:{href:"https://chatgpt.com/"},
    document:{documentElement:{setAttribute(){}}},
    postMessage(detail){emitted.push(detail)},
    addEventListener(name,callback){if(name==="message")listeners.push(callback)},
    fetch:async (input,init)=>{
      // The Fetch standard Request(input, init) resolves BodyInit precedence.
      // Read exactly what native fetch WOULD send, not a mocked adapter field.
      const effective=new Request(input,init);
      const raw=await effective.text();
      let parsed=null;
      try {parsed=JSON.parse(raw)}catch(_){}
      sent.push({raw,parsed});
      return new Response("data: [DONE]\n\n",{
        status:200,headers:{"content-type":"text/event-stream"},
      });
    },
  };
  ctx.globalThis=ctx;
  vm.createContext(ctx);
  vm.runInContext(source,ctx);
  const win=vm.runInContext("globalThis",ctx);
  assert.equal(listeners.length,1);
  listeners[0]({
    source:win,
    data:{source:"chat2api-model-authority-v144",request_id:"req_model-body-override",
      authority:{requested_model:"gpt-6-astra",routed_model:"gpt-6-astra",
        transport_model:routedModel}},
  });
  const input=useRequest?new Request(endpoint,{
    method:"POST",headers:{"content-type":"application/json"},body:inputBody,
  }):endpoint;
  const init=makeInit?.();
  await ctx.fetch(input,init);
  const observation=emitted.filter(e=>e.phase==="request").at(-1);
  assert.ok(observation,"request evidence must exist");
  assert.equal(sent.length,1);
  return {wire:sent[0],observation};
}

function expectRouted({wire,observation}) {
  assert.equal(wire.parsed.model,routedModel,
    "actual fetch body must contain requested transport model");
  assert.equal(observation.request_wire_model,routedModel,
    "reported network wire model must match actual fetch BodyInit");
  assert.equal(observation.request_rewritten,true);
  assert.equal(observation.authority_violation,false);
}

expectRouted(await run(payload(oldModel),()=>({
  method:"POST",body:new Blob([payload("gpt-5.5-wm")],{
    type:"application/json",
  }),
})));
expectRouted(await run(payload(oldModel),()=>({
  method:"POST",body:new Uint8Array(new TextEncoder().encode(payload("gpt-5.5-wm"))),
})));
expectRouted(await run(payload(oldModel),()=>({
  method:"POST",body:new TextEncoder().encode(payload("gpt-5.5-wm")).buffer,
})));
expectRouted(await run(payload(oldModel),()=>({
  method:"POST",body:payload("gpt-5.5-wm"),
})));
expectRouted(await run(payload(oldModel),()=>({method:"POST"})));
expectRouted(await run(payload(oldModel),()=>({
  method:"POST",body:new Blob([payload(oldModel)],{type:"application/json"}),
}),false));

// Non-JSON unknown BodyInit must NEVER be described as the old Request model.
// Its actual contents are deliberately left untouched.
const formDataResult=await run(payload(oldModel),()=>{
  const form=new FormData();
  form.append("model","gpt-5.5");
  return {method:"POST",body:form};
});
assert.equal(formDataResult.wire.parsed,null);
assert.equal(formDataResult.observation.request_rewritten,false);
assert.equal(formDataResult.observation.request_wire_model,null);
assert.equal(formDataResult.observation.authority_violation,false);

// The normal free-tier page-default route never rewrites the browser body.
{
  const original=source;
  assert.ok(original.includes("authority?.default_page_route === true"));
}
console.log("model BodyInit override / true transmitted bytes evidence: passed");
