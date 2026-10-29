import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function shortRolelessSourceOverlap(answer, observed) {");
const end=source.indexOf("\n  // #535: bounded, privacy-safe diagnostics",start);
assert.ok(start>0 && end>start,"all v248 single-owner helpers must exist");

function fixture(answer="处理完成了，您现在可以使用这个功能。",network="现在可以使用这个功能。") {
 const root={id:"main"};
 const create=(tag,value,pos)=>({
   tagName:tag,innerText:value,textContent:value,pos,isConnected:true,
   nextElementSibling:null,parentElement:{id:"different-wrapper",pos:pos-.1},
   closest:q=>/main|#thread/.test(q)?root:null,
   querySelector:()=>null,querySelectorAll:()=>[],
 });
 const stale=create("H4","ChatGPT said:",1);
 const user=create("H4","You said:",10);
 const assistant=create("H4","ChatGPT said:",12);
 const div=create("DIV",answer,13);
 const after=create("H4","Worked for 5s",14);
 const toolbar=create("BUTTON","Copy",16);
 const staleToolbar=create("BUTTON","Copy",2);
 assistant.nextElementSibling=div;
 div.nextElementSibling=after;
 let headings=[stale,user,assistant,after],actions=[staleToolbar,toolbar],
     working=false,promptPresent=false;
 const ctx={
   normalize:x=>String(x||"").replace(/\s+/g," ").trim(),
   document:{querySelectorAll:()=>headings},
   visible:()=>true,transientText:()=>false,internalToolPayload:()=>false,
   turnFollows:(a,b)=>Boolean(a&&b&&a.pos<b.pos),
   responseFinishActions:()=>actions,
   promptStillPresent:()=>promptPresent,upstreamStillWorking:()=>working,
 };
 vm.runInNewContext(source.slice(start,end)+
   "\nthis.candidate=rolelessRichReplyCandidate;this.verify=verifiedRolelessRichAbortCandidate;this.complete=rolelessRichCaptureComplete;",ctx);
 const active={
   networkStreamSeen:true,submitStartedAt:1000,networkObservedAt:1100,
   networkObservedText:network,
   baselineRolelessHeadings:new Set([stale]),
   baselineRenderedText:new Map(),
   baselineFinishActions:new Set([staleToolbar]),
   promptNormalized:"另一个完整的中文用户提问",
 };
 return {ctx,active,user,assistant,div,toolbar,stale,
   setActions:x=>{actions=x;},setWorking:x=>{working=x;},
   setPrompt:x=>{promptPresent=x;},setHeadings:x=>{headings=x;}};
}

// The uploaded v0.22.247 failure has one new H4 user, H4 assistant and
// a 32-character next-sibling DIV made from two paragraphs; old P-only path
// and >=240-char rich-DIV path both discarded it.
for(const [answer,network] of [
 ["处理完成了，您现在可以使用这个功能。","现在可以使用这个功能。"],
 ["请重新打开一下，然后填写最新验证码即可。","填写最新验证码即可。"],
 ["好的。","好的。"],
]) {
 const f=fixture(answer,network);
 const current=f.ctx.candidate(f.active);
 assert.equal(current?.latest,f.div);
 assert.equal(current?.reason,"fresh-roleless-compact-div-v248");
 const checked=f.ctx.verify(f.active,current);
 assert.equal(checked?.rolelessRichVerified,true);
 assert.equal(checked?.shortTextVerified,true);
 assert.equal(checked?.rolelessHeaderVerified,true);
 assert.equal(checked.coverage.nodes.length,1);
 assert.equal(f.ctx.complete(f.div,f.div.innerText),true);
}

// Must never use a 32-char page DIV just because it exists.
{
 const f=fixture("不相关的回复正文内容示例。","您收到验证码了吗？");
 assert.equal(f.ctx.candidate(f.active),null,"SSE content must overlap the current reply");
}
{
 const f=fixture();
 f.active.baselineRolelessHeadings.add(f.assistant);
 assert.equal(f.ctx.candidate(f.active),null,"old assistant header rejected");
}
{
 const f=fixture();
 const c=f.ctx.candidate(f.active);
 f.setActions([]);
 assert.equal(f.ctx.verify(f.active,c),null,"new completed toolbar required");
}
{
 const f=fixture();
 f.setWorking(true);
 assert.equal(f.ctx.verify(f.active,f.ctx.candidate(f.active)),null);
}
{
 const f=fixture();
 f.setPrompt(true);
 assert.equal(f.ctx.verify(f.active,f.ctx.candidate(f.active)),null);
}
{
 const f=fixture();
 f.div.nextElementSibling={tagName:"DIV",querySelector:()=>null};
 assert.equal(f.ctx.candidate(f.active),null,"multiple sibling sections not eligible");
}
{
 const f=fixture();
 f.active.networkObservedAt=900;
 assert.equal(f.ctx.candidate(f.active),null);
}
assert.match(source,/rendered\.shortTextVerified\s*\?/);
assert.match(source,/abortedRenderedCandidateSince >= abortedReplyStableThreshold\(rendered\)/);
assert.match(source,/return rendered\?\.shortTextVerified \? 4500 : 8000/);
assert.match(source,/fresh-roleless-compact-div-v248/);
assert.match(source,/type: "chat.completed", request_id: active.requestId/);
console.log("v248 compact DIV: real short answers, stale/foreign/working and partial guards passed");
