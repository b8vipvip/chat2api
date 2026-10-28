import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const routing = readFileSync("chrome_extension/conversation_routing.js","utf8");
const pool = readFileSync("chrome_extension/conversation_persistent_pool_v132.js","utf8");
const audio = readFileSync("chrome_extension/audio_routing_live.js","utf8");

const start = routing.indexOf("  async function liveTabForRequest(");
const end = routing.indexOf("\n  state.liveTabForRequest = liveTabForRequest;",start);
assert.ok(start>0 && end>start,"live lifecycle must be owned by the router");

const stored = {
  api_key_id:"keyA", window_id:501, tab_id:601, request_scoped:true,
  live_session:true, inflight_request_id:"live_abc", window_owned:true,
  close_after:null
};
const state={routes:{"keyA::live::live_abc":stored},activeRequests:new Map()};
const called={close:[],persist:0,tabGet:[]};
const ctx={state,Date,String,Number,
  keyForRequest:async requestId=>requestId==="live_abc"?"keyA::live::live_abc":null,
  chrome:{tabs:{async get(id){called.tabGet.push(id);return{id,windowId:501}}}},
  persist:async()=>{called.persist++},
  scheduleClose:async route=>{called.close.push(route.window_id)},
};
vm.createContext(ctx);
vm.runInContext(routing.slice(start,end)+
  "\nthis.getLive=liveTabForRequest;this.stopLive=finishLiveSession;",ctx);
assert.equal((await ctx.getLive("live_abc")).id,601,
 "MV3-restarted process must recover persisted original tab");
assert.equal(await ctx.getLive("different_live_request"),null);
const badTab=ctx.chrome.tabs.get;
ctx.chrome.tabs.get=async id=>({id,windowId:999});
assert.equal(await ctx.getLive("live_abc"),null,
  "a replaced window ID must not inherit the active session");
ctx.chrome.tabs.get=badTab;
assert.equal(await ctx.stopLive("live_abc","explicit-session-finish"),true);
assert.equal(stored.inflight_request_id,null);
assert.equal(stored.live_session,false);
assert.deepEqual(called.close,[501],"the existing router owns delayed close");
assert.equal(await ctx.stopLive("live_abc"),false,"replayed session.stop must be idempotent");
assert.equal(await ctx.stopLive("other_request"),false);
assert.equal(called.close.length,1);

const pStart=pool.indexOf("  function routingKey(");
const pEnd=pool.indexOf("\n  function freshRoute(",pStart);
assert.ok(pStart>0&&pEnd>pStart);
const check=vm.runInNewContext(pool.slice(pStart,pEnd)+
  "\nthis.routingKey=routingKey;this",{}).routingKey;
assert.equal(check({type:"voice.live.start",request_id:"live_abc",
  routing:{api_key_id:"keyA"}}),"keyA::live::live_abc");
assert.equal(check({type:"voice.request",request_id:"one",
  routing:{api_key_id:"keyA"}}),"keyA::request::one");
assert.equal(check({type:"voice.live.audio",request_id:"live_abc",
  routing:{api_key_id:"keyA"}}),"keyA");

const events=[],router={
  liveTabForRequest:async id=>id==="live_abc"?{id:601,windowId:501}:null,
  finishLiveSession:async (id,reason)=>{events.push(["finish",id,reason]);return true},
};
const aCtx={globalThis:null, handleServerMessage:async()=>{},
  __CHAT2API_CONVERSATION_ROUTING_V1__:router,
  chrome:{tabs:{sendMessage:async (id,message)=>{
    events.push(["stop-message",id,message.type]);return{ok:true};
  }}},
  trySendSocket:async ev=>{events.push(["socket",ev.type]);return true},
};
aCtx.globalThis=aCtx;
vm.createContext(aCtx);
vm.runInContext(audio,aCtx);
await aCtx.handleServerMessage({type:"voice.live.stop",request_id:"live_abc"});
assert.ok(events.some(row=>row[0]==="stop-message" && row[1]===601));
assert.ok(events.some(row=>row[0]==="finish" && row[1]==="live_abc"));
assert.ok(!events.some(row=>row[0]==="socket"),
  "voice.live.stop must not fabricate any terminal message");
events.length=0;
await aCtx.handleServerMessage({type:"voice.live.cancel_response",request_id:"live_abc"});
assert.ok(!events.some(row=>row[0]==="finish"),
  "barge-in response cancellation must preserve ongoing live session");

console.log("v229 voice live dedicated routing, exact-owner stop, MV3 restore and barge-in safety passed");
