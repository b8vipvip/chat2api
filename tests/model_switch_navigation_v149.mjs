import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/background_model_switch_v149.js","utf8");

async function runScenario({navigates=false,clickSucceeds=true,missing=false,disabled=false}={}) {
  let now=100000, clicks=0, inspected=0, navChecks=0, loaded=0;
  let current="gpt-6";
  const desired="gpt-5.6-sol";
  const modelRows=()=>[
    {id:"gpt-6",selected:current==="gpt-6",enabled:true},
    {id:desired,selected:current===desired,enabled:!disabled},
  ];
  const chrome={
    scripting:{executeScript:async detail=>{
      if(detail.files){loaded++;return []}
      if(detail.func){inspected++;return [{result:{selected_model:current,
        inspected_at:"2026-10-14T12:40:00Z",evidence_source:"live-visible-picker"}}]}
      throw Error("Unexpected script execution");
    }},
    tabs:{
      get:async id=>{
        assert.equal(id,22);
        const status=navigates&&clicks&&++navChecks<=2?"loading":"complete";
        return {id:22,status,url:"https://chatgpt.com/c/test"};
      },
      sendMessage:async(id,message)=>{
        assert.equal(id,22);
        if(message.type==="chat2api.model.prepare.v149"){
          if(missing)return {ok:false,code:"model_option_missing",stage:"prepare-model-selection",error:"not present"};
          if(disabled)return {ok:false,code:"model_option_disabled",stage:"prepare-model-selection",error:"disabled"};
          return {ok:true,data:{ticket:"t1",before:current,
            already_selected:current===desired,evidence:modelRows()}};
        }
        if(message.type==="chat2api.model.commit.v149") {
          clicks++;
          // The acknowledgement is delivered before a navigation. Subsequent
          // sendMessage calls use a different fresh content script/document.
          if(clickSucceeds)current=desired;
          return {ok:true,model:desired,click_acknowledged:true};
        }
        if(message.type==="chat2api.model.inspect.v149") throw Error("Confirmation must not re-open model picker via messaging");
        throw Error("Unexpected message "+message.type);
      }
    }
  };
  const context={
    chrome,Date:class extends Date{static now(){return now}},
    // Advance the fake clock for short UI ticks, not multi-second
    // transport watchdogs. Immediate mock responses must win the race
    // against an inspection timeout that has not actually elapsed.
    setTimeout:(fn,ms)=>{
      if(ms>=1000)return 1;
      queueMicrotask(()=>{now+=ms;fn()});
      return 1;
    },
    console,
  };
  context.globalThis=context;
  vm.runInNewContext(source,context);
  const result=await context.__CHAT2API_MODEL_SWITCH_V149__.safeSelect(22,desired);
  return {result,clicks,inspected,loaded};
}

const navigation=await runScenario({navigates:true});
assert.equal(navigation.result.ok,true,JSON.stringify(navigation.result));
assert.equal(navigation.clicks,1,"navigation must not cause duplicate click");
assert.equal(navigation.result.data.current_model,"gpt-5.6-sol");
assert.ok(navigation.inspected>=2,"must verify from the new live page twice");
assert.equal(navigation.result.data.validation_method,"fresh-tab-picker-state-v149");

const steady=await runScenario();
assert.equal(steady.result.ok,true);
assert.equal(steady.clicks,1);
assert.ok(steady.inspected>=2);

const notChanged=await runScenario({clickSucceeds:false});
assert.equal(notChanged.result.ok,false);
assert.equal(notChanged.result.code,"model_selection_unconfirmed");
assert.equal(notChanged.clicks,1,"never retry an already acknowledged click");

const notPresent=await runScenario({missing:true});
assert.equal(notPresent.result.ok,false);
assert.equal(notPresent.result.code,"model_option_missing");
assert.equal(notPresent.clicks,0);

const unavailable=await runScenario({disabled:true});
assert.equal(unavailable.result.ok,false);
assert.equal(unavailable.result.code,"model_option_disabled");
assert.equal(unavailable.clicks,0);
console.log("v149 background model navigation: reload, stable, missing, disabled, and unconfirmed cases passed");