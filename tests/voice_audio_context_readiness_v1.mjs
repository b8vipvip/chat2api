import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const voiceMain=readFileSync("chrome_extension/voice_main.js","utf8");
const liveMain=readFileSync("chrome_extension/voice_live_main.js","utf8");
const webClient=readFileSync("app/chat_voice_client_v1.js","utf8");
new vm.Script(voiceMain);
new vm.Script(liveMain);
new vm.Script(webClient);

function micHarness({resume="running",existing=false}={}) {
  const state={micContext:null,micDestination:null,micOscillator:null};
  const contexts=[];
  class FakeAudioContext {
    constructor(){
      this.state=resume==="closed"?"closed":"suspended";
      this.closed=false;
      this.oscillator=null;
      this.destination={stream:{getAudioTracks:()=>[{id:"synthetic-mic-track"}]}};
      contexts.push(this);
    }
    createMediaStreamDestination(){return this.destination;}
    createGain(){return {gain:{value:1},connect(){return this.destination;}};}
    createOscillator(){
      this.oscillator={frequency:{value:0},stopped:false,connect(){return this;},start(){},stop(){this.stopped=true;}};
      return this.oscillator;
    }
    async resume(){
      if(resume==="reject") throw new Error("resume denied");
      if(resume==="running") this.state="running";
    }
    async close(){this.closed=true;this.state="closed";}
  }
  if(existing){
    const old=new FakeAudioContext();
    old.state="suspended";
    state.micContext=old;
    state.micDestination=old.destination;
    state.micOscillator=old.oscillator;
  }
  const start=voiceMain.indexOf("  async function resumeSyntheticMicContext(");
  const end=voiceMain.indexOf("\n  const mediaDevices",start);
  assert.ok(start>=0 && end>start,"synthetic mic readiness helpers must remain extractable");
  const ensureSyntheticMic=vm.runInNewContext(
    voiceMain.slice(start,end)+"\nensureSyntheticMic",
    {state,AudioContext:FakeAudioContext,Promise,String,Error},
  );
  return {state,contexts,ensureSyntheticMic};
}

{
  const h=micHarness({resume:"running"});
  const stream=await h.ensureSyntheticMic();
  assert.equal(stream.getAudioTracks()[0].id,"synthetic-mic-track");
  assert.equal(h.state.micContext.state,"running");
  assert.ok(h.state.micDestination);
}

for(const mode of ["reject","suspended"]) {
  const h=micHarness({resume:mode});
  await assert.rejects(h.ensureSyntheticMic(),/AudioContext/);
  assert.equal(h.state.micContext,null,
    "a failed synthetic microphone must not retain a false-ready context");
  assert.equal(h.state.micDestination,null);
  assert.equal(h.state.micOscillator,null);
  assert.equal(h.contexts[0].closed,true);
  assert.equal(h.contexts[0].oscillator.stopped,true);
}

{
  const h=micHarness({resume:"reject",existing:true});
  await assert.rejects(h.ensureSyntheticMic(),/could not resume/);
  assert.equal(h.contexts.length,1,
    "a suspended existing microphone must not be returned as ready or silently replaced");
}

{
  const helperStart=voiceMain.indexOf("  async function resumeSyntheticMicContext(");
  const helperEnd=voiceMain.indexOf("\n  const mediaDevices",helperStart);
  const playStart=voiceMain.indexOf("  async function playPreparedInput()",helperEnd);
  const playEnd=voiceMain.indexOf("\n  window.addEventListener",playStart);
  assert.ok(helperStart>=0 && helperEnd>helperStart && playStart>helperEnd && playEnd>playStart);
  const events=[];
  const state={
    preparedInput:{base64:"AAAA",mime_type:"audio/wav"},
    micContext:{state:"suspended",async resume(){throw new Error("playback resume denied");}},
    micDestination:{},
  };
  const playPreparedInput=vm.runInNewContext(
    voiceMain.slice(helperStart,helperEnd)+
      "\n"+voiceMain.slice(playStart,playEnd)+
      "\nplayPreparedInput",
    {
      state,
      waitForRemoteTrackBeforeInput:async()=>1,
      ensureSyntheticMic:async()=>({}),
      decodeBase64:()=>new Uint8Array([0,0]),
      post:(type)=>events.push(type),
      performance:{now:()=>1},
      Promise,String,Error,Number,Math,
    },
  );
  await assert.rejects(playPreparedInput(),/playback resume denied/);
  assert.equal(events.includes("voice.input.started"),false,
    "prepared audio must not announce playback when its AudioContext is suspended");
}

{
  const state={micContext:{
    state:"suspended",
    async resume(){throw new Error("resume denied");},
  },micDestination:{}};
  const start=liveMain.indexOf("  async function waitForSyntheticMic(");
  const end=liveMain.indexOf("\n  async function pushInput(",start);
  assert.ok(start>=0 && end>start,"Live input readiness helper must remain extractable");
  const waitForSyntheticMic=vm.runInNewContext(
    liveMain.slice(start,end)+"\nwaitForSyntheticMic",
    {base:()=>state,delay:async()=>{},Date,String,Error,Promise},
  );
  await assert.rejects(waitForSyntheticMic(),/could not resume/);
}

{
  const events=[];
  const remoteTrack={kind:"audio",readyState:"live",addEventListener(){}};
  const live={prepared:true,requestId:"voice-remote-context",remoteProcessor:null,remoteTrack:null};
  class FakeRemoteContext {
    constructor(){this.state="suspended";this.sampleRate=48000;this.destination={};}
    createMediaStreamSource(){return {connect(){},disconnect(){}};}
    createScriptProcessor(){return {connect(){return this;},disconnect(){},onaudioprocess:null};}
    createGain(){return {gain:{value:0},connect(){return this;},disconnect(){}};}
    async resume(){throw new Error("remote context blocked");}
    async close(){}
  }
  const start=liveMain.indexOf("  async function attachRemoteStream(");
  const end=liveMain.indexOf("\n  function watchRemoteTrack(",start);
  assert.ok(start>=0 && end>start,"remote WebRTC audio attachment must remain extractable");
  const attachRemoteStream=vm.runInNewContext(
    liveMain.slice(start,end)+"\nattachRemoteStream",
    {
      live,AudioContext:FakeRemoteContext,Float32Array,
      rmsOf:()=>0,resampleStream:()=>new Float32Array(0),
      floatToPcm16Bytes:()=>new Uint8Array(0),encodeBase64:()=>"",post:(type)=>events.push(type),
      Promise,String,Error,Date,
    },
  );
  await assert.rejects(attachRemoteStream({getAudioTracks:()=>[remoteTrack]}),/could not resume/);
  assert.equal(events.includes("voice.live.remote.ready"),false,
    "a suspended remote output context must never satisfy Live audio readiness");
}

{
  const start=webClient.indexOf("    function playPcm(buffer) {");
  const end=webClient.indexOf("\n    function startMicrophonePipeline()",start);
  assert.ok(start>=0 && end>start,"Web Chat Live PCM playback guard must remain extractable");
  const events=[];
  let cleanupCalls=0;
  const playPcm=vm.runInNewContext(
    webClient.slice(start,end)+"\nplayPcm",
    {
      audioContext:{state:"suspended"},
      cleaned:false,
      event:payload=>events.push(payload),
      cleanup:async()=>{cleanupCalls+=1;},
      decodePcm16:()=>new Float32Array([0.1]),
      String,Error,Promise,Math,Number,ArrayBuffer,
    },
  );
  playPcm(new ArrayBuffer(2));
  assert.equal(events[0]?.type,"client.error");
  assert.match(events[0]?.message,/已暂停/);
  assert.equal(cleanupCalls,1,
    "a suspended Web Chat output context must not silently discard all future PCM playback");
}
assert.ok(voiceMain.includes("synthetic microphone AudioContext is not running"));
assert.ok(liveMain.includes("remote audio AudioContext is not running"));
assert.ok(webClient.includes("浏览器音频输出上下文未进入运行状态"));
console.log("voice-audio-context-readiness-v1-passed");
