import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
const source=readFileSync("chrome_extension/model_routing_v2.js","utf8");
new vm.Script(source);
const listeners=[], sent=[], socket=[], persisted=[];
const chrome={
  storage:{local:{async get(defaults){return {...defaults,models:[],modelObservedChoices:[],modelValidationState:"pending"};},async set(x){persisted.push(x);}}},
  tabs:{async sendMessage(id,message){sent.push(message);if(message.type==="chat2api.request.preflight")return {ok:true,data:{}};if(message.type==="chat2api.request")return {ok:true};throw Error("Unexpected tab message "+message.type);}},
  scripting:{async executeScript(){throw Error("No page model selector is allowed in default mode");}},
};
let modelAuthorityCalls=0;
const globalThis={__CHAT2API_MODEL_SWITCH_V149__:{async safeSelect(){modelAuthorityCalls++;throw Error("model selector invoked");}}};
const environment={chrome,globalThis,console,
  handleServerMessage:async ()=>{},resolveTargetTab:async()=>({id:77}),
  ensureContent:async()=>{},trySendSocket:async x=>{socket.push(x)},
  sleep:async()=>{},config:async()=>({}),sendExtensionStatus:async()=>{},
  chatTabs:async()=>[],
};
const ctx=vm.createContext(environment);
vm.runInContext(source,ctx);
await ctx.handleServerMessage({type:"chat.request",request_id:"req_default_v196",prompt:"hello",
  options:{model:"default",reasoning_effort:"default"}});
assert.equal(modelAuthorityCalls,0);
assert.equal(sent.filter(x=>x.type==="chat2api.reasoning.prepare.v7").length,0);
assert.equal(sent.filter(x=>x.type==="chat2api.model.prepare.v149").length,0);
const request=sent.find(x=>x.type==="chat2api.request");
assert.ok(request);
assert.equal(request.options.requested_model,"default");
assert.equal(request.options.effective_model,null);
assert.equal(request.options.model,"chatgpt-web");
assert.equal(request.options.model_prepared,false);
assert.equal(request.options.model_selection_strategy,"page-default-no-ui");
assert.equal("reasoning_effort" in request.options,false);
assert.equal("reasoning_level" in request.options,false);
assert.equal(persisted.length,0,"default must not overwrite validated model evidence");
const diagnosis=socket.find(x=>x.type==="chat.diagnostics").diagnostics;
assert.equal(diagnosis.model_selection_validated,false);
assert.equal(diagnosis.reasoning_default_unverified,true);
assert.equal(diagnosis.model_default_no_ui,true);
console.log("Model + reasoning page default skipped every model selector and preserved catalog");
