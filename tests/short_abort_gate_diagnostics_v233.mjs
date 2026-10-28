import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("chrome_extension/content_request_v6.js", "utf8");
const begin = source.indexOf("  function shortAbortGateEvidence(");
const end = source.indexOf("\n  function renderedNetworkCorrelatedTurn(", begin);
assert.ok(begin > 0 && end > begin);
assert.match(source, /\.\.\.shortAbortGateEvidence\(active, current\)/);
const old = {innerText:"You said:"};
const heading = {innerText:"ChatGPT said:"};
const button = {};
const now = Date.now();
let hasCandidate = true, hasToolbar = true;
const ctx = {
  normalize:v=>String(v || "").replace(/\s+/g," ").trim(),
  visible:()=>true,
  transientText:()=>false,
  internalToolPayload:()=>false,
  document:{querySelectorAll:()=>[old,heading]},
  responseFinishActions:()=>hasToolbar ? [button] : [],
  rolelessShortReplyCandidate:()=>hasCandidate ? {text:"答案",latest:heading} : null,
  verifiedRolelessShortAbortCandidate:()=>hasCandidate && hasToolbar ? {} : null,
  verifiedShortAbortCandidate:()=>null,
  promptStillPresent:()=>false,
  upstreamStillWorking:()=>false,
};
vm.runInNewContext(source.slice(begin,end)+"\nthis.inspect=shortAbortGateEvidence;",ctx);
const active={networkStreamSeen:true,submitStartedAt:now-3000,
  networkObservedAt:now-1000,networkObservedText:"答案内容。",
  baselineRolelessHeadings:new Set(),baselineFinishActions:new Set()};
assert.equal(ctx.inspect(active,{reason:"none"}).response_recovery_short_gate,"eligible");
assert.equal(ctx.inspect({...active,networkStreamSeen:false},{}).response_recovery_short_gate,"network-not-observed");
assert.equal(ctx.inspect({...active,networkObservedAt:now-5000},{}).response_recovery_short_gate,"network-evidence-predates-submit");
assert.equal(ctx.inspect({...active,baselineRolelessHeadings:new Set([old,heading])},{}).response_recovery_short_gate,"no-fresh-assistant-heading");
hasCandidate=false;
assert.equal(ctx.inspect(active,{}).response_recovery_short_gate,"heading-pair-or-answer-node-mismatch");
hasCandidate=true;hasToolbar=false;
assert.equal(ctx.inspect(active,{}).response_recovery_short_gate,"no-fresh-completion-toolbar");
const diagnostic=ctx.inspect(active,{reason:"none"});
assert.equal(diagnostic.response_recovery_short_sse_observed_chars,5);
assert.ok(!JSON.stringify(diagnostic).includes("答案内容"),"Never log model text or prompt content");
console.log("request-scoped read-only short-abort gate diagnostics passed");
