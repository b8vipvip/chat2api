import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/voice_main.js", "utf8");
new vm.Script(source);

const videoTrack = {kind:"video",readyState:"live",id:"native-camera-track"};
const audioTrack = {kind:"audio",readyState:"live",id:"synthetic-microphone-track"};
const calls = [];

class FakeMediaStream {
  constructor(tracks = []) { this.tracks = [...tracks]; }
  getTracks() { return [...this.tracks]; }
  getAudioTracks() { return this.tracks.filter(track => track.kind === "audio"); }
  getVideoTracks() { return this.tracks.filter(track => track.kind === "video"); }
}

class FakeAudioContext {
  constructor() { this.state = "running"; }
  createMediaStreamDestination() { return {stream:new FakeMediaStream([audioTrack])}; }
  createGain() { return {gain:{value:1},connect(){return this;}}; }
  createOscillator() {
    return {frequency:{value:0},connect(){return this;},start(){},stop(){}};
  }
  resume() { return Promise.resolve(); }
  close() { return Promise.resolve(); }
}

const mediaDevices = {
  async getUserMedia(constraints) {
    calls.push(constraints);
    if (constraints?.video) return new FakeMediaStream([videoTrack]);
    return new FakeMediaStream([audioTrack]);
  },
};
const listeners = [];
const window = {
  addEventListener(type, callback) {
    if (type === "message") listeners.push(callback);
  },
  postMessage() {},
};
const sandbox = vm.createContext({
  window,
  navigator:{mediaDevices},
  MediaStream:FakeMediaStream,
  AudioContext:FakeAudioContext,
  document:{visibilityState:"visible",hasFocus:()=>true},
  performance:{now:()=>1},
  setInterval:()=>1,
  clearInterval:()=>{},
  atob:value=>Buffer.from(value,"base64").toString("binary"),
  btoa:value=>Buffer.from(value,"binary").toString("base64"),
  crypto:{randomUUID:()=>"camera-test"},
  Uint8Array,Float32Array,DataView,Promise,Date,Math,Set,Array,String,Number,
});
vm.runInContext(source,sandbox);

assert.equal(sandbox.__CHAT2API_VOICE_MAIN_V1__.micHookReady,true,
  "the native getUserMedia hook must be installed before camera capture");
sandbox.__CHAT2API_VOICE_MAIN_V1__.requestId = "camera-test-request";

const videoConstraints = {width:{ideal:1280},height:{ideal:720},frameRate:{ideal:30}};
const combined = await mediaDevices.getUserMedia({audio:true,video:videoConstraints});
assert.deepEqual(combined.getAudioTracks(),[audioTrack],
  "ChatGPT's audio sender receives the synthetic microphone track");
assert.deepEqual(combined.getVideoTracks(),[videoTrack],
  "ChatGPT's native camera track must pass through unchanged");
assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{audio:false,video:videoConstraints},
  "the browser must still acquire the real camera with the original constraints");

const audioOnly = await mediaDevices.getUserMedia({audio:true,video:false});
assert.deepEqual(audioOnly.getAudioTracks(),[audioTrack]);
assert.deepEqual(audioOnly.getVideoTracks(),[],
  "audio-only Voice sessions must not request camera permission");
assert.equal(calls.length,1);

const cameraOnly = await mediaDevices.getUserMedia({video:true});
assert.deepEqual(cameraOnly.getVideoTracks(),[videoTrack],
  "video-only camera acquisition must retain the native track");
assert.equal(calls.length,2);

console.log("Voice main-world microphone substitution preserves native camera tracks");
