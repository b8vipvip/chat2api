import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const v4 = readFileSync("chrome_extension/content_voice_fix_v4.js", "utf8");
const v2 = readFileSync("chrome_extension/content_voice_v2.js", "utf8");
new vm.Script(v4);
new vm.Script(v2);

function env() {
  const state = { orb:false, exit:false, clickCount:0 };
  const start = {
    disabled:false, dataset:{testid:"composer-speech-button"},
    getAttribute(name){return name==="aria-label"?"Start Voice":"";},
    getBoundingClientRect(){return {width:40,height:40,bottom:900};},
    innerText:"", textContent:"",
    click(){state.clickCount++;state.orb=true;},
  };
  const end = {
    disabled:false, dataset:{},
    getAttribute(name){return name==="aria-label"?"End Voice":"";},
    getBoundingClientRect(){return {width:40,height:40,bottom:900};},
  };
  const orb = {getBoundingClientRect(){return {width:40,height:40};}};
  const composer = {
    getBoundingClientRect(){return {width:800,height:100};},
    querySelector(){return {};},
    querySelectorAll(selector){return selector==="button"?[start]:[];},
  };
  const document = {
    body:{innerText:"Ready when you are",textContent:"Ready when you are"},
    querySelectorAll(selector){
      if (selector.includes("voice-floating-orb") || selector.includes("voice-orb")) return state.orb?[orb]:[];
      if (selector==="button") return state.exit?[end]:[start];
      if (selector.includes("form[data-type")) return [composer];
      return [];
    },
  };
  const visible = element=>Boolean(element?.getBoundingClientRect()?.width);
  const labelOf = element=>element.getAttribute?.("aria-label")||"";
  return {state,start,document,visible,labelOf,composer};
}

// V4 must not return a "ready" Voice session merely because generic body
// text contains an old assistant's "Ready when you are" phrase.
{
  const e=env();
  let listener;
  const ctx=vm.createContext({
    document:e.document, window:{}, Date, Math, String, Number,
    getComputedStyle:()=>({display:"block",visibility:"visible"}),
    chrome:{runtime:{onMessage:{addListener(fn){listener=fn;}}},
      storage:{local:{get:async()=>({})}}},
    HTMLTextAreaElement:class {}, HTMLInputElement:class {},
    setTimeout,
  });
  vm.runInContext(v4,ctx);
  async function prepare(){
    return await new Promise((resolve,reject)=>{
      const asyncResponse=listener({type:"chat2api.voice.trigger.prepare.v4",timeout_ms:1500},null,r=>{
        if(r.ok)resolve(r.data);else reject(new Error(r.error));
      });
      assert.equal(asyncResponse,true);
    });
  }
  let res=await prepare();
  assert.equal(res.ready,false);
  assert.equal(res.reason,"strict-trigger-v4");
  e.state.orb=true;
  res=await prepare();
  assert.equal(res.ready,true);
  assert.equal(res.evidence,"voice-orb");
  e.state.orb=false;e.state.exit=true;
  res=await prepare();
  assert.equal(res.ready,true);
  assert.equal(res.evidence,"voice-exit-button");
}

// V2 must not click Start Voice a second time in an already-active session,
// and must not count a document-wide readiness phrase as a real session.
{
  const e=env();
  const stages=[];
  const required = v2.slice(v2.indexOf("  function voiceUiReady()"), v2.indexOf("  function assistantNodes()"));
  const flow = v2.slice(v2.indexOf("  async function openVoice("),v2.indexOf("  function normVoiceText("));
  assert.ok(required.includes("function voiceUiReady"));
  assert.ok(flow.includes("async function openVoice"));
  const envV2={
    document:e.document, getComputedStyle:()=>({display:"block",visibility:"visible"}),
    visible:e.visible, labelOf:e.labelOf, textOf:element=>element.innerText||"",
    voiceTrigger:()=>({button:e.start,label:"Start Voice"}),
    settleVoiceWelcome:async()=>false,
    eventBucket:()=>[{type:"voice.remote.track"}],
    waitFor:async fn=>fn(), waitMain:async()=>({type:"voice.remote.track"}),
    diagnostic:async(_active,stage,more)=>{stages.push({stage,...more});},
    voiceFailure:(code,message)=>new Error(code+": "+message),
    pageError:()=>"", composerRoot:()=>e.composer, delay:async()=>{},
  };
  const ctx=vm.createContext(envV2);
  vm.runInContext(required+"\n"+flow+"\nglobalThis.api={voiceUiReady,openVoice};",ctx);
  assert.equal(ctx.api.voiceUiReady(),null,
    "body-wide ready text cannot indicate a Voice session");

  e.state.orb=true;
  await ctx.api.openVoice({requestId:"v-existing"});
  assert.equal(e.state.clickCount,0,
    "already-active Voice UI cannot be toggled by another Start Voice click");
  assert.ok(stages.some(x=>x.stage==="session-already-visible"));

  e.state.orb=false;
  await ctx.api.openVoice({requestId:"v-new"});
  assert.equal(e.state.clickCount,1,
    "real start trigger is clicked exactly once when no Voice UI exists");
  assert.ok(stages.some(x=>x.stage==="trigger-clicked"));

  // Clicking a present trigger does not guarantee the UI really opened.
  e.state.orb=false;
  e.start.click=()=>{e.state.clickCount++;};
  await assert.rejects(
    ctx.api.openVoice({requestId:"v-failed"}),
    /VOICE_UI_NOT_READY/,
  );
  assert.equal(e.state.clickCount,2);
}
console.log("Voice V4/V2 readiness uses live session evidence, not stale body text");
