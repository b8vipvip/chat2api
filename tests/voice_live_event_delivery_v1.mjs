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
  let stopCalls=0,stopNotify=null;
  const active={requestId:"voice-event-failure",sessionId:"session-1"};
  const emitLive=vm.runInNewContext(
    liveSource.slice(start,end) + "\nemitLive",
    {
      emit:async()=>{throw new Error("server-websocket-disconnected");},
      isActiveSession:()=>true,
      stopLive:async(_active,notify)=>{stopCalls+=1;stopNotify=notify;},
      String,Promise,Error,
    },
  );
  await assert.rejects(emitLive(active,"response.audio.delta",{pcm_base64:"AAAA"}),/server-websocket-disconnected/);
  assert.equal(stopCalls,1,
    "a dropped intermediate audio/transcript event must stop upstream Voice rather than silently continue");
  assert.equal(stopNotify,true,
    "a failed event must notify the server instead of performing a silent cleanup");
  assert.equal(active.deliveryError,"server-websocket-disconnected");
  assert.equal(active.stopTerminalError.code,"VOICE_EVENT_DELIVERY_FAILED");
  assert.equal(active.stopTerminalError.message,
    "A live Voice event was not accepted by the server Broker: server-websocket-disconnected");

  stopCalls=0;
  await assert.rejects(emitLive(active,"session.ready"),/server-websocket-disconnected/);
  assert.equal(stopCalls,0,
    "session.ready startup cleanup is owned by startLive and must not race a second stop");
  await assert.rejects(emitLive(active,"session.closed"),/server-websocket-disconnected/);
  assert.equal(stopCalls,0,
    "a failed terminal send must propagate to the stop owner without recursive teardown");

  stopCalls=0;
  stopNotify=null;
  await assert.rejects(emitLive(active,"error"),/server-websocket-disconnected/);
  assert.equal(stopCalls,1,
    "if the fatal error event itself cannot be delivered, local Voice media must still stop");
  assert.equal(stopNotify,true,
    "a failed fatal notification must trigger one explicit error-terminal retry");
}

{
  const start=liveSource.indexOf("  async function waitFor(");
  const end=liveSource.indexOf("\n  function emit(",start);
  assert.ok(start>=0 && end>start,"production Voice readiness wait must remain extractable");
  const active={requestId:"voice-wait-failure"};
  const state={mainEvents:[{type:"voice.live.error",error:"synthetic microphone AudioContext could not resume"}]};
  const waitFor=vm.runInNewContext(
    liveSource.slice(start,end)+"\nwaitFor",
    {
      state,
      isActiveSession:()=>true,
      delay:async()=>{},
      Date,String,Error,Promise,
    },
  );
  await assert.rejects(
    waitFor(()=>null,15000,100,active),
    /synthetic microphone AudioContext could not resume/,
    "an explicit MAIN-world media failure must interrupt startup instead of waiting for the readiness timeout",
  );
}

{
  const start=liveSource.indexOf("  function sendStopTerminal(");
  const end=liveSource.indexOf("\n  async function stopLive(",start);
  assert.ok(start>=0 && end>start,"production Voice terminal selection must remain extractable");
  const events=[];
  const sendStopTerminal=vm.runInNewContext(
    liveSource.slice(start,end) + "\nsendStopTerminal",
    {
      emitLive:async(_active,event,data)=>{events.push({event,data});},
      Promise,String,Error,
    },
  );
  const active={
    stopTerminalError:{code:"VOICE_EVENT_DELIVERY_FAILED",message:"Broker rejected an audio frame"},
    stopMainAcknowledged:true,
  };
  await sendStopTerminal(active);
  assert.equal(events.length,1,"event loss must produce exactly one terminal");
  assert.equal(events[0].event,"error",
    "event loss must produce an error terminal, not a false successful session.closed");
  assert.equal(events[0].data.code,"VOICE_EVENT_DELIVERY_FAILED");
  assert.equal(events[0].data.message,"Broker rejected an audio frame");
  assert.equal(active.stopTerminalSent,true);
}

{
  const start=liveSource.indexOf("  function activeForStopRequest(");
  const end=liveSource.indexOf("  async function waitFor(",start);
  assert.ok(start>=0 && end>start,"request-scoped stop lookup must remain extractable");
  const current={requestId:"current-session"};
  const stopped={requestId:"previous-session",stopped:true};
  const state={active:current,stoppedSessions:new Map([[stopped.requestId,stopped]])};
  const activeForStopRequest=vm.runInNewContext(
    liveSource.slice(start,end) + "\nactiveForStopRequest",
    {state,String},
  );
  assert.equal(activeForStopRequest("current-session"),current);
  assert.equal(activeForStopRequest("previous-session"),stopped,
    "a delayed stop must resolve only to its original stopped session");
  assert.equal(activeForStopRequest("unknown-request"),null,
    "an unknown stale stop must not fall back to the currently active call");
  assert.equal(activeForStopRequest(""),current,
    "legacy stop messages without a request ID retain current-session behavior");
}

{
  const start=liveSource.indexOf("  function sendStopTerminal(");
  const end=liveSource.indexOf("\n  window.addEventListener(\"message\"",start);
  assert.ok(start>=0 && end>start,"production stopLive drain must remain extractable");

  async function makeStopHarness() {
    let acknowledge;
    const mainStopAck=new Promise(resolve=>{acknowledge=resolve;});
    const active={requestId:"concurrent-stop",stopped:false};
    const state={active,currentResponseId:"response-1",mainEvents:[],stoppedSessions:new Map()};
    const calls={polling:0,wait:0,post:[],click:0,flush:0,events:[]};
    const stopLive=vm.runInNewContext(
      liveSource.slice(start,end) + "\nstopLive",
      {
        state,
        stopTranscriptPolling(){calls.polling+=1;},
        waitForMainStop(){calls.wait+=1;return mainStopAck;},
        postMain(type){calls.post.push(type);},
        endVoiceButton(){return {click(){calls.click+=1;}};},
        flushPendingEmits:async()=>{calls.flush+=1;},
        emitLive:async(_active,event)=>{calls.events.push(event);},
        Promise,String,Error,
      },
    );
    return {active,state,calls,stopLive,acknowledge};
  }

  {
    const h=await makeStopHarness();
    const first=h.stopLive(h.active,true);
    const second=h.stopLive(h.active,true);
    assert.equal(h.calls.polling,1);
    assert.equal(h.calls.wait,1);
    assert.deepEqual(h.calls.post,["voice.live.stop"]);
    h.acknowledge(true);
    await Promise.all([first,second]);
    assert.deepEqual(h.calls.events,["session.closed"],
      "concurrent stop requests must emit one terminal only after the shared transcript drain");
    assert.deepEqual(h.calls.post,["voice.live.stop","voice.reset"]);
    assert.equal(h.calls.click,1);
    assert.equal(h.calls.flush,1);
    assert.equal(h.state.active,null);
  }

  {
    const h=await makeStopHarness();
    const silentCleanup=h.stopLive(h.active,false);
    const brokerStop=h.stopLive(h.active,true);
    h.acknowledge(true);
    await Promise.all([silentCleanup,brokerStop]);
    assert.deepEqual(h.calls.events,["session.closed"],
      "a later broker stop must upgrade an in-flight silent cleanup to a server-notified terminal");
    assert.equal(h.calls.wait,1);
    assert.equal(h.calls.click,1);
  }

  {
    const h=await makeStopHarness();
    const silentCleanup=h.stopLive(h.active,false);
    h.acknowledge(true);
    await silentCleanup;
    assert.deepEqual(h.calls.events,[]);
    await h.stopLive(h.active,true);
    assert.deepEqual(h.calls.events,["session.closed"],
      "a broker stop arriving just after silent cleanup must still deliver one terminal event");
    assert.equal(h.calls.wait,1);
    assert.equal(h.calls.click,1);
  }

  {
    const h=await makeStopHarness();
    const silentCleanup=h.stopLive(h.active,false);
    h.acknowledge(true);
    await silentCleanup;
    const newer={requestId:"newer-session"};
    h.state.active=newer;
    h.state.currentResponseId="newer-response";
    h.state.mainEvents=["newer-event"];
    await h.stopLive(h.active,true);
    assert.equal(h.state.active,newer,
      "a late stop for an old session must not clear the new active Voice owner");
    assert.equal(h.state.currentResponseId,"newer-response");
    assert.deepEqual(h.state.mainEvents,["newer-event"]);
  }
}

assert.ok(backgroundSource.includes('error: sent ? "" : "server-websocket-disconnected-or-send-failed"'),
  "background transport must return a diagnosable negative acknowledgement");
assert.ok(liveSource.includes(".catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));"),
  "the graceful stop listener must not convert an undelivered terminal event into ok:true");
assert.ok(routerSource.includes("if (!response?.ok) {"),
  "the Worker router must surface a failed page-controller stop drain");

console.log("voice-live-event-delivery-v1-passed");
