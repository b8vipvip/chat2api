import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("chrome_extension/content_request_v6.js", "utf8");
const begin = source.indexOf("  function rolelessShortReplyCandidate(");
const end = source.indexOf("\n  function renderedNetworkCorrelatedTurn(", begin);
assert.ok(begin > 0 && end > begin, "roleless candidate lives within the request-v6 terminal controller");
const network = fs.readFileSync("chrome_extension/content_network_stream_recovery_v55.js","utf8");
assert.ok(!network.includes('type: "chat.snapshot"'),
  "network observer is evidence only; it must not independently publish the same text");
assert.ok(network.includes("active.networkObservedText = value"),
  "the network observer must still send request-scoped evidence to v6");

const norm = v => String(v || "").replace(/\s+/g, " ").trim();
const parent = {};
function node(tag, text) {
  return {tagName:tag,innerText:text,textContent:text,isConnected:true,
    parentElement:parent,nextElementSibling:null,
    closest(){return null},querySelector(){return null}};
}
const old = node("H4","ChatGPT said:");
const user = node("H4","You said:");
const heading = node("H4","ChatGPT said:");
const body = node("P","您好，请核实手机号。");
const elapsed = node("H4","Worked for 5s");
const toolbar = node("BUTTON","Copy");
user.nextElementSibling = heading;
heading.nextElementSibling = body;
body.nextElementSibling = elapsed;
const headings=[old,user,heading,elapsed], actions=[toolbar];
const ctx={
  normalize:norm, visible:() => true, transientText:v=>/^worked for/i.test(norm(v)),
  internalToolPayload:() => false, plainNodeText:n=>n.innerText,
  turnFollows:(a,b) => (a===user && b===heading)
     || (a===heading && b===body) || (a===body && b===toolbar),
  responseFinishActions:()=>actions, promptStillPresent:()=>false,
  upstreamStillWorking:()=>false,
  document:{querySelectorAll:()=>headings},
};
vm.runInNewContext(source.slice(begin,end)+
  "\nthis.find=rolelessShortReplyCandidate; this.verify=verifiedRolelessShortAbortCandidate;",ctx);
const now=Date.now();
const active={networkStreamSeen:true,submitStartedAt:now-5000,
  networkObservedAt:now-3000,networkObservedText:"手机号。",
  baselineRolelessHeadings:new Set([old]),baselineFinishActions:new Set(),
  promptNormalized:"[User] 账户如何处理？"};
const candidate=ctx.find(active);
assert.ok(candidate);
assert.equal(candidate.text,body.innerText);
assert.equal(candidate.reason,"fresh-roleless-short-answer-v227");
const verified=ctx.verify(active,candidate);
assert.ok(verified);
assert.equal(verified.rolelessHeaderVerified,true);
assert.equal(verified.coverage.nodes[0],body);
// No false attribution from a prior window or a new assistant role-less shell
assert.equal(ctx.find({...active,baselineRolelessHeadings:new Set(headings)}),null);
assert.equal(ctx.find({...active,networkObservedText:"unrelated"}),null);
assert.equal(ctx.find({...active,networkObservedText:"手机号。",submitStartedAt:now+10000}),null);
assert.equal(ctx.verify({...active,baselineFinishActions:new Set([toolbar])},candidate),null);
assert.equal(ctx.verify(active,{...candidate,rolelessHeading:old}),null);
assert.ok(source.includes('response_terminal_source: rendered.shortTextVerified'));
assert.ok(source.includes('"fresh-roleless-short-answer-v227"'));
console.log("v227 sole-owner short roleless reply / no duplicated network snapshots passed");