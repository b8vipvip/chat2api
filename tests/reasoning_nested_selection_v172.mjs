import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

class Element {
  constructor(label="", attrs={}, parent=null) {
    this.innerText=label; this.textContent=label; this.attrs=attrs;
    this.parentElement=parent; this.children=[];
    if (parent) parent.children.push(this);
  }
  getAttribute(name) { return this.attrs[name]??null; }
  getBoundingClientRect() { return {width:170,height:44}; }
  closest(selector) {
    const allowed=selector.includes("[role='menuitemradio']");
    for(let node=this;node;node=node.parentElement) {
      if(allowed && ["menuitemradio","option","menuitem"].includes(node.attrs.role)) return node;
    }
    return null;
  }
  matches(selector) { return selector.includes("menuitemradio") && this.attrs.role==="menuitemradio"; }
  querySelectorAll(selector) {
    const descendants=[];
    function add(node) {for(const child of node.children){descendants.push(child);add(child);}}
    add(this);
    if (selector.includes("[aria-checked='true']")) return descendants.filter(node=>
      node.attrs["aria-checked"]==="true" ||
      node.attrs["aria-selected"]==="true" ||
      node.attrs["data-selected"]==="true" ||
      ["checked","selected"].includes(node.attrs["data-state"]));
    return [];
  }
}
const dialog=new Element("");
const selectedRow=new Element("Medium",{role:"menuitemradio","aria-label":"Selected effort"},dialog);
const checkedIcon=new Element("",{"data-state":"checked"},selectedRow);
const highRow=new Element("High",{role:"menuitemradio"},dialog);
const secondIcon=new Element("",{},highRow);
const doc={
  querySelectorAll(selector) {return selector==="[role='dialog']" ? [dialog] : [];}
};
const sandbox={
  console,document:doc,Element,
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
  sessionStorage:{getItem:()=>JSON.stringify({reasoning:"medium",dirty_reasoning:false})},
  chrome:{runtime:{onMessage:{addListener(){}}}},
};
sandbox.globalThis=sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync("chrome_extension/content_page_adapter_v22.js","utf8"),sandbox);
vm.runInContext(fs.readFileSync("chrome_extension/content_page_driver_v22.js","utf8"),sandbox);
const adapter=sandbox.__CHAT2API_PAGE_ADAPTER_V22__;
const driver=sandbox.__CHAT2API_PAGE_DRIVER_V22__;
assert.equal(adapter.reasoningEvidence().reasoning,"medium","selected icon must resolve to its interactive row text");
assert.equal(driver.verifyReasoning("medium").ok,true,"current menu row is authoritative, not cache");
assert.equal(driver.verifyReasoning("high").ok,false);
let inspection=driver.verifyReasoning("medium").reasoning_inspection;
assert.equal(inspection.selected_marker_count,1);
assert.equal(inspection.open_surface_count,1);
assert.deepEqual([...inspection.selected_reasoning_levels],["medium"]);
secondIcon.attrs["aria-checked"]="true";
const ambiguous=driver.verifyReasoning("medium");
assert.equal(ambiguous.ok,false,"contradictory live selections must fail closed");
assert.equal(ambiguous.reasoning_source,"ambiguous-reasoning-selection");
checkedIcon.attrs["data-state"]="";
secondIcon.attrs["aria-checked"]="";
const absent=driver.verifyReasoning("medium");
assert.equal(absent.ok,false,"cached medium must not be promoted as live proof");
assert.equal(absent.code,"reasoning_state_untrusted");
console.log("Nested reasoning selection evidence remains single-authority and fail-closed");
