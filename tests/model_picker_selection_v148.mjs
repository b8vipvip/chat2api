import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source=readFileSync("chrome_extension/content_model_library_v145.js","utf8");

async function scenario({withFive=true,enabled=true,canSwitch=true,hydrationReads=0,genericHeader=false,detachedMenu=false,ignoredFirstClick=false}={}){
  let opened=false;
  let clicks=0;
  let remainingHydrationReads=0;
  let activeModel=genericHeader?"gpt-5.6-sol":"gpt-6";
  let listener;
  class Element {
    constructor(label, attrs={}, onClick=null){
      this.label=label;this.attrs=attrs;this.onClick=onClick;this.parentElement=null;
      this.disabled=false;this.className="";
    }
    get innerText(){return this.label}
    get textContent(){return this.label}
    getAttribute(key){return this.attrs[key]??null}
    hasAttribute(key){return Object.prototype.hasOwnProperty.call(this.attrs,key)}
    getBoundingClientRect(){return {width:200,height:48,left:0,top:0}}
    matches(selector){return Boolean(this.attrs.role==="menuitem" && selector.includes("[role='menuitem']")) ||
      Boolean(this.attrs.role==="option" && selector.includes("[role='option']")) ||
      Boolean(this.attrs.tag==="button" && selector.includes("button"))}
    closest(selector){return null}
    querySelectorAll(){return []}
    scrollIntoView(){}
    click(){this.onClick?.()}
  }
  const picker=new Element(genericHeader?"ChatGPT":activeModel,{"data-testid":"model-switcher","aria-label":"Choose model","tag":"button"},()=>{clicks++;if(ignoredFirstClick&&clicks===1)return;opened=true;remainingHydrationReads=hydrationReads});
  const bigWrapper=new Element(
    "High High, 3 of 3. Locked, opens access options GPT-6 GPT-5.6 Sol GPT-5.5 Leaving on October 14",
    {role:"menuitem"},()=>{throw Error("Container must never be clicked")}
  );
  const sol=new Element("GPT-5.6 Sol",{role:"menuitem",...(genericHeader?{"aria-checked":"true"}:{})},()=>{if(canSwitch){activeModel="gpt-5.6-sol";picker.label="GPT-5.6 Sol";opened=false}});
  const five=new Element("GPT-5.5 Leaving on October 14",{
    role:"menuitem",...(enabled?{}:{"aria-disabled":"true"})
  },()=>{if(canSwitch){activeModel="gpt-5.5";picker.label="GPT-5.5";opened=false}});
  const group=new Element("",{role:"menu"});
  for(const item of [bigWrapper,sol,five])item.parentElement=group;
  group.querySelectorAll=()=>remainingHydrationReads-->0?[]:[bigWrapper,sol,...(withFive?[five]:[])];
  const document={
    querySelectorAll(selector){
      if(selector.includes("form"))return [];
      if(selector.includes("data-testid*='model'"))return [picker];
      if(selector.includes("[role='menu']"))return opened&&!detachedMenu?[group]:[];
      if(selector.includes("[role='menuitem']"))return opened?[bigWrapper,sol,...(withFive?[five]:[])]:[];
      return [];
    },
    querySelector:(selector)=>selector.includes("#prompt-textarea")?{ready:true}:null,
    dispatchEvent(event){if(event?.key==="Escape")opened=false},
  };
  const context={
    Element,document,console,Date,setTimeout,clearTimeout,
    getComputedStyle:()=>({display:"block",visibility:"visible"}),
    KeyboardEvent: class KeyboardEvent {constructor(type,props){this.key=props.key}},
    chrome:{runtime:{onMessage:{addListener(fn){listener=fn}}}},
  };
  context.globalThis=context;
  vm.runInNewContext(source,context);
  const issue=message=>new Promise(resolve=>{
    const used=listener(message,null,resolve);
    assert.equal(used,true);
  });
  const inspected=genericHeader?await context.__CHAT2API_MODEL_LIBRARY_V145__.inspectSelectedModel():null;
  const discovered=await issue({type:"chat2api.models.validate.v145"});
  assert.equal(discovered.ok,true);
  const models=discovered.data.observed_models;
  assert.equal(models.some(m=>m.id==="gpt-6"),false,
    "popup wrapper must not become a GPT-6 model candidate");
  assert.equal(models.some(m=>m.id==="gpt-5.6-sol"),true);
  if(withFive)assert.equal(models.find(m=>m.id==="gpt-5.5")?.enabled,enabled);
  const result=await issue({type:"chat2api.model.select.v145",model:"gpt-5.5"});
  return {result,discovered,inspected};
}

const checked=await scenario({genericHeader:true});
assert.equal(checked.inspected.selected_model,"gpt-5.6-sol",
  "generic header must use checked row in live menu");
assert.equal(checked.inspected.evidence_source,"live-menu-selected-row");
const detached=await scenario({detachedMenu:true});
assert.equal(detached.result.ok,true,"portal model rows without a menu wrapper must still be selectable");
const retried=await scenario({ignoredFirstClick:true});
assert.equal(retried.result.ok,true,"a picker click ignored during hydration must be retried once");
const hydrated=await scenario({hydrationReads:3});
assert.equal(hydrated.discovered.ok,true,"model-menu shell must hydrate before extracting choices");
assert.equal(hydrated.result.ok,true);

const success=await scenario();
assert.equal(success.result.ok,true);
assert.equal(success.result.data.validated,true);
assert.equal(success.result.data.validation_method,"selected-picker-label");
const disabled=await scenario({enabled:false});
assert.equal(disabled.result.ok,false);
assert.equal(disabled.result.code,"model_option_disabled");
const missing=await scenario({withFive:false});
assert.equal(missing.result.ok,false);
assert.equal(missing.result.code,"model_option_missing");
const unconfirmed=await scenario({canSwitch:false});
assert.equal(unconfirmed.result.ok,false);
assert.equal(unconfirmed.result.code,"model_selection_unconfirmed");
assert.equal(unconfirmed.result.stage,"confirm-model-selection");
console.log("v148 model picker: container rejection, selection, disabled, missing and unconfirmed states passed");
