import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/audio_routing_live.js", "utf8");
function deferred() {
  let resolve;
  const promise = new Promise(ok => resolve = ok);
  return {promise, resolve};
}
const flush = async () => {
  for (let i=0;i<10;i++) await new Promise(resolve=>setImmediate(resolve));
};

function setup({deferAllocation=false, deferStartAck=false}={}) {
  const allocated = {id:701, windowId:801};
  const allocation = deferred(), ack = deferred();
  const events = [];
  let released = false;
  const router = {
    async liveTabForRequest(requestId) {
      return !released && requestId === "live-race-230" ? allocated : null;
    },
    async finishLiveSession(requestId, reason) {
      events.push(["release",requestId,reason]);
      const first = !released;
      released = true;
      return first;
    },
    async failRequest(id,reason){events.push(["fail",id,reason]);return true},
  };
  const env = {
    globalThis:null,
    handleServerMessage:async()=>{},
    chat2apiResolveRoutedWorkerTabV24:async()=>deferAllocation?allocation.promise:allocated,
    chat2apiActivateVoiceTabV184:async tab=>({...tab,chat2apiWindowFocusedAfter:true}),
    ensureContent:async()=>{},
    sleep:async()=>{},
    trySendSocket:async event=>{events.push(["error",event.type]);return true},
    chrome:{
      scripting:{executeScript:async()=>{}},
      tabs:{async sendMessage(id,message){
        events.push(["send",id,message.type]);
        if(message.type === "chat2api.voice.live.start")
          return deferStartAck ? ack.promise : {ok:true};
        return {ok:true};
      }}
    },
  };
  env.globalThis=env;
  vm.createContext(env);
  vm.runInContext(source,env);
  return {env,events,allocation,ack,allocated};
}

// A stop arrives while the first async tab allocation is unresolved.
const a = setup({deferAllocation:true});
const pendingStart = a.env.handleServerMessage({
  type:"voice.live.start",request_id:"live-race-230",
  session_id:"session-230",routing:{api_key_id:"keyA"}});
await flush();
await a.env.handleServerMessage({type:"voice.live.stop",request_id:"live-race-230"});
a.allocation.resolve(a.allocated);
await pendingStart; await flush();
assert.equal(a.events.filter(x=>x[2]==="chat2api.voice.live.start").length,0,
  "a stopped session must not invoke slow-started Voice after allocation");
assert.equal(a.events.filter(x=>x[0]==="error").length,0);
assert.ok(a.events.some(x=>x[0]==="release"));
await a.env.handleServerMessage({type:"voice.live.start",request_id:"live-race-230",
  routing:{api_key_id:"keyA"}});
assert.equal(a.events.filter(x=>x[2]==="chat2api.voice.live.start").length,0,
  "duplicate/replayed start must not resurrect a stopped session");

// Stop overtakes an already sent start request, before the ACK.
const b = setup({deferStartAck:true});
await b.env.handleServerMessage({type:"voice.live.start",request_id:"live-race-230",
  session_id:"session-230",routing:{api_key_id:"keyA"}});
await flush();
assert.equal(b.events.filter(x=>x[2]==="chat2api.voice.live.start").length,1);
await b.env.handleServerMessage({type:"voice.live.stop",request_id:"live-race-230"});
b.ack.resolve({ok:true});
await flush();
assert.ok(b.events.filter(x=>x[2]==="chat2api.voice.live.stop").length >= 2,
 "a late start acknowledgement requires an idempotent stop to the same tab");
assert.equal(b.events.filter(x=>x[0]==="error").length,0,
 "normal session teardown cannot emit a false browser-start error");
await b.env.handleServerMessage({type:"voice.live.audio",request_id:"live-race-230",pcm_base64:"AAAA"});
assert.equal(b.events.filter(x=>x[2]==="chat2api.voice.live.audio").length,0,
 "late microphone packets cannot reactivate a stopped session");
console.log("v230 live stop during slow allocation and late start ACK passed");
