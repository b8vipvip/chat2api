import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const content=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");
const observer=fs.readFileSync("chrome_extension/content_network_stream_recovery_v55.js","utf8");
const start=content.indexOf("  function classifySubmissionState(active) {");
const end=content.indexOf("\n  function upstreamRateLimitFailure(",start);
assert.ok(start>0 && end>start);
const ctx={
  promptStillPresent:()=>false,
  currentAssistantState:()=>({isNew:false}),
  currentUserTurn:()=>null,
  isGenerating:()=>true,
};
vm.runInNewContext(content.slice(start,end)+"\nthis.classify=classifySubmissionState;",ctx);
const submitted=Date.now()-5000;
const active={submitStartedAt:submitted,networkStreamSeen:true,
  networkStreamResponseId:"mvrq-current-1",
  networkStreamResponseAt:submitted+1000,
  networkStreamResponseStatus:200};
assert.equal(ctx.classify(active)?.reason,"request-scoped-sse-accepted");
assert.equal(ctx.classify({...active,networkStreamResponseAt:submitted-1}),null);
assert.equal(ctx.classify({...active,networkStreamResponseStatus:403}),null);
assert.equal(ctx.classify({...active,networkStreamResponseId:""}),null);
assert.equal(ctx.classify({...active,networkStreamSeen:false}),null);
assert.equal(ctx.classify({...active,submitStartedAt:0}),null);
assert.match(observer,/active\.requestId === row\.requestId/);
assert.match(observer,/active\.networkStreamResponseAt = Date\.now\(\)/);
assert.match(content,/request-scoped-sse-accepted/);
assert.ok(!observer.includes('type: "chat.completed"'));
console.log("v234 current-request SSE acceptance is only submit proof, never terminal");
