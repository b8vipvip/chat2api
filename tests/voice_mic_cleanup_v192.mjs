import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/voice_main_v2.js", "utf8");
new vm.Script(source);

const listeners=[];
const posted=[];
const page={
  addEventListener(type, callback){if(type==="message")listeners.push(callback);},
  postMessage(event){posted.push(event);},
};
let closeOld;
let stoppedTracks=0;
let oldOscStopped=0;
const oldContext={close:()=>new Promise(resolve=>{closeOld=resolve;})};
const oldMic={
  requestId:"old-voice",
  micDestination:{stream:{getTracks:()=>[{stop(){stoppedTracks++;}}]}},
  micOscillator:{stop(){oldOscStopped++;}},
  micContext:oldContext,
};
const env=vm.createContext({window:page,globalThis:{__CHAT2API_VOICE_MAIN_V1__:oldMic}});
vm.runInContext(source,env);
assert.equal(listeners.length,1);
listeners[0]({
  source:page,
  data:{source:"chat2api-isolated",type:"voice.mic.stop",request_id:"old-voice"},
});
assert.equal(stoppedTracks,1);
assert.equal(oldOscStopped,1);
assert.equal(oldMic.micDestination,null);
assert.equal(oldMic.micOscillator,null);
assert.equal(oldMic.micContext,null);
assert.equal(typeof closeOld,"function");
// Simulate a newer Voice request installing a fresh synthetic microphone
// while the old AudioContext.close() Promise is still unresolved.
const newMic={stream:{getTracks:()=>[]}};
const newOsc={stop(){}};
const newContext={close:async()=>{}};
oldMic.micDestination=newMic;
oldMic.micOscillator=newOsc;
oldMic.micContext=newContext;
closeOld();
await new Promise(resolve=>setImmediate(resolve));
assert.equal(oldMic.micDestination,newMic);
assert.equal(oldMic.micOscillator,newOsc);
assert.equal(oldMic.micContext,newContext);
assert.ok(posted.some(e=>e.type==="voice.mic.stopped"&&e.request_id==="old-voice"));
oldMic.requestId="new-voice";
listeners[0]({
  source:page,
  data:{source:"chat2api-isolated",type:"voice.mic.stop",request_id:"old-voice"},
});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(oldMic.micDestination,newMic);
assert.equal(oldMic.micContext,newContext);
assert.ok(posted.some(e=>e.type==="voice.mic.stopped"&&e.reason==="stale-request"),
  "late previous-request mic-stop cannot shut down a newly active session");
console.log("Voice microphone is released per request without corrupting next-session microphone");
