import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function shortRolelessSourceOverlap(answer, observed) {");
const end=source.indexOf("\n  // #535: bounded, privacy-safe diagnostics",start);
assert.ok(start>=0 && end>start, "new roleless v245 routines must exist");
const code=source.slice(start,end);

function node(tag,text,pos,root,connected=true){
  return {
    tagName:tag, innerText:text, textContent:text, pos, isConnected:connected,
    parentElement:{tagName:"DIV",pos:pos-0.1},
    closest(selector){return /main|#thread/.test(selector) ? root : null;},
  };
}
function fixture(answer="亲，好的。请稍候", observed="好的。"){
  const root={
    tagName:"MAIN",
    querySelectorAll(selector){return selector==="p" ? paragraphs : [];},
  };
  const oldUser=node("H4","You said:",1,root);
  const oldAssistant=node("H4","ChatGPT said:",2,root);
  const oldAnswer=node("P","历史答复，已经完成。",3,root);
  const user=node("H4","You said:",10,root);
  const echoedPrompt=node("P","用户当前请求的多行内容",11,root);
  const assistant=node("H4","ChatGPT said:",12,root);
  const response=node("P",answer,13,root);
  const worked=node("H4","Worked for 5s",14,root);
  const button=node("BUTTON","Copy",15,root);
  const oldButton=node("BUTTON","Copy",4,root);
  // Different wrapper objects deliberately model the new ChatGPT markup:
  // the user H4, assistant H4 and reply P are not direct siblings.
  assert.notEqual(user.parentElement,assistant.parentElement);
  assert.notEqual(assistant.parentElement,response.parentElement);
  const headings=[oldUser,oldAssistant,user,assistant,worked];
  const paragraphs=[oldAnswer,echoedPrompt,response];
  let actions=[oldButton,button],working=false,promptHeld=false;
  const ctx={
    normalize:v=>String(v||"").replace(/\s+/g," ").trim(),
    visible:()=>true, transientText:()=>false, internalToolPayload:()=>false,
    plainNodeText:n=>n.innerText, 
    turnFollows:(a,b)=>Boolean(a&&b&&a.pos<b.pos),
    promptStillPresent:()=>promptHeld,upstreamStillWorking:()=>working,
    document:{querySelectorAll:()=>headings},
    responseFinishActions:()=>actions,
  };
  vm.runInNewContext(code+"\nthis.findCandidate=rolelessShortReplyCandidate;\nthis.verifyCandidate=verifiedRolelessShortAbortCandidate;",ctx);
  const active={
    networkStreamSeen:true,submitStartedAt:1000,networkObservedAt:1200,
    networkObservedText:observed,promptNormalized:"用户当前请求的多行内容",
    baselineRolelessHeadings:new Set([oldUser,oldAssistant]),
    baselineRenderedText:new Map([[oldAnswer,oldAnswer.innerText]]),
    baselineFinishActions:new Set([oldButton]),
  };
  return {
    ctx,active,root,response,button,user,assistant,oldAnswer,
    setActions:v=>{actions=v;},setWorking:v=>{working=v;},
    setPromptHeld:v=>{promptHeld=v;},
  };
}

// The five uploaded errors all have <=41-char assistant paragraphs with an
// exact (but sometimes truncated) network text. A 3-character answer is valid.
for(const [answer,observed] of [
  ["还没收到验证码的话，先别着急，等收到后发我。","还没收到验证码的话，先别着"],
  ["手机号收到了，接下来输入验证码并继续充值。","手机号收到了，接下来"],
  ["您是兑换链接打不开，还是点开后进不去呢？","您是兑换链接打不开"],
  ["亲，好的。请稍候","好的。"],
  ["创维电视需要我的音乐，拍照发我确认下。","拍照发我确认下"],
]){
  const f=fixture(answer,observed);
  const current=f.ctx.findCandidate(f.active);
  assert.equal(current?.latest,f.response,"new short answer must be selected, not old prompt text");
  assert.equal(current?.reason,"fresh-roleless-short-answer-v245");
  assert.equal(f.ctx.verifyCandidate(f.active,current)?.shortTextVerified,true);
  assert.equal(f.ctx.verifyCandidate(f.active,current)?.rolelessHeaderVerified,true);
}
// A short SSE tail can omit a linking word but still correlate to the exact
// new reply: neither old prompt nor old toolbar alone is sufficient.
{
  const f=fixture("手机号收到了，接下来输入验证码并继续充值。",
                  "手机号收到了，接下来验证码并继续充值。");
  assert.equal(f.ctx.findCandidate(f.active)?.latest,f.response);
}
{
  const f=fixture("您是兑换链接打不开，还是点进去后进不去呢？",
                  "您是兑换链接打不开，时进不去呢？");
  assert.equal(f.ctx.findCandidate(f.active)?.latest,f.response);
}
// A two-paragraph response must NOT be truncated by this one-paragraph
// recovery path, even if its first paragraph overlaps SSE text.
{
  const f=fixture();
  const original=f.root.querySelectorAll.bind(f.root);
  const more=node("P","第二段仍然属于当前回答。",13.5,f.root);
  f.root.querySelectorAll=selector=>selector==="p"
    ? [...original(selector),more] : original(selector);
  assert.equal(f.ctx.findCandidate(f.active),null);
}
// No stale answer or old toolbar may complete the new request.
{
  const f=fixture();
  f.setActions([node("BUTTON","Copy",4,f.root)]);
  const current=f.ctx.findCandidate(f.active);
  assert.ok(current);
  assert.equal(f.ctx.verifyCandidate(f.active,current),null);
}
{
  const f=fixture();
  f.setWorking(true);
  const current=f.ctx.findCandidate(f.active);
  assert.equal(f.ctx.verifyCandidate(f.active,current),null);
}
{
  const f=fixture();
  f.setPromptHeld(true);
  const current=f.ctx.findCandidate(f.active);
  assert.equal(f.ctx.verifyCandidate(f.active,current),null);
}
// No matching current-network fragment, no short reply candidate.
{
  const f=fixture("这是另一条无关的历史页面消息","好的。");
  assert.equal(f.ctx.findCandidate(f.active),null);
}
// Network evidence must be newer than the actual submission.
{
  const f=fixture();
  f.active.networkObservedAt=999;
  assert.equal(f.ctx.findCandidate(f.active),null);
}
// A historical role heading is not a current boundary.
{
  const f=fixture();
  f.active.baselineRolelessHeadings.add(f.assistant);
  assert.equal(f.ctx.findCandidate(f.active),null);
}
// When the next fresh user turn starts, its following paragraph is not the
// answer to the previous assistant heading.
{
  const f=fixture();
  const next=node("H4","You said:",12.5,f.root);
  f.ctx.document.querySelectorAll=()=>[f.user,f.assistant,next];
  assert.equal(f.ctx.findCandidate(f.active),null);
}
// Terminal owner remains request-v6 and retains 8s stability / working guard.
assert.match(source,/abortedRenderedCandidateSince >= 8000/);
assert.match(source,/rendered\.rolelessHeaderVerified \? 3 : 8/);
assert.match(source,/response_terminal_stable_ms:/);
assert.match(source,/type: "chat.completed", request_id: active.requestId/);
console.log("v245 roleless short reply: fresh nested DOM, short 3-char answers and unsafe fallbacks passed");
