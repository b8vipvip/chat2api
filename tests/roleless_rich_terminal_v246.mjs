import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function rolelessRichReplyCandidate(active) {");
const end=source.indexOf("\n  // #535: bounded, privacy-safe diagnostics",start);
assert.ok(start>0&&end>start);
function setup(){
 const root={kind:"main"};
 const make=(tag,text,pos)=>({
   tagName:tag,innerText:text,textContent:text,pos,isConnected:true,
   parentElement:{pos:pos-0.1},nextElementSibling:null,
   closest(selector){return /main|#thread/.test(selector)?root:null;},
   querySelector(){return null;}
 });
 const historical=make("H4","ChatGPT said:",1);
 const user=make("H4","You said:",10);
 const heading=make("H4","ChatGPT said:",12);
 const response=make("DIV","第一章 施工规划。".repeat(250) + "终章：所有步骤确认结束，完整交付。",13);
 const status=make("H4","Worked for 5s",14);
 const toolbar=make("BUTTON","Copy",16);
 const staleToolbar=make("BUTTON","Copy",2);
 heading.nextElementSibling=response;
 response.nextElementSibling=status;
 const headings=[historical,user,heading,status];
 let actions=[staleToolbar,toolbar],working=false,promptHeld=false;
 const ctx={
   document:{querySelectorAll:()=>headings},normalize:v=>String(v||"").replace(/\s+/g," ").trim(),
   visible:()=>true,transientText:()=>false,internalToolPayload:()=>false,
   turnFollows:(a,b)=>Boolean(a&&b&&a.pos<b.pos),
   responseFinishActions:()=>actions,promptStillPresent:()=>promptHeld,
   upstreamStillWorking:()=>working,
 };
 vm.runInNewContext(source.slice(start,end)+"\nthis.find=rolelessRichReplyCandidate;this.verify=verifiedRolelessRichAbortCandidate;this.complete=rolelessRichCaptureComplete;",ctx);
 const active={
   networkStreamSeen:true,submitStartedAt:1000,networkObservedAt:1050,
   networkObservedText:"hello from upstream",
   baselineRolelessHeadings:new Set([historical]),
   baselineRenderedText:new Map(),
   baselineFinishActions:new Set([staleToolbar]),
   promptNormalized:"原始任务是一个不同的复杂提示词".repeat(12),
 };
 return {ctx,active,heading,response,toolbar,user,status,root,historical,
   setActions:v=>actions=v,setWorking:v=>working=v,setPrompt:v=>promptHeld=v,
   setHeadings:v=>{headings.splice(0,headings.length,...v)}};
}
{
 const f=setup();
 assert.notEqual(f.heading.parentElement,f.user.parentElement,
   "role headings may be in different wrappers");
 const c=f.ctx.find(f.active);
 assert.equal(c?.latest,f.response,"full assistant response DIV must be selected");
 assert.equal(c.text.length,f.response.innerText.length);
 const verified=f.ctx.verify(f.active,c);
 assert.equal(verified?.rolelessRichVerified,true);
 assert.equal(verified.coverage.nodes.length,1);
 assert.equal(verified.coverage.nodes[0],f.response);
 assert.equal(f.ctx.complete(f.response, f.response.innerText),true);
 assert.equal(f.ctx.complete(f.response, f.response.innerText.slice(0,-60)),false,
   "a truncated trailing section must not be delivered");
}
{
 const f=setup();f.setActions([f.toolbar]);
 const c=f.ctx.find(f.active);assert.ok(f.ctx.verify(f.active,c));
 f.setActions([]);
 assert.equal(f.ctx.verify(f.active,c),null,"new reply must have its own toolbar");
}
{
 const f=setup();f.setActions([]);
 const c=f.ctx.find(f.active);
 assert.equal(f.ctx.verify(f.active,c),null);
}
{
 const f=setup();f.setWorking(true);
 assert.equal(f.ctx.verify(f.active,f.ctx.find(f.active)),null);
}
{
 const f=setup();f.setPrompt(true);
 assert.equal(f.ctx.verify(f.active,f.ctx.find(f.active)),null);
}
{
 const f=setup();f.active.baselineRolelessHeadings.add(f.heading);
 assert.equal(f.ctx.find(f.active),null);
}
{
 const f=setup();f.active.networkObservedAt=900;
 assert.equal(f.ctx.find(f.active),null);
}
{
 const f=setup();f.response.nextElementSibling={tagName:"DIV",querySelector:()=>null};
 assert.equal(f.ctx.find(f.active),null,
   "when multiple response sibling blocks exist do not commit a truncated DIV");
}
{
 const f=setup();f.response.innerText=f.active.promptNormalized;
 assert.equal(f.ctx.find(f.active),null,"prompt echo cannot be delivered");
}
{
 const f=setup();const nextUser={
   tagName:"H4",innerText:"You said:",pos:15,isConnected:true
 };
 f.setHeadings([f.historical,f.user,f.heading,f.status,nextUser]);
 f.toolbar.pos=17;
 assert.equal(f.ctx.verify(f.active,f.ctx.find(f.active)),null,
   "toolbar after a subsequent user turn is not proof of previous answer");
}
assert.match(source,/verifiedRolelessRichAbortCandidate\(active, current\)/);
assert.match(source,/abortedRenderedCandidateSince >= 8000/);
assert.match(source,/response_terminal_source: rendered\.rolelessRichVerified/);
assert.match(source,/current\.latest === current\.rolelessHeading\.nextElementSibling/);
assert.match(source,/type: "chat.completed", request_id: active.requestId/);
console.log("v246 rich-roleless full DIV terminal: fresh bounded reply and negative guards passed");
