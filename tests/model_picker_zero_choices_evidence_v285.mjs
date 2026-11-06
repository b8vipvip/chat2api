import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/content_model_library_v145.js","utf8");

async function runEmptyPicker({popup=true}={}) {
  let now=100_000, clickCount=0, opened=false, listener=null, selected=false;
  class Element {
    constructor(label,attrs={}) {this.label=label;this.attrs=attrs;this.disabled=false;this.className="";}
    get innerText(){return this.label;}
    get textContent(){return this.label;}
    getAttribute(name){return this.attrs[name] ?? null;}
    hasAttribute(name){return Object.hasOwn(this.attrs,name);}
    getBoundingClientRect(){return {width:150,height:40};}
    matches(selector){return Boolean(this.attrs.role==="menuitem" && selector.includes("[role='menuitem']"));}
    closest(){return null;}
    querySelectorAll(){return [];}
    click(){if(this.attrs["data-testid"]==="model-switcher"){opened=true;clickCount++;}else selected=true;}
  }
  const picker=new Element("ChatGPT",{"data-testid":"model-switcher","aria-label":"Choose model"});
  const rows=["Instant","Thinking","Pro"].map(label=>new Element(label,{role:"menuitem"}));
  const menu=new Element("",{role:"menu"});
  menu.querySelectorAll=()=>rows;
  rows.forEach(row=>row.parentElement=menu);
  const document={
    querySelectorAll(selector){
      if(selector.includes("form")) return [];
      if(selector.includes("data-testid*='model'")) return [picker];
      if(selector.includes("[role='menu']")) return popup&&opened?[menu]:[];
      if(selector.includes("[role='menuitem']")) return popup&&opened?rows:[];
      return [];
    },
    querySelector(selector){return selector.includes("#prompt-textarea")?{}:null;},
    dispatchEvent(event){if(event?.key==="Escape")opened=false;},
  };
  class Clock extends Date {static now(){return now;}}
  const context={
    Element,document,Date:Clock,
    console,getComputedStyle:()=>({display:"block",visibility:"visible"}),
    KeyboardEvent:class KeyboardEvent{constructor(type,props){this.key=props.key;}},
    // Virtual time keeps the actual two bounded 7-second retries testable
    // without slowing down CI or modifying the production timeout values.
    setTimeout(fn,ms){now+=ms;queueMicrotask(fn);return 1;},
    clearTimeout(){},
    chrome:{runtime:{onMessage:{addListener(fn){listener=fn;}}}},
  };
  context.globalThis=context;
  vm.runInNewContext(source,context);
  const result=await new Promise(resolve=>{
    assert.equal(listener({type:"chat2api.model.prepare.v149",model:"gpt-5.6-sol"},null,resolve),true);
  });
  assert.equal(selected,false,"never fabricate a model selection from vague menu labels");
  assert.equal(clickCount,2,"allow exactly the existing two bounded picker clicks");
  return result;
}

for(const popup of [true,false]){
  const result=await runEmptyPicker({popup});
  assert.equal(result.ok,false);
  assert.equal(result.code,"model_menu_no_concrete_choices");
  assert.equal(result.stage,"read-model-picker");
  assert.match(result.error,/ChatGPT model menu did not expose concrete model choices/);
  assert.match(result.error,new RegExp("popup_roots="+Number(popup)));
  assert.match(result.error,new RegExp("visible_rows="+(popup?3:0)));
  assert.match(result.error,/concrete_rows=0/);
  assert.match(result.error,/composer_ready=1/);
  assert.doesNotMatch(result.error,/Instant|Thinking|Pro|html|https:\/\//);
  assert.deepEqual(Array.from(result.evidence),[]);
}
console.log("v285 menu: bounded zero-choice evidence, fail-closed selection, no raw menu labels");
