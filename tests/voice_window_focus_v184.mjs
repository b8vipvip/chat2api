import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const routing = readFileSync("chrome_extension/audio_routing_v4.js", "utf8");
const liveRouting = readFileSync("chrome_extension/audio_routing_live.js", "utf8");
const speech = readFileSync("chrome_extension/content_voice_v2.js", "utf8");
const live = readFileSync("chrome_extension/content_voice_live.js", "utf8");
for (const source of [routing,liveRouting,speech,live]) new vm.Script(source);

let currentTab = {id: 17, windowId: 81, active: false};
let currentWindow = {id: 81, focused:false, state:"minimized"};
const calls=[];
const chrome={
  windows:{
    async get(){return {...currentWindow};},
    async update(id,opts){assert.equal(id,81);calls.push({method:"window.update",...opts});currentWindow={...currentWindow,...opts};return {...currentWindow};},
  },
  tabs:{
    async update(id,opts){assert.equal(id,17);calls.push({method:"tab.update",...opts});currentTab={...currentTab,...opts};return {...currentTab};},
    async get(){return {...currentTab};},
  },
};
const ctx=vm.createContext({
  chrome, sleep:async()=>{}, handleServerMessage:async()=>{},
  resolveTargetTab:async()=>({...currentTab}),console,
});
vm.runInContext(routing,ctx);
const activated=await ctx.chat2apiActivateVoiceTabV184({...currentTab});
assert.equal(activated.chat2apiWindowFocusedBefore,false);
assert.equal(activated.chat2apiWindowFocusedAfter,true);
assert.equal(activated.active,true);
assert.equal(currentWindow.state,"normal");
assert.equal(calls.filter(x=>x.method==="window.update"&&x.focused===true).length,1);
assert.equal(calls.filter(x=>x.method==="tab.update"&&x.active===true).length,1);

// The Worker Xvfb document may report hidden even after its Chrome window
// is focused. This alone is diagnostic, NOT a Voice admission veto.
const speechBlock = speech.slice(speech.indexOf("  async function openVoice("),speech.indexOf("  function normVoiceText("));
const stages=[];
const speechCtx=vm.createContext({
  document:{visibilityState:"hidden",hasFocus:()=>false},
  diagnostic:async(_,stage,details)=>stages.push({stage,...details}),
  voiceUiReady:()=>({reason:"voice-orb"}),
  settleVoiceWelcome:async()=>false,
  eventBucket:()=>[{type:"voice.remote.track"}],
  voiceFailure:(code,message)=>Object.assign(new Error(code+": "+message),{code}),
});
vm.runInContext(speechBlock+"\nglobalThis.trySpeech=openVoice;",speechCtx);
const response = await speechCtx.trySpeech({requestId:"speech-hidden"});
assert.equal(response.transport.type,"voice.remote.track");
assert.equal(stages[0].stage,"page-visibility-observed");
assert.equal(stages[0].browser_visibility,"hidden");

const liveBlock=live.slice(live.indexOf("  async function openVoice("),live.indexOf("  async function startLive("));
const liveCtx=vm.createContext({
  document:{visibilityState:"hidden"},
  voiceUiReady:()=>true,
  settleVoiceWelcome:async()=>false,
  waitLiveTransport:async type=>({type,track_count:1}),
});
vm.runInContext(liveBlock+"\nglobalThis.tryLive=openVoice;",liveCtx);
await liveCtx.tryLive({requestId:"live-hidden"});
assert.ok(liveRouting.includes("chat2apiActivateVoiceTabV184"));
assert.ok(liveRouting.includes("VOICE_WINDOW_NOT_FOCUSED"));
console.log("Voice speech/live share foreground routing; hidden is evidence, not a terminal veto");
