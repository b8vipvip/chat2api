import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {TextDecoder} from "node:util";
const router=fs.readFileSync("chrome_extension/conversation_routing.js","utf8");
const begin=router.indexOf("  async function failRequest(");
const end=router.indexOf("  state.failRequest = failRequest;",begin);
assert.ok(begin>0&&end>begin);
const route={request_scoped:true,window_id:900,tab_id:901,live_session:false,inflight_request_id:"req_failed"};
const state={activeRequests:new Map([["req_failed",{}]]),routes:{owner:route}};
const calls=[];
const ctx={state,Date,Number,String,
  keyForRequest:async()=> "owner",
  routeFor:async()=>route,
  persist:async()=>calls.push("persist"),
  scheduleClose:async r=>calls.push("schedule:"+r.window_id),
  retireRoute:async()=>{calls.push("immediate-retirement");return true}};
vm.runInNewContext(router.slice(begin,end)+"\nthis.fail=failRequest;",ctx);
assert.equal(await ctx.fail("req_failed","chat.error"),true);
assert.equal(route.inflight_request_id,null);
assert.deepEqual(calls,["persist","schedule:900"]);
assert.equal(await ctx.fail("req_failed","replayed-error"),false);
assert.deepEqual(calls,["persist","schedule:900"]);
const old={request_scoped:false,window_id:777,inflight_request_id:"req_legacy"};
ctx.routeFor=async()=>old;
assert.equal(await ctx.fail("req_legacy"),true);
assert.equal(calls.at(-1),"immediate-retirement");
assert.ok(router.includes("const IDLE_CLOSE_MS = 5 * 1000"));
assert.ok(router.includes('const ALARM_PREFIX = "chat2api-route-close:"'));

// An aborted source fetch and a clone-only failure can be distinguished
// without inventing a reply terminal or serializing abort reasons.
const code=fs.readFileSync("chrome_extension/network_stream_main_v55.js","utf8");
async function classify(nativeAbort){
  const messages=[],listeners={};
  const signal={aborted:nativeAbort,
    addEventListener:(name,fn)=>listeners[name]=fn,
    removeEventListener:(name)=>delete listeners[name],
    reason:"secret reason must never leave the tab"};
  const abortErr=new Error("reader stopped");abortErr.name="AbortError";
  const resp={ok:true,status:200,body:{locked:false},
    headers:{get:()=> "text/event-stream"},
    clone:()=>({body:{getReader:()=>({read:async()=>{throw abortErr},releaseLock(){}})}})};
  const x={globalThis:null,TextDecoder,Date,Number,URL,Math,setTimeout,
    location:{href:"https://chatgpt.com"},document:{documentElement:{setAttribute(){}}},
    postMessage:value=>messages.push(value),fetch:async()=>resp};
  x.globalThis=x;vm.runInNewContext(code,x);
  await x.fetch("https://chatgpt.com/backend-api/f/conversation",{method:"POST",signal});
  for(let i=0;i<10;i++)await new Promise(r=>setTimeout(r,0));
  assert.ok(!messages.some(x=>x.phase==="assistant-complete"));
  assert.ok(!messages.some(x=>JSON.stringify(x).includes("secret reason")));
  return messages.find(x=>x.phase==="error");
}
const aborted=await classify(true);
assert.equal(aborted.clone_reader_failure_kind,"page-fetch-signal-aborted");
assert.equal(aborted.native_signal_initially_aborted,true);
const cloneOnly=await classify(false);
assert.equal(cloneOnly.clone_reader_failure_kind,"clone-reader-without-native-abort");
assert.equal(cloneOnly.native_signal_initially_aborted,false);
assert.ok(!cloneOnly.native_signal_abort_observed);
console.log("v237 sole router terminal-close grace and SSE abort provenance passed");
