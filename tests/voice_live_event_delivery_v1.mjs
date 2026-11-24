import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const liveSource=readFileSync("chrome_extension/content_voice_live.js","utf8");
const backgroundSource=readFileSync("chrome_extension/background.js","utf8");
const routerSource=readFileSync("chrome_extension/audio_routing_live.js","utf8");
new vm.Script(liveSource);
new vm.Script(backgroundSource);
new vm.Script(routerSource);

const start=liveSource.indexOf("  function emit(event) {");
const end=liveSource.indexOf("  function waitForMainStop(",start);
assert.ok(start>=0 && end>start,"production Voice event sender must remain extractable");
const implementation=liveSource.slice(start,end);

async function runEmit(sendMessage) {
  const pendingEmits=new Set();
  const chrome={runtime:{sendMessage}};
  const emit=vm.runInNewContext(
    implementation + "\nemit",
    {chrome,pendingEmits,Promise,String,Error},
  );
  return {pendingEmits,emit};
}

{
  const sent=[];
  const h=await runEmit(async message=>{
    sent.push(message);
    return {ok:true};
  });
  const result=await h.emit({type:"image.progress",kind:"voice-live",live_event:"session.ready"});
  assert.deepEqual(result,{ok:true});
  assert.equal(sent.length,1);
  assert.equal(sent[0].type,"chat2api.event");
  await Promise.resolve();
  assert.equal(h.pendingEmits.size,0);
}

{
  const h=await runEmit(async()=>({ok:false,error:"server-websocket-disconnected-or-send-failed"}));
  await assert.rejects(
    h.emit({type:"image.progress",kind:"voice-live",live_event:"session.ready"}),
    /server-websocket-disconnected-or-send-failed/,
    "an unaccepted session.ready must never be treated as delivered",
  );
  await Promise.resolve();
  assert.equal(h.pendingEmits.size,0);
}

{
  const h=await runEmit(async()=>undefined);
  await assert.rejects(
    h.emit({type:"image.progress",kind:"voice-live",live_event:"session.closed"}),
    /Voice event was not accepted/,
    "missing background acknowledgement must fail closed",
  );
  await Promise.resolve();
  assert.equal(h.pendingEmits.size,0);
}

{
  const h=await runEmit(()=>{throw new Error("runtime unavailable");});
  await assert.rejects(h.emit({type:"image.progress",kind:"voice-live",live_event:"response.done"}),/runtime unavailable/);
  await Promise.resolve();
  assert.equal(h.pendingEmits.size,0);
}

{
  const start=liveSource.indexOf("  function emitLive(");
  const end=liveSource.indexOf("  function postMain(",start);
  assert.ok(start>=0 && end>start,"production emitLive failure policy must remain extractable");
  let stopCalls=0;
  const active={requestId:"voice-event-failure",sessionId:"session-1"};
  const emitLive=vm.runInNewContext(
    liveSource.slice(start,end) + "\nemitLive",
    {
      emit:async()=>{throw new Error("server-websocket-disconnected");},
      isActiveSession:()=>true,
      stopLive:async()=>{stopCalls+=1;},
      String,Promise,Error,
    },
  );
  await assert.rejects(emitLive(active,"response.audio.delta",{pcm_base64:"AAAA"}),/server-websocket-disconnected/);
  assert.equal(stopCalls,1,
    "a dropped intermediate audio/transcript event must stop upstream Voice rather than silently continue");
  assert.equal(active.deliveryError,"server-websocket-disconnected");

  stopCalls=0;
  await assert.rejects(emitLive(active,"session.ready"),/server-websocket-disconnected/);
  assert.equal(stopCalls,0,
    "session.ready startup cleanup is owned by startLive and must not race a second stop");
  await assert.rejects(emitLive(active,"session.closed"),/server-websocket-disconnected/);
  assert.equal(stopCalls,0,
    "a failed terminal send must propagate to the stop owner without recursive teardown");
}

assert.ok(backgroundSource.includes('error: sent ? "" : "server-websocket-disconnected-or-send-failed"'),
  "background transport must return a diagnosable negative acknowledgement");
assert.ok(liveSource.includes(".catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));"),
  "the graceful stop listener must not convert an undelivered terminal event into ok:true");
assert.ok(routerSource.includes("if (!response?.ok) {"),
  "the Worker router must surface a failed page-controller stop drain");

console.log("voice-live-event-delivery-v1-passed");
