import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const behavior = readFileSync("app/admin_worker_limits_clipboard_v121.js", "utf8");
const columns = readFileSync("app/admin_extension_columns.js", "utf8");
const start = behavior.indexOf("  function ensureLimitDialog() {");
const end = behavior.indexOf("  function loginEndpoint(workerId) {", start);
assert.ok(start > 0 && end > start, "single concurrency modal implementation present");
assert.ok(!columns.includes("data-v121-windows"), "no separate standby input in Worker table");
assert.ok(!columns.includes("data-v121-save-limits"), "Save must not be clipped inside the table");
assert.ok(!behavior.includes("/windows/limit"), "old separate standby setter is retired");
assert.match(behavior, /dialog\.showModal\(\)/, "dialog opens in browser top layer");

const elements = {};
const input = {value:"",focus(){this.focused=true;},select(){this.selected=true;}};
const note = {textContent:""};
const label = {textContent:""};
const cancel = {textContent:"取消"};
const save = {disabled:false,closest(selector){
  assert.equal(selector,"#v240WorkerConcurrencyDialog");
  return elements.dialog;
}};
const form = {addEventListener(type, fn){if(type==="submit")this.submit=fn;}};
let appended=0, reloads=0, requestCount=0, transportError=null;
const requests=[];
const summaryA={textContent:"3"}, summaryB={textContent:"3"};
const rowA={dataset:{v121WorkerLimits:"ext_test"},querySelector(){return summaryA;}};
const rowB={dataset:{v121WorkerLimits:"ext_test"},querySelector(){return summaryB;}};
const dialog = {
  id:"",dataset:{},style:{},open:false,innerHTML:"",
  setAttribute(){},showModal(){this.open=true;},close(){this.open=false;},
  addEventListener(type,fn){this["on_"+type]=fn;},
  querySelector(selector){
    if(selector==="form")return form;
    if(selector==="[data-v121-concurrency]")return input;
    if(selector==="[data-v121-limit-note]")return note;
    if(selector==="[data-v240-worker-label]")return label;
    if(selector==="[data-v121-cancel-limits]")return cancel;
    if(selector==="[data-v121-save-limits]")return save;
    throw new Error("Unknown selector: "+selector);
  }
};
elements.dialog=dialog;
const document={
  getElementById(id){assert.equal(id,"v240WorkerConcurrencyDialog");return appended?dialog:null;},
  createElement(tag){assert.equal(tag,"dialog");return dialog;},
  body:{appendChild(element){assert.equal(element,dialog);appended++;}},
  querySelectorAll(selector){assert.equal(selector,"[data-v121-worker-limits]");return [rowA,rowB];}
};
const context={
  document, globalThis:{chat2apiReloadCanonicalWorkerListV59:async()=>{reloads++;}},
  jsonRequest:async (url,opts)=>{
    requestCount++;requests.push({url,opts});
    if(transportError)throw transportError;
    return {ok:true,max_concurrency:opts.body.max_concurrency,window_target_sync:{sent:true}};
  }
};
vm.runInNewContext(behavior.slice(start,end)+
  "\nthis.openConcurrency=toggleLimitPopover;this.saveConcurrency=saveLimits;this.closeConcurrency=closeLimitPopovers;",context);

const edit={closest(selector){assert.equal(selector,"[data-v121-worker-limits]");return rowA;}};
context.openConcurrency(edit);
assert.equal(appended,1,"modal appended once to body rather than scrolling table");
assert.equal(dialog.open,true);
assert.equal(label.textContent,"Worker ID：ext_test");
assert.equal(input.value,"3");
assert.equal(input.focused,true);
assert.equal((dialog.innerHTML.match(/<input\b/g)||[]).length,1,"only one numeric input");
assert.match(dialog.innerHTML,/data-v121-save-limits>保存/);
assert.match(dialog.innerHTML,/data-v121-cancel-limits>取消/);
assert.equal(dialog.innerHTML.includes("data-v121-windows"),false);
assert.equal(dialog.style.cssText.includes("position:fixed"),true);

input.value="0";
await context.saveConcurrency(save);
assert.equal(requestCount,0,"invalid concurrency cannot be submitted");
assert.match(note.textContent,/1～32/);
input.value="5";
await context.saveConcurrency(save);
assert.equal(requestCount,1);
assert.equal(requests[0].url,"/api/admin/extensions/ext_test/capacity-v57");
assert.equal(requests[0].opts.method,"PUT");
assert.equal(requests[0].opts.body.max_concurrency,5);
assert.equal(summaryA.textContent,"5");
assert.equal(summaryB.textContent,"5");
assert.equal(reloads,1);
assert.match(note.textContent,/保存成功/);
assert.equal(cancel.textContent,"关闭");
assert.equal(save.disabled,false);

context.closeConcurrency();
assert.equal(dialog.open,false);
dialog.on_close();
context.openConcurrency(edit);
assert.equal(appended,1,"reopened dialog reuses the same modal");
assert.equal(input.value,"5");
input.value="2";
transportError=new Error("HTTP 503");
await context.saveConcurrency(save);
assert.match(note.textContent,/保存失败.*HTTP 503/);
assert.equal(summaryA.textContent,"5","failed save cannot falsely update displayed value");
assert.equal(requestCount,2);
console.log("v240 Worker top-layer single-concurrency editor and backend save path passed");
