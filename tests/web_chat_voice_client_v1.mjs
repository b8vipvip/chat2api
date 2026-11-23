import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync("app/chat_voice_client_v1.js","utf8");
const chatClientSource = readFileSync("app/chat_client_v1.js","utf8");
new vm.Script(source);
new vm.Script(chatClientSource);

class FakeWebSocket {
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static last = null;
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.binaryType = "";
    FakeWebSocket.last = this;
    setTimeout(() => {
      this.readyState = FakeWebSocket.OPEN;
      this.onopen?.();
    },0);
  }
  send(value) {
    this.sent.push(value);
    if (typeof value !== "string") return;
    const payload = JSON.parse(value);
    if (payload.type === "session.start") {
      queueMicrotask(() => this.onmessage?.({data:JSON.stringify({
        type:"session.ready",
        session_id:"session-webchat",
        supports:["input_text","input_media","input_video.frame"],
      })}));
    } else if (payload.type === "input_video.frame") {
      queueMicrotask(() => this.onmessage?.({data:JSON.stringify({
        type:"input_video.frame.sent",
        item_id:payload.item_id,
        sequence:payload.sequence,
        timestamp_ms:payload.timestamp_ms,
        attachment_count:1,
      })}));
    } else if (payload.type === "session.finish") {
      queueMicrotask(() => {
        this.onmessage?.({data:JSON.stringify({type:"session.closed",status:"completed"})});
        this.readyState = FakeWebSocket.CLOSED;
        this.onclose?.();
      });
    }
  }
  close() {
    if (this.readyState >= FakeWebSocket.CLOSING) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
}

const processors = [];
const playbackSources = [];
const audioContexts = [];
class FakeAudioContext {
  constructor() {
    this.sampleRate = 48000;
    this.currentTime = 0;
    this.destination = {};
    this.closed = false;
    audioContexts.push(this);
  }
  async resume() {}
  async close() { this.closed = true; }
  createMediaStreamSource(stream) {
    return {stream,connect(){},disconnect(){}};
  }
  createScriptProcessor() {
    const processor = {onaudioprocess:null,connect(){},disconnect(){}};
    processors.push(processor);
    return processor;
  }
  createGain() {
    return {gain:{value:1},connect(){},disconnect(){}};
  }
  createBuffer(_channels,length,rate) {
    return {duration:length/rate,copyToChannel(samples){this.samples=new Float32Array(samples);}};
  }
  createBufferSource() {
    const source = {buffer:null,connect(){},start(at){this.startedAt=at;},stop(){},onended:null};
    playbackSources.push(source);
    return source;
  }
}

const stoppedTracks = [];
let mediaCall = 0;
const mediaDevices = {
  async getUserMedia(constraints) {
    mediaCall += 1;
    const track = {stopped:false,stop(){this.stopped=true;stoppedTracks.push(this);}};
    return {
      constraints,
      getTracks:() => [track],
      track,
    };
  },
};
const timers = {
  setTimeout,
  clearTimeout,
  setInterval(fn,ms) {
    const id = setInterval(fn,ms);
    id.unref?.();
    return id;
  },
  clearInterval,
};
const video = {
  videoWidth:640,
  videoHeight:480,
  srcObject:null,
  async play(){},
  pause(){},
};
const fakeDocument = {
  createElement(tag) {
    assert.equal(tag,"canvas");
    return {
      width:0,height:0,
      getContext() { return {drawImage(){}}; },
      toBlob(callback,type) {
        callback(new Blob([new Uint8Array(128)],{type}));
      },
    };
  },
};
const uploads = [];
const fetcher = async (url,options={}) => {
  if (url === "/v1/audio/realtime/sessions") {
    assert.equal(options.method,"POST");
    assert.equal(options.headers.Authorization,"Bearer managed-key");
    return {ok:true,status:200,json:async()=>({
      websocket_path:"/v1/audio/realtime?session_token=one-time-token",
    })};
  }
  if (url === "/v1/files") {
    const body = JSON.parse(options.body);
    uploads.push(body);
    assert.equal(body.purpose,"vision");
    assert.equal(body.mime_type,"image/jpeg");
    assert.ok(body.data_base64.length > 0);
    return {ok:true,status:200,json:async()=>({id:"file_abcdefgh"})};
  }
  throw new Error("unexpected fetch: "+url);
};
const window = {
  WebSocket:FakeWebSocket,
  AudioContext:FakeAudioContext,
  navigator:{mediaDevices},
  location:{href:"https://chat.example/chat"},
  document:fakeDocument,
  fetch:fetcher,
  btoa:value => Buffer.from(value,"binary").toString("base64"),
  setTimeout,clearTimeout,setInterval,clearInterval,
};
const sandbox = vm.createContext({
  window,
  URL,
  Blob,
  ArrayBuffer,
  DataView,
  Uint8Array,
  Float32Array,
  Number,
  String,
  Math,
  Set,
  Promise,
  Date,
  TypeError,
  Error,
  btoa:window.btoa,
});
vm.runInContext(source,sandbox);
const api = window.Chat2APIVoiceClientV1;
assert.ok(api);

const samples = new Float32Array(480).fill(0.25);
const pcm = api.resamplePcm16(samples,48000,16000);
assert.equal(pcm.byteLength,160*2);
const decoded = api.decodePcm16(pcm);
assert.equal(decoded.length,160);
assert.ok(decoded.every(value => value > 0.24 && value < 0.26));
const stream48 = {pending:new Float32Array(0),position:0};
let samples48 = 0;
for(let index=0;index<12;index+=1) {
  samples48 += api.resamplePcm16Stream(new Float32Array(4096).fill(0.1),48000,stream48).byteLength/2;
}
assert.equal(samples48,16384,"48 kHz callback boundaries must not accumulate resampling drift");
const stream441 = {pending:new Float32Array(0),position:0};
let samples441 = 0;
for(let index=0;index<10;index+=1) {
  samples441 += api.resamplePcm16Stream(new Float32Array(4410).fill(0.1),44100,stream441).byteLength/2;
}
assert.equal(samples441,16000,"44.1 kHz callback boundaries must preserve the 16 kHz clock");

const states = [];
const events = [];
const client = api.create({
  apiToken:"managed-key",
  fetcher,
  WebSocketImpl:FakeWebSocket,
  AudioContextImpl:FakeAudioContext,
  mediaDevices,
  location:window.location,
  timers,
  onState:(status,detail)=>states.push({status,detail}),
  onEvent:event=>events.push(event),
});
await client.start();
assert.equal(client.isActive(),true);
const socket = FakeWebSocket.last;
assert.equal(socket.binaryType,"arraybuffer");
assert.match(socket.url,/wss:\/\/chat\.example\/v1\/audio\/realtime/);
assert.ok(!socket.url.includes("managed-key"),"the long-lived API Key must never appear in the WebSocket URL");
assert.ok(socket.url.includes("one-time-token"),"only the short-lived one-time session token belongs in the URL");
assert.ok(socket.sent.some(value=>typeof value==="string" && JSON.parse(value).type==="session.start"));

const input = new Float32Array(4096).fill(0.1);
processors[0].onaudioprocess({inputBuffer:{getChannelData:()=>input}});
assert.ok(socket.sent.some(value=>value instanceof ArrayBuffer && value.byteLength > 0));

const output = new ArrayBuffer(640);
const outputView = new DataView(output);
for(let i=0;i<output.byteLength;i+=2) outputView.setInt16(i,2400,true);
socket.onmessage({data:output});
assert.equal(playbackSources.length,1);
assert.ok(playbackSources[0].startedAt >= 0);

await client.startCamera(video);
await new Promise(resolve=>setTimeout(resolve,10));
await new Promise(resolve=>setImmediate(resolve));
const firstFrame = socket.sent.map(value=>typeof value==="string" ? JSON.parse(value) : null)
  .find(value=>value?.type==="input_video.frame");
assert.ok(firstFrame);
assert.equal(firstFrame.sequence,0);
assert.equal(firstFrame.file_id,"file_abcdefgh");
assert.equal(uploads.length,1);
assert.equal(client.isCameraActive(),true);
assert.ok(events.some(value=>value.type==="input_video.frame.queued"));

client.stopCamera();
assert.equal(client.isCameraActive(),false);
await new Promise(resolve=>setTimeout(resolve,3));
await client.startCamera(video);
await new Promise(resolve=>setTimeout(resolve,10));
await new Promise(resolve=>setImmediate(resolve));
const frames = socket.sent.map(value=>typeof value==="string" ? JSON.parse(value) : null)
  .filter(value=>value?.type==="input_video.frame");
assert.equal(frames.length,2);
assert.equal(frames[1].sequence,1,"camera restart must not reset server-owned sequence");
assert.ok(frames[1].timestamp_ms > frames[0].timestamp_ms,"camera restart must keep monotonic session timestamps");

await client.stop();
assert.equal(client.isActive(),false);
assert.ok(socket.sent.some(value=>typeof value==="string" && JSON.parse(value).type==="session.finish"));
assert.ok(audioContexts[0].closed);
assert.ok(stoppedTracks.length >= 2);
assert.ok(states.some(value=>value.status==="ready"));
assert.ok(states.some(value=>value.status==="closed"));

const deniedStates = [];
const denied = api.create({
  apiToken:"managed-key",
  fetcher,
  WebSocketImpl:FakeWebSocket,
  AudioContextImpl:FakeAudioContext,
  mediaDevices:{getUserMedia:async()=>{throw new Error("permission denied");}},
  location:window.location,
  timers,
  onState:status=>deniedStates.push(status),
});
await assert.rejects(denied.start(),/permission denied/);
assert.ok(deniedStates.includes("closed"),"failed startup must release resources and return to idle state");

const externalSocket = api.create({
  apiToken:"managed-key",
  fetcher:async()=>({ok:true,status:200,json:async()=>({
    websocket_path:"wss://attacker.example/v1/audio/realtime?session_token=bad",
  })}),
  WebSocketImpl:FakeWebSocket,
  AudioContextImpl:FakeAudioContext,
  mediaDevices,
  location:window.location,
  timers,
});
await assert.rejects(externalSocket.start(),/无法创建 GPT-Live 会话|same-origin/i);

// The main Web Chat panel must create an assistant transcript node on the
// first delta and replace partial deltas with the authoritative response.done text.
class FakeElement {
  constructor(tag) { this.tag=tag;this.className="";this.children=[];this.textContent=""; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node);return node; }
  remove() {}
}
const transcript = {
  children:[],
  scrollTop:0,
  scrollHeight:0,
  get childElementCount(){return this.children.length;},
  get firstElementChild(){return this.children[0] || null;},
  appendChild(node){this.children.push(node);return node;},
};
const clientContext = vm.createContext({
  state:{voiceAssistantLine:null},
  $:id=>id==="voiceTranscript" ? transcript : new FakeElement("status"),
  document:{createElement:tag=>new FakeElement(tag)},
  String,
});
const appendStart = chatClientSource.indexOf("  function appendVoiceTranscript(");
const appendEnd = chatClientSource.indexOf("  async function startVoiceCall(",appendStart);
assert.ok(appendStart>=0 && appendEnd>appendStart);
const voiceUi = vm.runInContext(
  chatClientSource.slice(appendStart,appendEnd) + "\n({appendVoiceTranscript,handleVoiceEvent})",
  clientContext,
);
voiceUi.handleVoiceEvent({type:"response.text.delta",delta:"部分"});
assert.equal(clientContext.state.voiceAssistantLine.textContent,"部分");
voiceUi.handleVoiceEvent({type:"response.done",text:"完整的最终语音回复"});
assert.equal(transcript.children[0].children[1].textContent,"完整的最终语音回复");
assert.equal(clientContext.state.voiceAssistantLine,null);
console.log("web-chat-voice-client-v1-passed");
