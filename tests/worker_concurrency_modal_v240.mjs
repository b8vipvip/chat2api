import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
const behavior=readFileSync("app/admin_worker_limits_clipboard_v121.js","utf8");
const canonical=readFileSync("app/admin_extension_columns.js","utf8");
const linux=readFileSync("app/admin_linux_device_authority_v124.js","utf8");
const unified=readFileSync("app/admin_unified_workers_v153.js","utf8");
assert.ok(!behavior.includes("v240WorkerConcurrencyDialog"));
assert.ok(!canonical.includes("data-v121-windows"));
assert.ok(!linux.includes("data-v121-windows"));
assert.ok(canonical.includes("data-v121-limit-summary"));
assert.ok(linux.includes("data-v121-limit-summary"));
assert.ok(unified.includes('cell(row,"worker_settings")'));
const start=behavior.indexOf("  function closeLimitPopovers("),end=behavior.indexOf("  function loginEndpoint(",start);
assert.ok(start>0&&end>start);
let count=0,fail=false,reloads=0;
const requests=[],editors=[];
function makeRow(value){
  const summary={textContent:String(value),hidden:false},button={hidden:false,closest(){return row;}},nodes={};
  const row={dataset:{v121WorkerLimits:"ext_demo"},isConnected:true,
    querySelector(sel){
      if(sel==="[data-v121-limit-summary]")return summary;
      if(sel==="[data-v121-edit-limits]")return button;
      if(sel==="[data-v121-inline-input]")return nodes.input||null;
      if(sel==="[data-v121-save-limits]")return nodes.save||null;
      if(sel==="[data-v121-limit-note]")return nodes.note||null;
      return null;
    },
    append(input,save,note){
      Object.assign(nodes,{input,save,note});
      for(const node of [input,save,note]){
        node.closest=()=>row;
        node.remove=()=>{for(const key of ["input","save","note"])if(nodes[key]===node)delete nodes[key];};
      }
    }
  };
  editors.push(row);return {row,summary,button,nodes};
}
const first=makeRow(3),second=makeRow(3);
const document={
 createElement(tag){return {tag,dataset:{},style:{},disabled:false,
  addEventListener(type,cb){this["on_"+type]=cb;},setAttribute(){},focus(){this.focused=true;}};},
 querySelectorAll(sel){
   if(sel==='[data-v121-inline-editing="1"]')return editors.filter(x=>x.dataset.v121InlineEditing==="1");
   if(sel==="[data-v121-worker-limits]")return editors;
   throw Error("selector "+sel);
 }};
const ctx={document,encodeURIComponent,globalThis:{chat2apiReloadCanonicalWorkerListV59:async()=>{reloads++;}},
 jsonRequest:async(url,opt)=>{count++;requests.push({url,opt});if(fail)throw Error("HTTP 503");
  return {ok:true,max_concurrency:opt.body.max_concurrency};}};
vm.runInNewContext(behavior.slice(start,end)+"\nthis.openInline=toggleLimitPopover;this.saveInline=saveLimits;this.undoInline=restoreLimitEditor;",ctx);
ctx.openInline(first.button);
assert.equal(first.row.dataset.v121InlineEditing,"1");
assert.equal(first.summary.hidden,true);
assert.equal(first.button.hidden,true);
assert.equal(first.nodes.input.value,"3");
assert.equal(first.nodes.save.textContent,"保存");
assert.equal(first.nodes.input.focused,true);
first.nodes.input.value="0";
await ctx.saveInline(first.nodes.save);
assert.equal(count,0);
assert.match(first.nodes.note.textContent,/1～32/);
first.nodes.input.value="5";
await ctx.saveInline(first.nodes.save);
assert.equal(count,1);
assert.equal(requests[0].url,"/api/admin/extensions/ext_demo/capacity-v57");
assert.equal(requests[0].opt.method,"PUT");
assert.equal(requests[0].opt.body.max_concurrency,5);
assert.equal(first.summary.textContent,"5");
assert.equal(second.summary.textContent,"5");
assert.equal(first.summary.hidden,false);
assert.equal(first.button.hidden,false);
assert.equal(first.row.dataset.v121InlineEditing,undefined);
assert.equal(reloads,1);
ctx.openInline(first.button);
first.nodes.input.value="2";fail=true;
await ctx.saveInline(first.nodes.save);
assert.match(first.nodes.note.textContent,/保存失败.*HTTP 503/);
assert.equal(first.summary.textContent,"5");
assert.equal(first.row.dataset.v121InlineEditing,"1");
ctx.undoInline(first.row);
assert.equal(first.button.hidden,false);
console.log("v241 inline Worker concurrency editor and backend save contract passed");
