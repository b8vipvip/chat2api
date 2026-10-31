import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const src=readFileSync("app/admin_playground_abcd_v197.js","utf8");
const legacy=readFileSync("app/admin_playground_chat.js","utf8");
assert.match(src,/function safeProgress\(update,chars,frames,status\)/);
assert.match(src,/if\(panel\)panel\.textContent=s/);
assert.match(legacy,/if \(box\) box\.scrollTop = box\.scrollHeight/);
const helperStart=src.indexOf("function safeProgress(");
const helperEnd=src.indexOf("\nconst el=",helperStart);
const streamStart=src.indexOf("async function stream(");
const streamEnd=src.indexOf("\nconst images=",streamStart);
assert.ok(helperStart>0 && helperEnd>helperStart && streamStart>0 && streamEnd>streamStart);

const data=chunk=>"data: "+JSON.stringify({choices:[{delta:{content:chunk},finish_reason:null}]})+"\n\n";
const raw=[data("first"),data(" second"),"data: "+JSON.stringify({choices:[{delta:{},finish_reason:"stop"}]})+"\n\n"+"data: [DONE]\n\n"];
let reads=0,cancelled=0,progressFailures=0;
const mock={
  TextDecoder,Promise,Error,JSON,String,
  fetch:async()=>({ok:true,body:{getReader(){return {
    async read(){return reads<raw.length?{done:false,value:new TextEncoder().encode(raw[reads++])}:{done:true}},
    async cancel(){cancelled++},
  }}}}),
  setTimeout,
};
runInNewContext(src.slice(helperStart,helperEnd)+"\n"+src.slice(streamStart,streamEnd)+"\nthis.openStream=stream",mock);
const result=await mock.openStream("gpt-6",{token:"test-key"},"req_progress_detached",[],()=>{
  progressFailures++;throw new Error("admin view was unmounted");
});
assert.equal(result.text,"first second");
assert.equal(result.frames,3);
assert.equal(reads,3,"UI progress errors must never close SSE before [DONE]");
assert.equal(progressFailures,3);
assert.equal(cancelled,1);
console.log("SSE progress presentation cannot cancel active reply delivery");
