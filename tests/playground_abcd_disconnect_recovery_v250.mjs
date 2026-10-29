import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync("app/admin_playground_abcd_v197.js","utf8");
const start=source.indexOf("async function recoverDisconnectedStream(");
const end=source.indexOf("\nasync function stream(",start);
assert.ok(start>0&&end>start,"request-scoped recovery function must exist");

async function exercise(results) {
  let calls=0; const progress=[];const urls=[];
  const ctx={encodeURIComponent,Promise,
    api:async url=>{urls.push(url);const value=results[Math.min(calls++,results.length-1)];if(value instanceof Error)throw value;return value;},
    setTimeout:fn=>fn(),
  };
  vm.runInNewContext(source.slice(start,end)+"\nthis.recover=recoverDisconnectedStream;",ctx);
  let result,error;
  try {result=await ctx.recover("req_abcdef0123456789abcdef",(chars,frames,status)=>progress.push(status));}
  catch(e){error=String(e.message);}
  return {result,error,calls,urls,progress};
}
const ready=await exercise([
 {status:"running"},{status:"running"},
 {status:"completed",response_text:"# 完整内容\n真实结果",response_text_truncated:false},
]);
assert.equal(ready.result.text,"# 完整内容\n真实结果");
assert.equal(ready.result.recovered,true);
assert.equal(ready.calls,3);
assert.ok(ready.progress.some(x=>x.includes("仍在运行")));
assert.ok(ready.urls.every(x=>x.includes("req_abcdef0123456789abcdef") && x.endsWith("outcome?full_text=1")));
const noTerminal=await exercise([{}]);
assert.equal(noTerminal.result,null);
assert.ok(noTerminal.calls<=4);
const failed=await exercise([{status:"running"},{status:"error",error:"upstream failed"}]);
assert.match(failed.error,/upstream failed/);
const partial=await exercise([{status:"completed",response_text:"# preview",response_text_truncated:true}]);
assert.match(partial.error,/不能按已交付处理/);
const empty=await exercise([{status:"completed",response_text:""}]);
assert.match(empty.error,/不能按已交付处理/);
assert.match(source,/recovered_after_disconnect:out\.recovered===true/);
assert.match(source,/report\.warnings\.push\("至少一条请求在 SSE 提前断开/);
assert.match(source,/if\(!stopped\|\|!done\)/);
console.log("v250 ABCD disconnected SSE recovery: real terminal, failed, missing, truncated, and no-replay passed");
