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

console.log("GPTWork served-vs-default model evidence port: four Chrome Bridge integration scenarios passed");
