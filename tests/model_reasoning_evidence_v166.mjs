import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const adapterSrc = fs.readFileSync("chrome_extension/content_page_adapter_v22.js", "utf8");
const driverSrc = fs.readFileSync("chrome_extension/content_page_driver_v22.js", "utf8");
let selected = true;
class Element {
  constructor(label, attrs={}) { this.innerText=label; this.textContent=label; this.attrs=attrs; this.disabled=false; }
  getAttribute(name){return this.attrs[name]??null}
  getBoundingClientRect(){return {width:170,height:40}}
  querySelectorAll(){return []}
}
const headerModel = new Element("GPT-6", {"data-testid":"model-switcher","aria-label":"GPT-6"});
const header = new Element("");
header.querySelectorAll=()=>[headerModel];
const selectedReasoning = new Element("Medium", {"aria-checked":"true"});
const menu = new Element("GPT-6 Medium High");
menu.querySelectorAll=()=>selected?[selectedReasoning]:[];
const document = {
  querySelectorAll(selector) {
    if(selector === "header") return [header];
    if(selector === "[role='menu']") return [menu];
    return [];
  }
};
const cache = JSON.stringify({family:"gpt-5.5", reasoning:"medium", dirty_family:false, dirty_reasoning:false});
let listener = null;
const sandbox = {
  document, Element, console,
  sessionStorage: {getItem:()=>cache},
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
  chrome:{runtime:{onMessage:{addListener:fn=>{listener=fn}}}},
};
sandbox.globalThis=sandbox;
vm.createContext(sandbox);
vm.runInContext(adapterSrc,sandbox);
vm.runInContext(driverSrc,sandbox);
const adapter=sandbox.__CHAT2API_PAGE_ADAPTER_V22__;
const driver=sandbox.__CHAT2API_PAGE_DRIVER_V22__;
assert.equal(adapter.familyFromText("GPT-6"),"gpt-6");
assert.equal(adapter.familyFromText("GPT-5.6 Sol"),"gpt-5.6-sol");
assert.equal(adapter.familyFromText("GPT-6 GPT-5.6 Sol"),"");
assert.equal(driver.currentState().family,"gpt-6");
assert.equal(driver.currentState().reasoning,"medium");
assert.equal(driver.verifyState({model:"gpt-6",reasoning:"medium"}).ok,true);
selected=false;
const result=driver.verifyState({model:"gpt-6",reasoning:"medium"});
assert.equal(result.ok,false,"cached reasoning cannot prove an applied UI state");
assert.equal(result.code,"reasoning_state_untrusted");
assert.equal(result.reasoning_source,"session-cache-unverified");
assert.equal(typeof listener,"function");
console.log("Live model and reasoning selection requires fresh evidence; stale cache rejected");
