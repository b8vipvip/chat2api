import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/model_routing_v2.js", import.meta.url), "utf8");

async function runCase({account="free", confidence="high", picker=false, requested="gpt-5.6-sol", freeDefaultRoute=true, reasoning=null}={}) {
  const frames=[], outgoing=[];
  const storage={
    models: [], modelValidationState: "failed", modelValidationRevision: 145,
    accountType: account,
    chatgptAccountProfileV20: {
      account_type: account, confidence, model_control_present: picker,
      composer_ready: true, checked_at_ms: Date.now(),
    },
    currentModel: null,
  };
  const ctx={
    handleServerMessage: async()=>{throw new Error("unexpected base handler");},
    discoverModels: async()=>{},
    sendExtensionStatus: async()=>{},
    resolveTargetTab: async()=>({id:4}),
    ensureContent: async()=>{},
    trySendSocket: async msg=>{frames.push(msg);return true;},
    chrome: {
      storage: {local:{
        get: async defaults=>({...defaults,...storage}),
        set: async fields=>{Object.assign(storage,fields);},
      }},
      runtime:{getManifest:()=>({version:"0.22.130"})},
      tabs:{
        sendMessage: async(_tab, msg)=>{
          if(msg.type==="chat2api.request.preflight")return {ok:true,data:{ready:true}};
          if(msg.type==="chat2api.request"){outgoing.push(msg);return{ok:true};}
          if(msg.type==="chat2api.reasoning.prepare.v7") {
            return {ok:false,code:"reasoning_state_untrusted",
              error:"ChatGPT reasoning level could not be verified",
              diagnostics:{verification:{ok:false,code:"reasoning_state_untrusted"}}};
          }
          if(msg.type==="chat2api.model.select.v145"){
            throw new Error("model picker should not be used for unvalidated Free Mini");
          }
          return{ok:true,data:{}};
        },
      },
    },
    globalThis:null,
  };
  ctx.globalThis=ctx;
  vm.runInNewContext(source,ctx);
  let failure=null;
  try {
    await ctx.handleServerMessage({
      type:"chat.request",request_id:"req_test",
      prompt:"ping",options:{model:requested,free_default_route:freeDefaultRoute,...(reasoning?{reasoning_effort:reasoning}:{})},
    });
  } catch(error) { failure=error; }
  return{frames,outgoing,storage,failure};
}

const free=await runCase();
assert.equal(free.outgoing.length,1,"Free composer should receive the request without a picker");
assert.equal(free.outgoing[0].options.model,"chatgpt-web");
assert.equal(free.outgoing[0].options.requested_model,"gpt-5.6-sol");
assert.equal(free.outgoing[0].options.effective_model,null,
  "An unverified default must not be reported as the served model");
assert.equal(free.outgoing[0].options.chat2api_diagnostics.model_selection_validated,false);
assert.equal(free.outgoing[0].options.chat2api_diagnostics.model_library_authority,
  "free-default-compatibility-unverified");
assert.ok(!free.frames.some(x=>x.type==="chat.error"));
const paid=await runCase({account:"paid"});
assert.equal(paid.outgoing.length,0);
assert.ok(paid.failure,"invalid paid mini route throws to the sole dispatch terminal owner");
assert.ok(!paid.frames.some(x=>x.type==="chat.error"));
const noProof=await runCase({confidence:"low"});
assert.equal(noProof.outgoing.length,0);
const withPicker=await runCase({picker:true});
assert.equal(withPicker.outgoing.length,0);
const nonDefault=await runCase({requested:"gpt-5.5",freeDefaultRoute:false});
assert.equal(nonDefault.outgoing.length,0,
  "Free default compatibility cannot bypass verified-model requirements for gpt-5.5");
const noServerRoute=await runCase({freeDefaultRoute:false});
assert.equal(noServerRoute.outgoing.length,0,"No Free default bypass without server model authority");
const newDefault=await runCase({requested:"gpt-6"});
assert.equal(newDefault.outgoing.length,1,"The server-selected default is dynamic, not hardcoded Mini");
const untrustedEffort=await runCase({reasoning:"medium"});
assert.equal(untrustedEffort.outgoing.length,1,"Unrecognized reasoning UI must not block a ready default request");
assert.equal(untrustedEffort.outgoing[0].options.reasoning_effort,undefined);
assert.equal(untrustedEffort.outgoing[0].options.effective_reasoning,null);
assert.equal(untrustedEffort.outgoing[0].options.chat2api_diagnostics.logical_requested_reasoning,"medium");
assert.equal(untrustedEffort.outgoing[0].options.chat2api_diagnostics.reasoning_default_unverified,true);
console.log("Configured Free default: no picker, no fabricated model/effort verification, server-owned eligibility");
