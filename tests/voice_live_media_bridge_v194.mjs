import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const liveMain = readFileSync("chrome_extension/voice_live_main.js", "utf8");
const liveContent = readFileSync("chrome_extension/content_voice_live.js", "utf8");
const liveText = readFileSync("chrome_extension/content_voice_live_text_v24.js", "utf8");
for (const src of [liveMain, liveContent, liveText]) new vm.Script(src);
assert.ok(liveMain.includes("state.remoteTrack"), "stream from actual WebRTC track");
assert.ok(!liveMain.includes("state?.recorder?.stream"), "never wait on an unarmed MediaRecorder");
assert.ok(!liveMain.includes("recorderChunks.splice"), "live stream cannot mutate batch recording");
assert.ok(liveContent.includes('voice.live.remote.ready'), "ready only after PCM processor starts");
assert.ok(liveContent.includes('voice.mic.synthetic'), "ready only after microphone acquisition");
assert.ok(!liveContent.includes("ready when you are"), "generic body text is not Voice UI readiness");
assert.ok(liveText.includes("baselineUsers"), "text receipt needs a real new ChatGPT user turn");
assert.ok(liveText.includes("assertComposerDraftSafe"), "live input must not overwrite a user-owned composer draft");
assert.ok(liveText.includes("refusing to overwrite it"), "draft conflicts must fail closed");
assert.ok(liveText.includes("const hasNewUserTurn = () =>"), "composer lag must be checked against the new user turn");
assert.ok(liveText.includes("if (!sent && !hasNewUserTurn() && !dispatchedEnter)"),
  "a second Enter must never be sent after ChatGPT already accepted the turn");
assert.ok(liveContent.includes("function latestRoleMessage(role)"), "Voice transcript polling tracks the actual ChatGPT message node");
assert.ok(liveContent.includes("lastUserMessageKey"), "Voice transcript polling remembers the last user message identity");
assert.ok(liveContent.includes("userMessageKey !== state.lastUserMessageKey || userText !== state.lastUserText"),
  "two consecutive identical user utterances must still produce two transcript events");
assert.ok(liveContent.includes("assistantBaselineMessageKey"),
  "each assistant response must capture its own prior-message identity");
assert.ok(liveContent.includes("hasCurrentResponseText"),
  "a completed response must not reuse the previous assistant turn's text");
assert.ok(liveContent.includes("function assistantTextForResponse(node)"),
  "a reused ChatGPT assistant DOM node must contribute only the new response suffix");

// Exercise the transcript poller with two distinct ChatGPT user message nodes
// containing the same text. The second utterance must not be deduplicated.
const transcriptIntervals = new Map();
const transcriptEvents = [];
const contentMessageListeners = [];
let transcriptTimerId = 0;
let userNodes = [makeTranscriptNode("repeat this phrase", "user-1")];
let assistantNodes = [makeTranscriptNode("previous answer", "assistant-1")];
function makeTranscriptNode(text, messageId) {
  return {
    innerText:text,
    textContent:text,
    getAttribute(name){return name==="data-message-id" ? messageId : null;},
    getBoundingClientRect(){return {width:100,height:20};},
  };
}
const transcriptWindow = {
  addEventListener(type,listener){if(type==="message")contentMessageListeners.push(listener);},
};
const transcriptChrome = {
  runtime:{
    onMessage:{addListener(){}},
    sendMessage:async message=>{transcriptEvents.push(message);return {ok:true};},
  },
};
const transcriptSandbox = vm.createContext({
  window:transcriptWindow,
  chrome:transcriptChrome,
  document:{querySelectorAll(selector){
    if(selector.includes("data-message-author-role='user'")) return userNodes;
    if(selector.includes("data-message-author-role='assistant'")) return assistantNodes;
    return [];
  }},
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
  setInterval(fn){const id=++transcriptTimerId;transcriptIntervals.set(id,fn);return id;},
  clearInterval(id){transcriptIntervals.delete(id);},
  setTimeout,
  clearTimeout,
  Date,Math,Promise,String,Number,Set,Array,
});
const closingIife = liveContent.lastIndexOf("})();");
assert.ok(closingIife >= 0,"content script IIFE terminator exists");
const instrumentedContent = liveContent.slice(0,closingIife) +
  "globalThis.__voiceTranscriptTest = {startTranscriptPolling,state}; " +
  liveContent.slice(closingIife);
assert.notEqual(instrumentedContent,liveContent,"transcript poller test hook must be injected");
vm.runInContext(instrumentedContent,transcriptSandbox);
const transcriptTest = transcriptSandbox.__voiceTranscriptTest;
const transcriptSession = {requestId:"voice-request-1",sessionId:"voice-session-1"};
transcriptTest.state.active = transcriptSession;
transcriptTest.startTranscriptPolling(transcriptSession);
const pollTranscript = [...transcriptIntervals.values()][0];
pollTranscript();
userNodes = [makeTranscriptNode("repeat this phrase", "user-2")];
pollTranscript();
await flush();
const repeatedTurns = transcriptEvents
  .map(message=>message.event)
  .filter(event=>event?.live_event==="transcript.final");
assert.equal(repeatedTurns.length,1);
assert.equal(repeatedTurns[0].text,"repeat this phrase");

function dispatchVoiceMain(type,responseId,reason="") {
  for(const listener of contentMessageListeners) listener({
    source:transcriptWindow,
    data:{
      source:"chat2api-main",
      request_id:"voice-request-1",
      session_id:"voice-session-1",
      type,
      response_id:responseId,
      reason,
    },
  });
}
// A response with no new assistant DOM turn must not publish the previous
// answer again as if it were generated by the current response.
dispatchVoiceMain("voice.live.response.started","response-empty");
dispatchVoiceMain("voice.live.response.stopped","response-empty");
await flush();
const emptyResponse = transcriptEvents
  .map(message=>message.event)
  .find(event=>event?.live_event==="response.done" && event.response_id==="response-empty");
assert.ok(emptyResponse,"a stopped response still emits its completion event");
assert.equal(emptyResponse.text,"",
  "an unchanged previous assistant message must never be replayed as the new response");
// When the current assistant turn has a new message identity, capture its
// latest text synchronously even if the 250 ms polling interval has not run.
dispatchVoiceMain("voice.live.response.started","response-new");
assistantNodes = [makeTranscriptNode("new assistant answer","assistant-2")];
dispatchVoiceMain("voice.live.response.stopped","response-new");
await flush();
const newResponse = transcriptEvents
  .map(message=>message.event)
  .find(event=>event?.live_event==="response.done" && event.response_id==="response-new");
assert.equal(newResponse?.text,"new assistant answer");

// Some Voice UI revisions reuse the same assistant message node after a
// barge-in. The new response must contain only the appended suffix.
assistantNodes = [makeTranscriptNode("older partial","assistant-3")];
dispatchVoiceMain("voice.live.response.started","response-reused-node");
assistantNodes[0].innerText = "older partial continuation";
assistantNodes[0].textContent = "older partial continuation";
dispatchVoiceMain("voice.live.response.stopped","response-reused-node");
await flush();
const reusedNodeResponse = transcriptEvents
  .map(message=>message.event)
  .find(event=>event?.live_event==="response.done" && event.response_id==="response-reused-node");
assert.equal(reusedNodeResponse?.text,"continuation",
  "the next response must not repeat the previous partial text when the DOM node is reused");

const listeners = [];
const posted = [];
const timers = new Map();
let timerSequence = 0;
let realAudioSource = null;
let remoteProcessor = null;
const remoteSources = [];
const remoteProcessors = [];
const sources = [];
function makeTrack(name) {
  const listeners = new Map();
  return {
    name, kind:"audio", readyState:"live",
    addEventListener(type,callback) {
      if (!listeners.has(type)) listeners.set(type,[]);
      listeners.get(type).push(callback);
    },
    end() {
      this.readyState = "ended";
      for (const callback of listeners.get("ended") || []) callback();
    },
  };
}
const firstTrack = makeTrack("first");
const window = {
  addEventListener(type, callback) { if (type === "message") listeners.push(callback); },
  postMessage(message) { posted.push(message); },
};
const currentVoiceState = {
  requestId: "live-v194",
  remoteTrack: firstTrack,
  recorder: null,
  recorderChunks: [],
  micDestination: { id: "outbound-mic" },
  micContext: {
    currentTime: 0,
    resume: async () => {},
    createBuffer(_channels, count, rate) {
      return {duration:count/rate,copyToChannel(samples){this.data=new Float32Array(samples);}};
    },
    createBufferSource() {
      const src = {connect(){},start(at){sources.push(at);},stop(){},onended:null};
      return src;
    },
  },
};
class FakeAudioContext {
  constructor(){this.state="running";this.sampleRate=44100;this.destination={};}
  createMediaStreamSource(stream){realAudioSource=stream;remoteSources.push(stream.tracks[0]);return {connect(){},disconnect(){}};}
  createScriptProcessor(){remoteProcessor={connect(v){return v;},disconnect(){},onaudioprocess:null};remoteProcessors.push(remoteProcessor);return remoteProcessor;}
  createGain(){return {gain:{value:1},connect(){},disconnect(){}};}
  resume(){return Promise.resolve();}
  close(){return Promise.resolve();}
}
class FakeMediaStream {constructor(tracks){this.tracks=tracks;}}
const sandbox=vm.createContext({
  window,
  globalThis:{__CHAT2API_VOICE_MAIN_V1__:currentVoiceState},
  AudioContext:FakeAudioContext,
  MediaStream:FakeMediaStream,
  atob:s=>Buffer.from(s,"base64").toString("binary"),
  btoa:s=>Buffer.from(s,"binary").toString("base64"),
  crypto:{randomUUID:()=>"v194"},
  performance:{now:()=>0},
  setInterval(fn){const id=++timerSequence;timers.set(id,fn);return id;},
  clearInterval(id){timers.delete(id);},
  Uint8Array,Float32Array,DataView,Promise,Date,Math,Set,Array,String,Number,
});
vm.runInContext(liveMain,sandbox);
assert.equal(listeners.length,1);
function send(type, more={}) {
  listeners[0]({source:window,data:{source:"chat2api-isolated",
    type,request_id:"live-v194",session_id:"session-v194",...more}});
}
function tick() { for(const fn of [...timers.values()])fn(); }
async function flush(){await new Promise(resolve=>setImmediate(resolve));}

send("voice.live.prepare");
currentVoiceState.requestId="another-request";
tick();await flush();
assert.equal(realAudioSource,null, "wrong request WebRTC track cannot be captured");
currentVoiceState.requestId="live-v194";
tick();await flush();
assert.ok(realAudioSource instanceof FakeMediaStream,
  "live remote source must attach directly to the WebRTC track without MediaRecorder");
assert.equal(realAudioSource.tracks[0],currentVoiceState.remoteTrack);
assert.ok(posted.some(x=>x.type==="voice.live.remote.ready"));

// The bridge must detach the old processor and attach a replacement track
// after WebRTC renegotiation; a stale processor may not emit duplicate PCM.
const oldProcessor = remoteProcessor;
firstTrack.end();
const replacementTrack = makeTrack("replacement");
currentVoiceState.remoteTrack = replacementTrack;
tick();await flush();
assert.equal(remoteSources.length,2);
assert.equal(remoteSources[1],replacementTrack);
assert.notEqual(remoteProcessor,oldProcessor);
assert.equal(oldProcessor.onaudioprocess,null);

const audio = new Float32Array(2048).fill(0.18);
remoteProcessor.onaudioprocess({inputBuffer:{getChannelData:()=>audio}});
assert.ok(posted.some(x=>x.type==="voice.live.response.started"));
const packet=posted.find(x=>x.type==="voice.live.audio");
assert.ok(packet && packet.pcm_base64 && packet.sample_rate===24000);
assert.ok(Buffer.from(packet.pcm_base64,"base64").byteLength>0);

// 44.1 kHz -> 24 kHz is a fractional ratio. Resampling each 2048-sample
// callback independently would round every frame and accumulate clock drift.
const beforeLongRun = posted.length;
const blockCount = 500;
for (let index = 0; index < blockCount; index += 1) {
  remoteProcessor.onaudioprocess({inputBuffer:{getChannelData:()=>audio}});
}
const longRunPackets = posted.slice(beforeLongRun).filter(item=>item.type==="voice.live.audio");
const longRunSamples = longRunPackets.reduce((sum,item)=>
  sum + Buffer.from(item.pcm_base64,"base64").byteLength / 2,0);
const expectedLongRunSamples = audio.length * blockCount * 24000 / 44100;
assert.ok(Math.abs(longRunSamples - expectedLongRunSamples) <= 2,
  "remote PCM resampling must preserve the aggregate 44.1 kHz to 24 kHz clock");

// Small PCM WebSocket frames must be played in arrival order by the fake mic,
// despite each AudioContext.resume() being asynchronous.
const pcm=Buffer.alloc(640);
pcm.writeInt16LE(4200,0);
const encoded=pcm.toString("base64");
send("voice.live.audio.push",{pcm_base64:encoded,sample_rate:16000});
send("voice.live.audio.push",{pcm_base64:encoded,sample_rate:16000});
send("voice.live.audio.push",{pcm_base64:encoded,sample_rate:16000});
await flush();await flush();await flush();
assert.equal(sources.length,3);
assert.ok(sources[0]<sources[1] && sources[1]<sources[2], "PCM frames in same order as received");
assert.equal(currentVoiceState.recorder,null, "live mode must not start batch recorder");
assert.deepEqual(currentVoiceState.recorderChunks,[], "live stream must not touch recorder chunks");
send("voice.live.stop");
assert.equal(timers.size,0, "stop terminates PCM and speech timers");
console.log("Live Voice binds WebRTC track directly, streams PCM and preserves ordered microphone input");
