import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync("app/admin_playground_chat_v69.js","utf8");
const start=source.indexOf("  async function recoverAuthoritativeResult(requestId,");
const end=source.indexOf("\n  async function recordChatTurn(",start);
assert.ok(start>0 && end>start);
async function scenario(responses, waitForTerminal=true) {
  let calls=0;
  const statuses=[];
  const ctx={
    api:async()=>{ const result=responses[Math.min(calls++,responses.length-1)];return result; },
    status:(...args)=>statuses.push(args),
    setTimeout:(fn)=>fn(),
    fetch:async()=>({ok:false}),
    key:()=>"admin",
    trustedRecoveryBlobs:new Set(),URL,Date,console,
  };
  vm.runInNewContext(source.slice(start,end)+"\nthis.recover=recoverAuthoritativeResult;",ctx);
  const result=await ctx.recover("req_abcdefghijklmnop12345",{waitForTerminal});
  return {result,calls,statuses};
}
const longText="# 完整长篇方案\n"+("项目数据与策略、表格、公式和详细说明。\n".repeat(1500));
const complete=await scenario([
  {status:"running"},{status:"running"},
  {status:"completed",request_id:"req_abcdefghijklmnop12345",response_text:longText,
    response_text_truncated:false},
]);
assert.equal(complete.result.terminalStatus,"completed");
assert.equal(complete.result.content,longText);
assert.equal(complete.calls,3);
assert.ok(complete.statuses.some(([m])=>m.includes("仍在执行")));
const failure=await scenario([{status:"running"},{status:"error",error:"Worker timed out"}]);
assert.equal(failure.result.terminalStatus,"error");
assert.equal(failure.result.error,"Worker timed out");
const notFinished=await scenario([{status:"running"}],false);
assert.equal(notFinished.result.pending,true);
const noRecord=await scenario([{}]);
assert.equal(noRecord.result,null);
assert.equal(noRecord.calls,4);
assert.match(source,/if \(recovered\?\.terminalStatus === "completed"\)/);
assert.match(source,/recovered\?\.pending/);
assert.match(source,/include_in_context:!imageMode && !recovered.imageCount && !recovered.textTruncated/);
assert.ok(!source.includes('fetch("/v1/chat/completions"'));
console.log("v236 admin response recovery waits on Broker terminal and restores full text");
