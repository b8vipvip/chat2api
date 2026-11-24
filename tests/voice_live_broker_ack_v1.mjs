import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source=readFileSync("chrome_extension/background.js","utf8");
const start=source.indexOf("const pendingVoiceEventAcks = new Map();");
const end=source.indexOf("function nativeCapacityControlMetadata()",start);
assert.ok(start>=0 && end>start,"production Voice broker ACK transport must remain extractable");
const implementation=source.slice(start,end);

function harness() {
  const timers=new Map();
  let nextTimer=1;
  const socket={
    readyState:1,
    sent:[],
    send(raw){this.sent.push(JSON.parse(raw));},
  };
  const context={
    socket,
    WebSocket:{OPEN:1},
    crypto:{randomUUID:()=>"11111111-2222-4333-8444-555555555555"},
    setTimeout(fn,ms){const id=nextTimer++;timers.set(id,{fn,ms});return id;},
    clearTimeout(id){timers.delete(id);},
    Promise,Map,String,Number,Boolean,JSON,
  };
  const api=vm.runInNewContext(
    implementation + "\n({awaitVoiceEventAck,handleVoiceEventAck,rejectPendingVoiceEventAcks,pendingVoiceEventAcks})",
    context,
  );
  return {socket,timers,api};
}

{
  const h=harness();
  const event={type:"image.progress",kind:"voice-live",request_id:"voicereq_12345678",live_event:"session.ready"};
  const pending=h.api.awaitVoiceEventAck(event);
  assert.equal(h.socket.sent.length,1);
  const sent=h.socket.sent[0];
  assert.equal(sent.worker_event_ack_id,"11111111-2222-4333-8444-555555555555");
  assert.equal(sent.live_event,"session.ready");
  assert.equal(h.api.pendingVoiceEventAcks.size,1);

  const otherSocket={};
  assert.equal(h.api.handleVoiceEventAck({
    type:"voice.event.ack",event_id:sent.worker_event_ack_id,
    request_id:event.request_id,accepted:true,
  },otherSocket),false,"ACK from a replaced/untrusted socket must not resolve the send");
  assert.equal(h.api.handleVoiceEventAck({
    type:"voice.event.ack",event_id:sent.worker_event_ack_id,
    request_id:"another-request",accepted:true,
  },h.socket),false,"ACK for a different request must not resolve the send");
  assert.equal(h.api.pendingVoiceEventAcks.size,1);

  assert.equal(h.api.handleVoiceEventAck({
    type:"voice.event.ack",event_id:sent.worker_event_ack_id,
    request_id:event.request_id,accepted:true,
  },h.socket),true);
  const accepted=await pending;
  assert.equal(accepted.ok,true);
  assert.equal(accepted.error,"");
  assert.equal(h.api.pendingVoiceEventAcks.size,0);
  assert.equal(h.timers.size,0);
}

{
  const h=harness();
  const event={type:"image.progress",kind:"voice-live",request_id:"voicereq_87654321",live_event:"response.done"};
  const pending=h.api.awaitVoiceEventAck(event);
  const sent=h.socket.sent[0];
  assert.equal(h.api.handleVoiceEventAck({
    type:"voice.event.ack",event_id:sent.worker_event_ack_id,
    request_id:event.request_id,accepted:false,reason:"request-not-owned-or-active",
  },h.socket),true);
  const rejected=await pending;
  assert.equal(rejected.ok,false);
  assert.equal(rejected.error,"request-not-owned-or-active");
  assert.equal(h.api.pendingVoiceEventAcks.size,0);
}

{
  const h=harness();
  const pending=h.api.awaitVoiceEventAck({
    type:"image.progress",kind:"voice-live",request_id:"voicereq_timeout",live_event:"response.audio.delta",
  });
  const [timerId,timer]=[...h.timers.entries()][0];
  assert.equal(timer.ms,8000);
  timer.fn();
  const timedOut=await pending;
  assert.equal(timedOut.ok,false);
  assert.equal(timedOut.error,"voice-event-broker-ack-timeout");
  assert.equal(h.api.pendingVoiceEventAcks.size,0);
  h.api.rejectPendingVoiceEventAcks("test cleanup");
}

{
  const h=harness();
  const pending=h.api.awaitVoiceEventAck({
    type:"image.progress",kind:"voice-live",request_id:"voicereq_disconnect",live_event:"session.closed",
  });
  h.api.rejectPendingVoiceEventAcks("socket-closed-before-broker-ack",h.socket);
  const disconnected=await pending;
  assert.equal(disconnected.ok,false);
  assert.equal(disconnected.error,"socket-closed-before-broker-ack");
  assert.equal(h.api.pendingVoiceEventAcks.size,0);
}

{
  const h=harness();
  const result=await h.api.awaitVoiceEventAck({
    type:"image.progress",kind:"chat",request_id:"voicereq_invalid",live_event:"session.ready",
  });
  assert.equal(result.ok,false);
  assert.equal(result.error,"invalid-voice-event-envelope");
  assert.equal(h.socket.sent.length,0);
}

console.log("voice-live-broker-ack-v1-passed");
