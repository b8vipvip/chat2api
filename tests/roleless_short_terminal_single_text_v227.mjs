import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");
const begin=source.indexOf("  function shortRolelessSourceOverlap(");
const end=source.indexOf("\n  // #535: bounded, privacy-safe diagnostics",begin);
assert.ok(begin>0 && end>begin, "single request-v6 owner still includes v245 short reply logic");
const network=fs.readFileSync("chrome_extension/content_network_stream_recovery_v55.js","utf8");
assert.ok(!network.includes('type: "chat.snapshot"'));
assert.ok(network.includes("active.networkObservedText = value"));

const root={querySelectorAll:s=>s==="p"?[body]:[]};
const norm=v=>String(v||"").replace(/\s+/g," ").trim();
function node(tag,text,pos){
  return {tagName:tag,innerText:text,textContent:text,isConnected:true,pos,
    parentElement:{pos:pos-0.1},
    closest:s=>/main|#thread/.test(s)?root:null};
}
const old=node("H4","ChatGPT said:",1);
const user=node("H4","You said:",10);
const heading=node("H4","ChatGPT said:",11);
const body=node("P","您好，请核实手机号。",12);
const elapsed=node("H4","Worked for 5s",13);
const toolbar=node("BUTTON","Copy",14);
const headings=[old,user,heading,elapsed],actions=[toolbar];
const ctx={
  normalize:norm,visible:()=>true,transientText:v=>/^worked for/i.test(norm(v)),
  internalToolPayload:()=>false,plainNodeText:n=>n.innerText,
  turnFollows:(a,b)=>Boolean(a&&b&&a.pos<b.pos),
  responseFinishActions:()=>actions,promptStillPresent:()=>false,
  upstreamStillWorking:()=>false,document:{querySelectorAll:()=>headings},
};
vm.runInNewContext(source.slice(begin,end)+
  "\nthis.find=rolelessShortReplyCandidate;this.verify=verifiedRolelessShortAbortCandidate;",ctx);
const now=Date.now();
const active={
  networkStreamSeen:true,submitStartedAt:now-5000,networkObservedAt:now-3000,
  networkObservedText:"手机号。",baselineRolelessHeadings:new Set([old]),
  baselineFinishActions:new Set(),baselineRenderedText:new Map(),
  promptNormalized:"[User] 账户如何处理？",
};
const candidate=ctx.find(active);
assert.ok(candidate,"fresh short reply must be seen without direct sibling headings");
assert.equal(candidate.latest,body);
assert.equal(candidate.reason,"fresh-roleless-short-answer-v245");
const verified=ctx.verify(active,candidate);
assert.ok(verified);
assert.equal(verified.rolelessHeaderVerified,true);
assert.equal(verified.coverage.nodes[0],body);
assert.equal(ctx.find({...active,baselineRolelessHeadings:new Set(headings)}),null);
assert.equal(ctx.find({...active,networkObservedText:"unrelated"}),null);
assert.equal(ctx.find({...active,submitStartedAt:now+10000}),null);
assert.equal(ctx.verify({...active,baselineFinishActions:new Set([toolbar])},candidate),null);
assert.equal(ctx.verify(active,{...candidate,rolelessHeading:old}),null);
assert.ok(source.includes('response_terminal_source: rendered.shortTextVerified'));
assert.ok(source.includes('"fresh-roleless-short-answer-v245"'));
console.log("v245 short roleless reply remains single-publisher and rejects old headings/buttons");
