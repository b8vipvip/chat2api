import assert from "node:assert/strict";
import vm from "node:vm";
import {readFileSync} from "node:fs";

const source=readFileSync("chrome_extension/content_voice_ui_gate_v185.js","utf8");
new vm.Script(source);
const state={toast:false,modal:true,clicks:0};
function element(value, getChildren=()=>[]) {
  return {
    disabled:false,innerText:value,textContent:value,
    getBoundingClientRect(){return {width:240,height:80};},
    getAttribute(){return "";},
    querySelectorAll(query){return query==="button"?getChildren():[];},
  };
}
const button=element("Continue");
button.click=()=>{state.clicks++;state.modal=false;};
const dialog=element("Meet Voice Say what's on your mind Continue",()=>[button]);
const toast=element("Voice couldn't connect Please check your connection and try again");
const document={
  querySelectorAll(selector) {
    if (selector.includes("[role='dialog']")) return state.modal?[dialog]:[];
    if (selector==="h1,h2,h3,[role='heading']") return [];
    if (selector.includes("[role='alert']")) return state.toast?[toast]:[];
    if (selector==="p,span,div") return state.toast?[toast]:[];
    return [];
  },
};
const ctx=vm.createContext({document,getComputedStyle:()=>({display:"block",visibility:"visible"})});
vm.runInContext(source,ctx);
const gate=ctx.__CHAT2API_VOICE_UI_GATE_V185__;
assert.ok(gate.welcomeDialog());
assert.equal(gate.connectionFailure(),null);
const done=gate.continueWelcome();
assert.equal(done.clicked,true);
assert.equal(done.evidence,"dialog");
assert.equal(state.clicks,1);
assert.equal(gate.continueWelcome(),null,"No second arbitrary Continue click");
state.toast=true;
const fail=gate.connectionFailure();
assert.equal(fail.code,"VOICE_CHATGPT_CONNECT_FAILED");
assert.match(fail.message,/Voice couldn't connect/i);
state.toast=false;
assert.equal(gate.connectionFailure(),null);
const nonVoice=element("Billing Continue",()=>[button]);
state.modal=false;
document.querySelectorAll=selector=>selector.includes("[role='dialog']")?[nonVoice]:[];
assert.equal(gate.continueWelcome(),null,"Unrelated dialog cannot be accepted");
assert.equal(state.clicks,1);
console.log("Voice onboarding requires exact dialog title and fails with real ChatGPT Voice toast");
