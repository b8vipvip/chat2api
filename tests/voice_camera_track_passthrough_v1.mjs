import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/voice_main.js", "utf8");
new vm.Script(source);

const nativeVideoTrack = {kind:"video",readyState:"live",id:"native-camera-track",stop(){this.readyState="ended";}};
const audioTrack = {kind:"audio",readyState:"live",id:"synthetic-microphone-track"};
let generatedVideoTrack = null;
let captureFrameRate = 0;
let drawCalls = 0;
const nativeCalls = [];
const posted = [];
const listeners = [];

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

class FakeRTCPeerConnection {
  addEventListener() {}
  addTrack(track) { return {track}; }
  addTransceiver(trackOrKind) { return {sender:{track:trackOrKind}}; }
}
class FakeRTCRtpSender {
  constructor(){this.track=null;}
  replaceTrack(track){this.track=track;return Promise.resolve();}
}

const mediaDevices = {
  async getUserMedia(constraints) {
    nativeCalls.push(constraints);
    if (constraints?.video) return new FakeMediaStream([nativeVideoTrack]);
    return new FakeMediaStream([audioTrack]);
  },
};
const window = {
  addEventListener(type, callback) {
    if (type === "message") listeners.push(callback);
  },
  postMessage(message) { posted.push(message); },
};
const canvasContext = {
  fillStyle:"",
  fillRect(){},
  drawImage(){drawCalls+=1;},
};
const fakeCanvas = {
  width:0,
  height:0,
  getContext(){return canvasContext;},
  captureStream(fps) {
    captureFrameRate = fps;
    generatedVideoTrack = {
      kind:"video",
      readyState:"live",
      id:"chat2api-synthetic-video-track",
      contentHint:"",
      stop(){this.readyState="ended";},
    };
    return new FakeMediaStream([generatedVideoTrack]);
  },
};
const sandbox = vm.createContext({
  window,
  navigator:{mediaDevices},
  MediaStream:FakeMediaStream,
  RTCPeerConnection:FakeRTCPeerConnection,
  RTCRtpSender:FakeRTCRtpSender,
  AudioContext:FakeAudioContext,
  document:{visibilityState:"visible",hasFocus:()=>true,createElement:tag=>{
    assert.equal(tag,"canvas");
    return fakeCanvas;
  }},
  performance:{now:()=>1},
  setInterval:()=>1,
  clearInterval:()=>{},
  atob:value=>Buffer.from(value,"base64").toString("binary"),
  btoa:value=>Buffer.from(value,"binary").toString("base64"),
  crypto:{randomUUID:()=>"camera-test"},
  Blob,
  createImageBitmap:async()=>({width:640,height:480,close(){}}),
  Uint8Array,Float32Array,DataView,Promise,Date,Math,Set,Array,String,Number,JSON,Object,Error,
});
vm.runInContext(source,sandbox);

assert.equal(sandbox.__CHAT2API_VOICE_MAIN_V1__.micHookReady,true,
  "the native getUserMedia hook must be installed before camera capture");
assert.equal(sandbox.__CHAT2API_VOICE_MAIN_V1__.peerHookReady,true,
  "the RTCPeerConnection hook must be installed before Voice creates its sender");

function dispatch(type, data = {}) {
  for (const listener of listeners) listener({
    source:window,
    data:{
      source:"chat2api-isolated",
      type,
      request_id:"camera-test-request",
      session_id:"camera-test-session",
      ...data,
    },
  });
}

dispatch("voice.prepare",{mode:"live"});
dispatch("voice.live.video.prepare");
assert.equal(captureFrameRate,5,"the synthetic camera must expose a continuous 5 FPS MediaStreamTrack");
assert.ok(generatedVideoTrack,"a synthetic camera track must be created");
assert.ok(posted.some(message=>message.type==="voice.live.video.prepared"));

const videoConstraints = {width:{ideal:1280},height:{ideal:720},frameRate:{ideal:30}};
const combined = await mediaDevices.getUserMedia({audio:true,video:videoConstraints});
assert.deepEqual(combined.getAudioTracks(),[audioTrack],
  "ChatGPT Live receives the synthetic microphone track");
assert.deepEqual(combined.getVideoTracks(),[generatedVideoTrack],
  "ChatGPT Live receives the Web Chat user's synthetic video track, not the Worker webcam");
assert.equal(nativeCalls.length,0,
  "Live video must never request the Worker browser's native camera permission");
assert.ok(posted.some(message=>message.type==="voice.live.video.track.provided"));

const peer = new sandbox.RTCPeerConnection();
peer.addTrack(generatedVideoTrack);
assert.ok(posted.some(message=>message.type==="voice.live.video.sender.attached"),
  "the bridge must verify that ChatGPT actually attached the synthetic camera track");
const attachedBeforeReplace = posted.filter(message=>message.type==="voice.live.video.sender.attached").length;
const sender = new FakeRTCRtpSender();
await sender.replaceTrack(generatedVideoTrack);
assert.equal(sender.track,generatedVideoTrack);
assert.equal(posted.filter(message=>message.type==="voice.live.video.sender.attached").length,
  attachedBeforeReplace + 1,
  "WebRTC sender replacement after renegotiation must be observed as a real track attachment");

dispatch("voice.live.video.frame",{
  base64:"AA==",
  mime_type:"image/jpeg",
  sequence:0,
  timestamp_ms:1000,
});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(drawCalls,1,"each accepted JPEG frame must update the synthetic camera canvas");
assert.ok(posted.some(message=>
  message.type==="voice.live.video.frame.received" &&
  message.sequence===0 && message.timestamp_ms===1000),
"the Worker must acknowledge the exact rendered frame sequence and timestamp");

dispatch("voice.live.video.frame",{
  base64:"AA==",
  mime_type:"image/jpeg",
  sequence:0,
  timestamp_ms:1001,
});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(drawCalls,1,"duplicate frame sequence must never overwrite a newer camera frame");
assert.ok(posted.some(message=>
  message.type==="voice.live.video.error" && message.sequence===0),
"duplicate sequence must return a bounded frame-level error");

const audioOnly = await mediaDevices.getUserMedia({audio:true,video:false});
assert.deepEqual(audioOnly.getAudioTracks(),[audioTrack]);
assert.deepEqual(audioOnly.getVideoTracks(),[],
  "audio-only Voice sessions must not request camera permission");
assert.equal(nativeCalls.length,0);

dispatch("voice.reset");
assert.equal(generatedVideoTrack.readyState,"ended",
  "stopping the Voice session must release the synthetic video track");

// Non-Live Voice keeps the existing native-camera passthrough behavior.
dispatch("voice.prepare",{request_id:"speech-camera-request",mode:"speech"});
const speechCombined = await mediaDevices.getUserMedia({audio:true,video:videoConstraints});
assert.deepEqual(speechCombined.getAudioTracks(),[audioTrack]);
assert.deepEqual(speechCombined.getVideoTracks(),[nativeVideoTrack]);
assert.deepEqual(JSON.parse(JSON.stringify(nativeCalls[0])),{audio:false,video:videoConstraints},
  "non-Live speech capture must retain the original native camera constraints");
const speechCameraOnly = await mediaDevices.getUserMedia({video:true});
assert.deepEqual(speechCameraOnly.getVideoTracks(),[nativeVideoTrack]);
assert.equal(nativeCalls.length,2);

console.log("Voice main-world Live synthetic WebRTC camera track is wired and bounded");
