import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync("chrome_extension/content_reasoning_v7.js","utf8");

async function scenario(applySelection) {
  let onMessage, menuOpen=false, selected=false, inspectClicks=0, choiceClicks=0;
  const pill={kind:"pill",innerText:"GPT-5.6 Sol",focus(){},click(){inspectClicks++;menuOpen=true;}};
  const choice={kind:"choice",innerText:"Medium",focus(){},click(){choiceClicks++;selected=true;menuOpen=false;}};
  const menu={kind:"menu",querySelectorAll:()=>[choice]};
  const document={kind:"document"};
  const driver={
    version:"22.4.0",
    dispatchKey(target,name,_code,extra={}){
      if(target===document && name==="M" && extra.ctrlKey && extra.shiftKey) menuOpen=true;
      if(target===choice && name==="Enter"){if(applySelection)selected=true;menuOpen=false;}
      return true;
    },
    verifyReasoning(level){return {ok:level==="medium"&&selected&&menuOpen,code:"reasoning_state_untrusted",requested_reasoning:level,actual_reasoning:selected&&menuOpen?"medium":null};},
    attachVerification(data,level){const verification=this.verifyReasoning(level);return {...data,verification};},
    classifyReasoningError(err,level){return {code:err.code||"reasoning_state_untrusted",message:err.message,verification:err.verification||this.verifyReasoning(level)};}
  };
  const page={
    normalizedLabelLower:value=>String(value||"").toLowerCase().trim(),
    labelOf:el=>el?.innerText||"",
    visible:()=>true,
    reasoningControl:()=>({element:pill,reasoning:"medium"}),
    openSurfaces:()=>menuOpen?[menu]:[],
    reasoningSlider:()=>null,
  };
  const sandbox={
    console,document,window:{kind:"window"},HTMLInputElement:class{},
    setTimeout,clearTimeout,
    chrome:{runtime:{onMessage:{addListener(fn){onMessage=fn;}}}},
    __CHAT2API_PAGE_ADAPTER_V22__:page,
    __CHAT2API_PAGE_DRIVER_V22__:driver,
    __CHAT2API_MODEL_LIBRARY_V145__:{pickerControl:()=>pill},
  };
  sandbox.globalThis=sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src,sandbox);
  const response=await new Promise(resolve=>onMessage({type:"chat2api.reasoning.prepare.v7",reasoning_level:"medium"},null,resolve));
  return {response,inspectClicks,choiceClicks};
}

const success=await scenario(true);
assert.equal(success.response.ok,true,"live selected-state proof should allow request preflight");
assert.equal(success.inspectClicks,1,"one read-only picker reopening");
assert.equal(success.choiceClicks,0,"never replay model/reasoning selection via click fallback");
const fail=await scenario(false);
assert.equal(fail.response.ok,false,"no selected-state proof must fail closed");
assert.equal(fail.choiceClicks,0,"no-click attempt must not lead to a second selection authority");
assert.equal(fail.response.code,"reasoning_state_untrusted");
console.log("Reasoning one-write/live-inspection regression passed");
