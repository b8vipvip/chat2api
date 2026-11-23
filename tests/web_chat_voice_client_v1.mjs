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
    if (constraints.audio) assert.ok(audioContexts.length > 0,"AudioContext must unlock before microphone permission/network waits");
    const track = {stopped:false,stop(){this.stopped=true;stoppedTracks.push(this);}};
    return {
      constraints,
      getTracks:() => [track],
      track,
    };
  },
};
const intervalCallbacks = new Map();
let nextIntervalId = 0;
const timers = {
  setTimeout,
  clearTimeout,
  setInterval(fn,ms) {
    const id = ++nextIntervalId;
    intervalCallbacks.set(id,{fn,ms});
    return id;
  },
  clearInterval(id) { intervalCallbacks.delete(id); },
};
let clock = 1000;
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
let beforeConnectCalls = 0;
const client = api.create({
  apiToken:"managed-key",
  beforeConnect:async()=>{beforeConnectCalls+=1;assert.ok(audioContexts.length>0);},
  fetcher,
  WebSocketImpl:FakeWebSocket,
  AudioContextImpl:FakeAudioContext,
  mediaDevices,
  location:window.location,
  timers,
  now:()=>clock,
  onState:(status,detail)=>states.push({status,detail}),
  onEvent:event=>events.push(event),
});
await client.start();
assert.equal(beforeConnectCalls,1);
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
await client.startCamera(video);
// Reopening the camera immediately must not exceed the one-frame-per-second cap.
await new Promise(resolve=>setTimeout(resolve,10));
let frames = socket.sent.map(value=>typeof value==="string" ? JSON.parse(value) : null)
  .filter(value=>value?.type==="input_video.frame");
assert.equal(frames.length,1);
clock = 2000;
const cameraTimer = [...intervalCallbacks.entries()].find(([,value])=>value.ms===1000);
assert.ok(cameraTimer);
cameraTimer[1].fn();
await new Promise(resolve=>setTimeout(resolve,10));
await new Promise(resolve=>setImmediate(resolve));
frames = socket.sent.map(value=>typeof value==="string" ? JSON.parse(value) : null)
  .filter(value=>value?.type==="input_video.frame");
assert.equal(frames.length,2);
assert.equal(frames[1].sequence,1,"camera restart must not reset server-owned sequence");
assert.equal(frames[1].timestamp_ms,1000,"camera restart must preserve monotonic session timestamps and one-second pacing");

await client.stop();
assert.equal(client.isActive(),false);
assert.ok(socket.sent.some(value=>typeof value==="string" && JSON.parse(value).type==="session.finish"));
assert.ok(audioContexts[0].closed);
assert.ok(stoppedTracks.length >= 2);
assert.equal(intervalCallbacks.size,0,"heartbeat and camera timers must be cleared");
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
  state:{voiceAssistantLine:null,voiceAssistantPersisted:false},
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
voiceUi.handleVoiceEvent({type:"response.text.replace",text:"修订后的部分回复"});
assert.equal(clientContext.state.voiceAssistantLine.textContent,"修订后的部分回复",
  "a corrected in-progress snapshot must replace rather than append duplicate text");
voiceUi.handleVoiceEvent({type:"response.done",text:"完整的最终语音回复"});
assert.equal(transcript.children[0].children[1].textContent,"完整的最终语音回复");
assert.equal(clientContext.state.voiceAssistantLine,null);

const voiceConversation = {
  id:"conversation-voice-history",
  title:"新对话",
  messages:[],
  updatedAt:"",
};
const historyState = {
  voiceAssistantLine:null,
  voiceAssistantPersisted:false,
  voiceConversationId:voiceConversation.id,
  conversations:[voiceConversation],
  activeConversationId:voiceConversation.id,
};
let persisted = 0;
let rendered = 0;
let nextMessageId = 0;
const historyContext = vm.createContext({
  state:historyState,
  MAX_MESSAGE_CHARS:8000,
  MAX_MESSAGES:30,
  $:id=>id==="voiceTranscript" ? transcript : new FakeElement("status"),
  document:{createElement:tag=>new FakeElement(tag)},
  id:()=> "voice-message-"+(++nextMessageId),
  currentConversation:()=>historyState.conversations.find(row=>row.id===historyState.activeConversationId)||null,
  autoTitle:row=>{if(row.title==="新对话")row.title="语音会话";},
  saveHistory:()=>{persisted+=1;},
  renderAll:()=>{rendered+=1;},
  String,
  Date,
});
const historyUi = vm.runInContext(
  chatClientSource.slice(appendStart,appendEnd) + "\n({persistVoiceTranscript,handleVoiceEvent})",
  historyContext,
);
historyUi.handleVoiceEvent({type:"transcript.final",text:"请帮我总结今天的会议"});
historyUi.handleVoiceEvent({type:"response.text.delta",delta:"会议主要讨论"});
historyUi.handleVoiceEvent({type:"response.done",text:"会议主要讨论项目进度和下周安排。"});
assert.deepEqual(voiceConversation.messages.map(row=>row.role),["user","assistant"]);
assert.deepEqual(voiceConversation.messages.map(row=>row.content),[
  "请帮我总结今天的会议","会议主要讨论项目进度和下周安排。",
]);
assert.equal(voiceConversation.title,"语音会话");
assert.equal(persisted,2,"each final voice turn must be saved for server sync");
assert.equal(rendered,2,"active conversation should render persisted voice turns");
assert.equal(historyState.voiceAssistantPersisted,true,
  "response.done must mark the final assistant transcript as already persisted");

// A barge-in ends the current assistant turn but keeps the Voice session open.
// Persist that partial turn separately before accepting the next response.
historyUi.handleVoiceEvent({type:"response.text.delta",delta:"中断前的部分回复"});
historyUi.handleVoiceEvent({type:"response.interrupted",reason:"barge_in"});
assert.equal(voiceConversation.messages.length,3);
assert.equal(voiceConversation.messages[2].content,"中断前的部分回复");
assert.equal(voiceConversation.messages[2].recoveryStatus,"voice_partial");
assert.equal(historyState.voiceAssistantLine,null);
historyUi.handleVoiceEvent({type:"response.text.delta",delta:"下一轮新回复"});
assert.equal(historyState.voiceAssistantLine.textContent,"下一轮新回复",
  "a new response after barge-in must not append to the previous partial line");
historyUi.handleVoiceEvent({type:"response.done",text:"下一轮完整回复"});
assert.equal(voiceConversation.messages.length,4);
assert.equal(voiceConversation.messages[3].content,"下一轮完整回复");
assert.notEqual(voiceConversation.messages[3].recoveryStatus,"voice_partial");

// A session/track stop may deliver its only final partial snapshot in the
// interruption event, before the 250 ms DOM poller has emitted any delta.
historyUi.handleVoiceEvent({type:"response.created",response_id:"response-no-delta"});
historyUi.handleVoiceEvent({
  type:"response.interrupted",
  reason:"session_stop",
  text:"最后一个未被轮询捕获的部分片段",
});
assert.equal(voiceConversation.messages.length,5);
assert.equal(voiceConversation.messages[4].content,"最后一个未被轮询捕获的部分片段");
assert.equal(voiceConversation.messages[4].recoveryStatus,"voice_partial");
assert.equal(historyState.voiceAssistantPersisted,false,
  "the persistence guard must reset after a completed turn before the next interruption");

const setVoiceUiStart = chatClientSource.indexOf("  function setVoiceUi(");
const setVoiceUiEnd = chatClientSource.indexOf("  function appendVoiceTranscript(",setVoiceUiStart);
assert.ok(setVoiceUiStart>=0 && setVoiceUiEnd>setVoiceUiStart);
function closeElements() {
  const element = () => ({classList:{add(){},toggle(){}},textContent:""});
  return {
    voicePanel:element(),
    voiceStatus:element(),
    voiceCameraPreview:element(),
    voiceCameraButton:element(),
  };
}
const closeState = {
  voiceStarting:false,
  voiceActive:true,
  voicePanelVisible:true,
  voiceAssistantLine:{textContent:"尚未完成的回复"},
  voiceAssistantPersisted:false,
  voiceClient:{},
  voiceConversationId:"conversation-voice-history",
  voiceAuthTimer:null,
};
const partialWrites = [];
const closeContext = vm.createContext({
  state:closeState,
  $:id=>closeElementsMap[id],
  window:{clearInterval(){}},
  persistVoiceTranscript:(role,value,recoveryStatus)=>{partialWrites.push({role,value,recoveryStatus});return true;},
  updateVoiceControls(){},
  resizeComposer(){},
  String,
});
const closeElementsMap = closeElements();
const closeUi = vm.runInContext(
  chatClientSource.slice(setVoiceUiStart,setVoiceUiEnd) + "\nsetVoiceUi",
  closeContext,
);
closeUi("closed");
assert.equal(partialWrites.length,1,"closing mid-response must persist the visible partial assistant text");
assert.equal(partialWrites[0].role,"assistant");
assert.equal(partialWrites[0].value,"尚未完成的回复");
assert.equal(partialWrites[0].recoveryStatus,"voice_partial",
  "interrupted Voice text must be visibly labeled as a partial reply in synchronized history");
assert.equal(closeState.voiceAssistantLine,null);
assert.equal(closeState.voiceConversationId,"");
assert.equal(closeState.voiceAssistantPersisted,false);

const completedCloseState = {
  ...closeState,
  voiceAssistantLine:null,
  voiceAssistantPersisted:true,
  voiceConversationId:"conversation-voice-history",
};
let duplicateCloseWrites = 0;
const completedCloseContext = vm.createContext({
  state:completedCloseState,
  $:id=>closeElementsMap[id],
  window:{clearInterval(){}},
  persistVoiceTranscript:()=>{duplicateCloseWrites+=1;return true;},
  updateVoiceControls(){},
  resizeComposer(){},
  String,
});
const completedCloseUi = vm.runInContext(
  chatClientSource.slice(setVoiceUiStart,setVoiceUiEnd) + "\nsetVoiceUi",
  completedCloseContext,
);
completedCloseUi("closed");
assert.equal(duplicateCloseWrites,0,"closing after response.done must not persist the same answer twice");

// A server-side camera ACK timeout must release the browser's one-frame
// in-flight guard so the next timestamped frame can be sent.
class NoAckWebSocket extends FakeWebSocket {
  send(value) {
    this.sent.push(value);
    if (typeof value !== "string") return;
    const payload = JSON.parse(value);
    if (payload.type === "session.start") {
      queueMicrotask(() => this.onmessage?.({data:JSON.stringify({
        type:"session.ready",session_id:"session-no-ack",
        supports:["input_text","input_media","input_video.frame"],
      })}));
    } else if (payload.type === "session.finish") {
      queueMicrotask(() => {
        this.onmessage?.({data:JSON.stringify({type:"session.closed",status:"completed"})});
        this.readyState = NoAckWebSocket.CLOSED;
        this.onclose?.();
      });
    }
  }
}
const noAckClient = api.create({
  apiToken:"managed-key",
  fetcher,
  WebSocketImpl:NoAckWebSocket,
  AudioContextImpl:FakeAudioContext,
  mediaDevices,
  location:window.location,
  timers,
  now:()=>clock,
  onEvent:event=>events.push(event),
});
await noAckClient.start();
const noAckSocket = NoAckWebSocket.last;
await noAckClient.startCamera(video);
await new Promise(resolve=>setTimeout(resolve,10));
await new Promise(resolve=>setImmediate(resolve));
let noAckFrames = noAckSocket.sent.map(value=>typeof value==="string" ? JSON.parse(value) : null)
  .filter(value=>value?.type==="input_video.frame");
assert.equal(noAckFrames.length,1);
noAckSocket.onmessage({data:JSON.stringify({
  type:"error",code:"LIVE_VIDEO_FRAME_ACK_TIMEOUT",item_id:noAckFrames[0].item_id,
})});
clock += 1000;
const noAckCameraTimer = [...intervalCallbacks.values()].find(value=>value.ms===1000);
assert.ok(noAckCameraTimer);
noAckCameraTimer.fn();
await new Promise(resolve=>setTimeout(resolve,10));
await new Promise(resolve=>setImmediate(resolve));
noAckFrames = noAckSocket.sent.map(value=>typeof value==="string" ? JSON.parse(value) : null)
  .filter(value=>value?.type==="input_video.frame");
assert.equal(noAckFrames.length,2,
  "camera capture must resume after the server releases a timed-out in-flight frame");
assert.equal(noAckFrames[1].sequence,1);
await noAckClient.stop();

console.log("web-chat-voice-client-v1-passed");
