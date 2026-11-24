import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/audio_routing_live.js","utf8");
const start = source.indexOf("  async function ensureLiveContent(");
const end = source.indexOf("  async function reportInputError(",start);
assert.ok(start >= 0 && end > start,"production ensureLiveContent must remain extractable");
const implementation = source.slice(start,end);

function harness({failFile="",mainReady=true,isolatedReady=true}={}) {
  const calls = [];
  const sleeps = [];
  const chrome = {
    scripting:{
      async executeScript(details) {
        calls.push(details);
        if (details.files?.some(file=>file===failFile)) {
          throw new Error("injected fixture failure: " + failFile);
        }
        if (typeof details.func === "function") {
          if (details.world === "MAIN") {
            return [{result:{
              voiceMain:mainReady,
              liveMain:mainReady,
            }}];
          }
          return [{result:{
            liveContent:isolatedReady,
            textContext:isolatedReady,
          }}];
        }
        return [{result:undefined}];
      },
    },
  };
  let ensuredTab = null;
  const ensureContent = async tabId=>{ensuredTab=tabId;};
  const sleep = async ms=>{sleeps.push(ms);};
  const ensureLiveContent = vm.runInNewContext(
    implementation + "\nensureLiveContent",
    {chrome,ensureContent,sleep,String,Error,Boolean},
  );
  return {ensureLiveContent,calls,sleeps,get ensuredTab(){return ensuredTab;}};
}

{
  const h=harness();
  await h.ensureLiveContent(42);
  assert.equal(h.ensuredTab,42);
  assert.equal(h.calls.length,4,
    "inject both worlds and verify each bridge before starting Voice");
  assert.equal(h.calls[0].world,"MAIN");
  assert.ok(h.calls[0].files.includes("voice_live_main.js"));
  assert.ok(h.calls[1].files.includes("content_voice_live.js"));
  assert.equal(h.calls[2].world,"MAIN");
  assert.equal(typeof h.calls[2].func,"function");
  assert.equal(h.calls[3].world,undefined,
    "isolated-world verification must run outside MAIN world");
  assert.deepEqual(h.sleeps,[120]);
}

{
  const h=harness({failFile:"voice_live_main.js"});
  await assert.rejects(h.ensureLiveContent(43),/MAIN-world bridge injection failed.*fixture failure/);
  assert.equal(h.calls.length,1,
    "a MAIN-world injection failure must stop before isolated injection and session dispatch");
}

{
  const h=harness({failFile:"content_voice_live_text_v24.js"});
  await assert.rejects(h.ensureLiveContent(44),/isolated-world controller injection failed.*fixture failure/);
  assert.equal(h.calls.length,2);
}

{
  const h=harness({mainReady:false});
  await assert.rejects(h.ensureLiveContent(45),/MAIN-world audio\/WebRTC bridge did not become ready/);
  assert.equal(h.calls.length,3,
    "an absent MAIN bridge must be detected before the isolated-world probe");
}

{
  const h=harness({isolatedReady:false});
  await assert.rejects(h.ensureLiveContent(46),/isolated controller\/context bridge did not become ready/);
  assert.equal(h.calls.length,4);
}

console.log("voice-live-bridge-injection-v1-passed");
