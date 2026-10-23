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

const listeners = [];
const posted = [];
const timers = new Map();
let timerSequence = 0;
let realAudioSource = null;
let remoteProcessor = null;
const sources = [];
const window = {
  addEventListener(type, callback) { if (type === "message") listeners.push(callback); },
  postMessage(message) { posted.push(message); },
};
const currentVoiceState = {
  requestId: "live-v194",
  remoteTrack: { kind: "audio", readyState: "live" },
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
  constructor(){this.state="running";this.sampleRate=48000;this.destination={};}
  createMediaStreamSource(stream){realAudioSource=stream;return {connect(){},disconnect(){}};}
  createScriptProcessor(){remoteProcessor={connect(v){return v;},disconnect(){},onaudioprocess:null};return remoteProcessor;}
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

const audio = new Float32Array(2048).fill(0.18);
remoteProcessor.onaudioprocess({inputBuffer:{getChannelData:()=>audio}});
assert.ok(posted.some(x=>x.type==="voice.live.response.started"));
const packet=posted.find(x=>x.type==="voice.live.audio");
assert.ok(packet && packet.pcm_base64 && packet.sample_rate===24000);
assert.ok(Buffer.from(packet.pcm_base64,"base64").byteLength>0);

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
