import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const root = "chrome_extension/";
const content = readFileSync(root + "content_voice_v2.js", "utf8");
const main = readFileSync(root + "voice_main.js", "utf8");
new vm.Script(content);
new vm.Script(main);

function section(a, b) {
  const begin = content.indexOf(a);
  const end = content.indexOf(b, begin);
  assert.ok(begin > -1 && end > begin, "voice contract section missing: " + a);
  return content.slice(begin, end);
}
const sources = [
  section("  function voiceFailure(", "  async function waitFor("),
  section("  async function openVoice(", "  function normVoiceText("),
  section("  function normVoiceText(", "  async function playVoiceInput("),
  section("  async function armRecording(", "  async function waitResponse("),
  section("  async function waitResponse(", "  async function runVoice("),
];
let clock = 0;
let submitCount = 0;
let turnSeen = false;
const stages = [];
const fakeDate = {now() { clock += 2000; return clock; }};
const input = {value:"",focus() {}};
const fakeDocument = {
  querySelectorAll(selector) {
    return selector.includes("data-message-author-role='user'") && turnSeen ?
      [{innerText:"chat2api spoken prompt"}] : [];
  },
};
const base = {
  Date:fakeDate, Math, Number, String, Boolean,
  state:{mainSequence:0}, postMain:()=>{},
  voiceTrigger:()=>({button:{click() {}},label:"Start Voice"}),
  voiceUiReady:()=>({reason:"voice-orb"}),
  waitFor:async fn =>fn(), waitMain:async()=>null,
  composerInput:()=>input, setText:(_, value)=>{input.value=value;},
  sendButton:()=>({click() {submitCount++;}}),
  document:fakeDocument, visible:()=>true,
  textOf:node=>node.innerText,
  assistantNodes:()=>[],
  pageError:()=>"",
  diagnostic:async (active, stage, data={})=>{
    active.lastStage=stage;
    stages.push({stage,...data});
  },
  eventBucket:()=>[],
  finalizeRecording:async()=>({size:900,remote_track_seen:true}),
  delay:async()=>{},
};
const context = vm.createContext(base);
vm.runInContext(sources.join("\n") +
  "\nglobalThis.voiceApi = {openVoice, sendTypedPrompt, verifyInjectedMicrophone, armRecording, waitResponse, normVoiceText};",
  context);

// The orb-only condition reproduced by TX03 must not be counted as transport.
await assert.rejects(
  context.voiceApi.openVoice({requestId:"v1", mode:"speech"}),
  /VOICE_TRANSPORT_NOT_READY/,
);
assert.ok(stages.some(x=>x.stage==="transport-not-ready"));

// A successful editor write + Send click is NOT a delivered prompt.
await assert.rejects(
  context.voiceApi.sendTypedPrompt({requestId:"v2",mode:"speech"},"chat2api spoken prompt"),
  /VOICE_TEXT_SUBMISSION_UNVERIFIED/,
);
assert.equal(submitCount,1);
assert.ok(stages.some(x=>x.stage==="prompt-submit-attempted"));
assert.ok(!stages.some(x=>x.stage==="prompt-accepted"));

// A genuinely new matching user message proves the click was accepted.
context.sendButton = ()=>({click() { submitCount++; turnSeen=true; }});
await context.voiceApi.sendTypedPrompt({requestId:"v3",mode:"speech"},"chat2api spoken prompt");
assert.equal(submitCount,2);
assert.ok(stages.some(x=>x.stage==="prompt-accepted"));

// Uploaded microphone audio is an actual conversation input only when the
// page called our intercepted getUserMedia and received a synthetic track.
context.waitMain=async()=>null;
await assert.rejects(
  context.voiceApi.verifyInjectedMicrophone({requestId:"mic-not-connected"}),
  /VOICE_SYNTHETIC_MIC_NOT_ATTACHED/,
);
assert.ok(stages.some(x=>x.stage==="mic-not-attached"));
context.waitMain=async (_id, predicate)=>{
  assert.equal(predicate({type:"voice.mic.synthetic",track_count:0}),false);
  const event={type:"voice.mic.synthetic",track_count:1};
  assert.equal(predicate(event),true);
  return event;
};
await context.voiceApi.verifyInjectedMicrophone({requestId:"mic-connected"});
assert.ok(stages.some(x=>x.stage==="mic-injection-confirmed"));

assert.equal(context.voiceApi.normVoiceText("文本\n  多行"),"文本 多行",
  "Voice prompt comparison must normalize actual Unicode layout whitespace");

// Transport and the initial ChatGPT greeting may be audible *before* the
// verified prompt. The recorder must be explicitly armed for the reply.
context.state.mainSequence = 45;
let armCommands = 0;
context.postMain = (type)=>{if (type === "voice.record.arm") armCommands++;};
context.waitMain = async (_id, predicate)=> {
  const event = {type:"voice.record.armed",remote_track_seen:true};
  assert.equal(predicate(event),true);
  return event;
};
const armed = {requestId:"arm1"};
await context.voiceApi.armRecording(armed);
assert.equal(armCommands,1);
assert.equal(armed.captureArmSequence,45);
assert.equal(armed.captureArmSawTrack,true);
assert.ok(stages.some(x=>x.stage==="capture-armed"));
await assert.rejects(
  context.voiceApi.armRecording({requestId:"cancel1",cancelled:true}),
  /VOICE_CANCELLED/,
);
assert.equal(armCommands,1, "cancelled Voice cannot start a new capture");

// Old welcome sound and earlier one-time track events must be ignored; with
// verified preexisting track proof, only response-era sound can complete.
clock=0;
context.eventBucket=()=>[
  {_voice_sequence:43,type:"voice.audio.level",speaking:true,last_sound_at:2000},
  {_voice_sequence:44,type:"voice.remote.track"},
  {_voice_sequence:46,type:"voice.record.started"},
];
await assert.rejects(
  context.voiceApi.waitResponse({...armed,timeoutSeconds:180},0),
  /VOICE_REMOTE_AUDIO_SILENT/,
);

// After a verified submit, an absent remote track fails at the stage
// deadline and never consumes the entire 300-second API timeout.
clock=0;
context.eventBucket=()=>[];
await assert.rejects(
  context.voiceApi.waitResponse({requestId:"v4", timeoutSeconds:180},0),
  /VOICE_REMOTE_TRACK_NOT_FOUND/,
);
assert.ok(clock<180000,"a missing track should stop before the API timeout");

// A visible remote track and active MediaRecorder are NOT evidence of speech.
clock=0;
context.eventBucket=()=>[
  {type:"voice.remote.track"},
  {type:"voice.record.started"},
];
await assert.rejects(
  context.voiceApi.waitResponse({requestId:"v5",timeoutSeconds:180},0),
  /VOICE_REMOTE_AUDIO_SILENT/,
);

// The MAIN-world hook is installed at document_start and acknowledges
// actual constructor/getUserMedia replacement, not the Voice orb state.
const posted=[];
const listeners={};
const pageWindow={
  addEventListener(type,listener) { listeners[type]=listener; },
  postMessage(value){posted.push(value);},
};
class Peer {
  constructor(){this.handlers={};}
  addEventListener(type,fn){this.handlers[type]=fn;}
}
class AudioCtx {
  createMediaStreamSource(){return {connect(){}};}
  createAnalyser(){return {fftSize:0,getFloatTimeDomainData(values){values.fill(0);}};}
}
class Stream {constructor(tracks){this.tracks=tracks;} }
class Recorder {
  static isTypeSupported(){return true;}
  constructor(){this.state="inactive";}
  start(){this.state="recording";}
  stop(){this.state="inactive";this.onstop?.();}
}
const page=vm.createContext({
  window:pageWindow,navigator:{mediaDevices:{getUserMedia:async()=>({})}},
  RTCPeerConnection:Peer, AudioContext:AudioCtx, MediaStream:Stream,
  MediaRecorder:Recorder, performance:{now:()=>42}, setInterval:()=>1,
  clearInterval(){}, setTimeout:fn=>fn(), console,
  Blob, Uint8Array, btoa,
});
vm.runInContext(main,page);
listeners.message({
  source:pageWindow,
  data:{source:"chat2api-isolated",type:"voice.prepare",request_id:"v6",mode:"speech"},
});
const prepared=posted.find(x=>x.type==="voice.prepared");
assert.ok(prepared);
assert.equal(prepared.peer_hook_ready,true);
assert.equal(prepared.mic_hook_ready,true);
const peer=new page.RTCPeerConnection();
assert.ok(peer.handlers.track);
peer.handlers.track({track:{kind:"audio",readyState:"live"}});
assert.ok(posted.some(x=>x.type==="voice.remote.track"));
assert.ok(!posted.some(x=>x.type==="voice.record.started"),
  "orb/track discovery must not record an unsolicited Voice greeting");
listeners.message({
  source:pageWindow,
  data:{source:"chat2api-isolated",type:"voice.record.arm",request_id:"v6"},
});
assert.ok(posted.some(x=>x.type==="voice.record.armed"&&x.request_id==="v6"));
assert.ok(posted.some(x=>x.type==="voice.record.started"&&x.request_id==="v6"));

// Reuse of the same browser tab MUST NOT turn a prior recorder's asynchronous
// onstop Base64 output into the new Voice request's completed audio.
const priorRecorder = page.__CHAT2API_VOICE_MAIN_V1__.recorder;
priorRecorder.ondataavailable({
  data:new Blob([Uint8Array.from([1,2,3,4])],{type:"audio/webm"}),
});
listeners.message({
  source:pageWindow,
  data:{source:"chat2api-isolated",type:"voice.prepare",request_id:"v7",mode:"speech"},
});
await new Promise(resolve=>setImmediate(resolve));
const priorCompleted=posted.filter(x=>x.type==="voice.record.complete");
assert.equal(priorCompleted.length,0,
  "abandoned old recording must never deliver audio after next request prepares");
assert.equal(page.__CHAT2API_VOICE_MAIN_V1__.captureArmed,false);
const trackEventsBeforeOldPeer=posted.filter(x=>x.type==="voice.remote.track").length;
peer.handlers.track({track:{kind:"audio",readyState:"live"}});
assert.equal(posted.filter(x=>x.type==="voice.remote.track").length,trackEventsBeforeOldPeer,
  "late track from a prior RTCPeerConnection cannot become this request's audio");
const nextPeer=new page.RTCPeerConnection();
nextPeer.handlers.track({track:{kind:"audio",readyState:"live"}});
const recordingsBefore=posted.filter(x=>x.type==="voice.record.started").length;
assert.equal(recordingsBefore,1,
  "the next Voice orb/track cannot auto-record before confirmed input");
listeners.message({
  source:pageWindow,
  data:{source:"chat2api-isolated",type:"voice.record.arm",request_id:"v7"},
});
assert.equal(posted.filter(x=>x.type==="voice.record.started").length,2);
assert.ok(posted.some(x=>x.type==="voice.record.armed"&&x.request_id==="v7"));

// A WebRTC track that arrives after confirmed input must start the same
// recorder, without requiring a second Voice request or retry controller.
listeners.message({
  source:pageWindow,
  data:{source:"chat2api-isolated",type:"voice.prepare",request_id:"v8",mode:"speech"},
});
const beforeLate=posted.filter(x=>x.type==="voice.record.started").length;
listeners.message({
  source:pageWindow,
  data:{source:"chat2api-isolated",type:"voice.record.arm",request_id:"v8"},
});
const lateAck=posted.find(x=>x.type==="voice.record.armed"&&x.request_id==="v8");
assert.ok(lateAck);
assert.equal(lateAck.remote_track_seen,false);
assert.equal(posted.filter(x=>x.type==="voice.record.started").length,beforeLate);
const latePeer=new page.RTCPeerConnection();
latePeer.handlers.track({track:{kind:"audio",readyState:"live"}});
assert.equal(posted.filter(x=>x.type==="voice.record.started").length,beforeLate+1);
assert.ok(posted.some(x=>x.type==="voice.record.started"&&x.request_id==="v8"));

// A previous onstop can already be inside async blob.arrayBuffer() when a
// new Voice request prepares. Do not emit its bytes after request ownership
// has changed, even though recorderCapture was detached before the await.
let unblockBlob;
class SuspendedBlob extends Blob {
  arrayBuffer() {
    return new Promise(resolve => {
      unblockBlob = () => Blob.prototype.arrayBuffer.call(this).then(resolve);
    });
  }
}
page.Blob = SuspendedBlob;
const completedBeforeAsyncReset = posted.filter(x=>x.type==="voice.record.complete").length;
listeners.message({
  source:pageWindow,
  data:{source:"chat2api-isolated",type:"voice.record.stop",request_id:"v8"},
});
assert.equal(typeof unblockBlob,"function",
  "recording finalization must reach the asynchronous blob read");
listeners.message({
  source:pageWindow,
  data:{source:"chat2api-isolated",type:"voice.prepare",request_id:"v9",mode:"speech"},
});
unblockBlob();
await new Promise(resolve=>setImmediate(resolve));
assert.equal(posted.filter(x=>x.type==="voice.record.complete").length,completedBeforeAsyncReset,
  "stale recorder bytes must be dropped after an awaited blob read");
console.log("Voice transport, prompt receipt, stage guards and recorder request ownership verified");
