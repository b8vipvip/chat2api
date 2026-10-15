import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const model = fs.readFileSync(new URL("../chrome_extension/model_routing_v2.js", import.meta.url), "utf8");
const dispatch = fs.readFileSync(new URL("../chrome_extension/conversation_dispatch.js", import.meta.url), "utf8");

const eventLog = [];
const submitted = [];
let retireCount = 0;
const current = {
  models: [],
  modelObservedChoices: [{id: "gpt-5.6-sol", selectable:true}],
  modelValidationState:"discovered",
  modelValidationRevision:145,
};
const chrome = {
  storage: {local:{
    get: async defaults => ({...defaults,...current}),
    set: async patch => Object.assign(current,patch),
  }},
  tabs: {
    get: async id => ({id, windowId:100, url:"https://chatgpt.com/", status:"complete"}),
    sendMessage: async (id, req) => {
      if (req.type === "chat2api.request.preflight") return {ok:true,data:{}};
      if (req.type === "chat2api.request") {submitted.push(req);return {ok:true};}
      if (req.type === "chat2api.reasoning.prepare.v7") return {
        ok:false,code:"reasoning_control_not_found",
        error:"ChatGPT reasoning control was not found",
        diagnostics:{verification:{ok:false,code:"reasoning_state_untrusted"}}
      };
      throw Error("Unexpected content message: "+req.type);
    }
  },
  scripting: {executeScript: async()=>{}},
  runtime: {onMessage:{addListener(){}}},
};
const ctx = {
  chrome, console, setTimeout, clearTimeout, URL,
  sleep: async()=>{}, ensureContent:async()=>{},
  trySendSocket: async msg => {eventLog.push(msg);return true},
  resolveTargetTab: async()=>({id:101,windowId:100}),
  handleServerMessage: async()=>{throw Error("Should not reach old handler")},
  __CHAT2API_MODEL_SWITCH_V149__: {
    safeSelect: async() => ({ok:true,data:{validated:true,current_model:"gpt-5.6-sol"}})
  },
  __CHAT2API_CONVERSATION_ROUTING_V1__:{
    failRequest:async()=>{retireCount++;return true;}
  },
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(model,ctx);
vm.runInContext(dispatch,ctx);

await ctx.handleServerMessage({
  type:"chat.request",request_id:"req_single_owner",
  prompt:"hi",
  options:{model:"gpt-5.6-sol",reasoning_level:"medium"},
  routing:{api_key_id:"key_demo",strict_api_fifo:true},
});
assert.equal(retireCount,0,"unrecognized reasoning control uses the page default without retiring the route");
const terminals=eventLog.filter(event=>event.type==="chat.error");
assert.equal(terminals.length,0,"no synthetic terminal for unrecognized effort control");
assert.equal(submitted.length,1,"the browser receives one real request");
assert.equal(submitted[0].options.reasoning_level,undefined);
assert.equal(submitted[0].options.effective_reasoning,null);
assert.equal(eventLog.filter(event=>event.type==="chat.diagnostics").length,1);
assert.equal(eventLog.find(event=>event.type==="chat.diagnostics").diagnostics.reasoning_default_unverified,true);
const beforeExtraHigh = eventLog.length;
await ctx.handleServerMessage({
  type:"chat.request", request_id:"req_extra_high_must_not_downgrade",
  prompt:"hi", options:{model:"gpt-5.6-sol", reasoning_level:"extra-high"},
  routing:{api_key_id:"key_demo",strict_api_fifo:true},
});
const extraHighEvents = eventLog.slice(beforeExtraHigh);
const extraHighTerminal = extraHighEvents.filter(item => item.type === "chat.error");
assert.equal(extraHighTerminal.length, 1, "unsupported extra-high has one terminal");
assert.equal(extraHighTerminal[0].diagnostics.error_code, "reasoning_level_unsupported");
assert.equal(extraHighTerminal[0].diagnostics.error_stage, "worker-reasoning-control");
assert.equal(retireCount, 1, "only unsupported extra-high retires its route exactly once");

console.log("Model preparation delegates exactly one typed terminal to dispatch");
