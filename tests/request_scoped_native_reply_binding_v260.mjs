import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function bindNativeReplyScope(active, phase");
const end=source.indexOf("  // A redesigned reply can render one Markdown/code/table component",start);
assert.ok(start>0 && end>start,"request binding must live in request-v6");
const guardStart=source.indexOf("  function hasUntrustedReplyInput(scope) {");
const guardEnd=source.indexOf("  // f7c8:",guardStart);
assert.ok(guardStart>0 && guardEnd>guardStart,
  "the one request-v6 editor-aware composer guard must be included");
const binder=source.slice(guardStart,guardEnd)+source.slice(start,end)
  +"\nthis.bind=bindNativeReplyScope;";

class Element {
  constructor(text="") {this.innerText=text;this.textContent=text;this.isConnected=true;this.user=false;}
  matches(){return false;}
  querySelectorAll(){return [];}
  closest(selector){
    if(this.user && selector.includes("data-message-author-role"))return {user:true};
    if(selector.includes("main,[role='main'],#thread"))return main;
    return null;
  }
}
const main=new Element();
const candidate=new Element("Safe assistant response with tables and files");
const action=new Element();
const stale=new Element();
let actions=[action],scopeOwner=null,newTurn=true;
const reply=new Element("Safe assistant response with tables and files");
reply.contains=node=>node===candidate;
reply.querySelector=()=>null;
const scope={
  Element,
  currentAssistantState:()=>({isNew:newTurn,latest:candidate}),
  responseFinishActions:()=>actions,
  replyScopeForFinishAction:(_candidate,button)=>button===action?scopeOwner:null,
  turnFollows:()=>true,
  visible:()=>true,
  normalize:value=>String(value||"").replace(/\s+/g," ").trim(),
};
scopeOwner=reply;
runInNewContext(binder,scope);

const baseline={
  requestId:"req_exact123456",
  submitStartedAt:200,baselineCapturedAt:199,baselineFinishActions:new Set([stale]),
  networkStreamSeen:true,networkObservedAt:205,promptNormalized:"A distinct user input",
  cancelled:false,lastCandidateNode:candidate,
};
const make=()=>({...baseline,baselineFinishActions:new Set([stale])});
const good=make();
assert.equal(scope.bind(good,"terminal"),true);
assert.equal(good.nativeVerifiedReplyScope,reply);
assert.match(good.nativeBindingEvidence,/same-request-baseline/);
assert.equal(good.nativeBindingPhase,"terminal");

const noNetwork=make();noNetwork.networkStreamSeen=false;
assert.equal(scope.bind(noNetwork),false);
const preSubmitNetwork=make();preSubmitNetwork.networkObservedAt=199;
assert.equal(scope.bind(preSubmitNetwork),false);
const missingBaseline=make();missingBaseline.baselineCapturedAt=0;
assert.equal(scope.bind(missingBaseline),false);
newTurn=false;
assert.equal(scope.bind(make()),false,"old assistant node is never rebound");
newTurn=true;
actions=[stale];
assert.equal(scope.bind(make()),false,"a baseline toolbar is not fresh");
actions=[action];
candidate.user=true;
assert.equal(scope.bind(make()),false,"user message candidate must be rejected");
candidate.user=false;
reply.querySelector=selector=>selector.includes("user")?{user:true}:null;
assert.equal(scope.bind(make()),false,"contaminated reply scope must be rejected");
reply.querySelector=()=>null;
reply.matches=selector=>selector.includes("main");
assert.equal(scope.bind(make()),false,"page-wide chat root must be rejected");
reply.matches=()=>false;
reply.innerText="A distinct user input";
assert.equal(scope.bind(make()),false,"current prompt echoed inside scope must be rejected");
reply.innerText="Safe assistant response with tables and files";
const rolelessUser=new Element("You said:");
reply.querySelectorAll=()=>[rolelessUser];
assert.equal(scope.bind(make()),false,"roleless user heading inside scope must be rejected");
reply.querySelectorAll=()=>[];
const voice=make();voice.options={mode:"voice"};
assert.equal(scope.bind(voice),false,"voice requests must remain on their existing path");
const another=new Element("other reply scope");
another.contains=n=>n===candidate;another.querySelector=()=>null;
scope.replyScopeForFinishAction=(_candidate,button)=>button===action?reply:another;
actions=[action,new Element("another toolbar")];
assert.equal(scope.bind(make()),false,"ambiguous scopes must be rejected");

// A redesigned roleless reply has its toolbar as a sibling. Its shared
// ancestor contains the prompt, so binding must use the positively verified
// whole assistant DIV, never that common ancestor or a nested paragraph.
actions=[action];
scope.currentAssistantState=()=>({isNew:true,latest:candidate,reason:"fresh-roleless-rich-answer-v246"});
scope.verifiedRolelessRichAbortCandidate=()=>({finishAction:action});
scope.replyScopeForFinishAction=()=>null;
candidate.contains=node=>node===candidate;
candidate.querySelector=()=>null;
candidate.querySelectorAll=()=>[];
const roleless=make();
assert.equal(scope.bind(roleless),true);
assert.equal(roleless.nativeVerifiedReplyScope,candidate);
// A verified full answer DIV with two fresh controls must not be rejected
// merely because another action's *ancestor* differs from the exact reply.
// Do not broaden the rule to unverified roleless candidates.
const auxiliary=new Element("Other action toolbar");
scope.verifiedRolelessRichAbortCandidate=()=>({
  finishAction:action,rolelessRichVerified:true,
});
scope.replyScopeForFinishAction=(_candidate,button)=>button===auxiliary?another:null;
actions=[action,auxiliary];
const multiple=make();
assert.equal(scope.bind(multiple,"terminal"),true,
  "verified entire reply DIV must survive multiple auxiliary toolbars");
assert.equal(multiple.nativeVerifiedReplyScope,candidate);
assert.equal(multiple.nativeScopeGateFacts?.eligible_scope_count,2);
assert.equal(multiple.nativeScopeGateFacts?.roleless_reply_verified,true);
const notIndependent=make();
scope.verifiedRolelessRichAbortCandidate=()=>({finishAction:action});
assert.equal(scope.bind(notIndependent),false,
  "without independent roleless terminal proof, ambiguous scopes remain rejected");
assert.equal(notIndependent.nativeScopeGateReason,"ambiguous-reply-scopes");
assert.equal(notIndependent.nativeScopeGateFacts?.eligible_scope_count,2);
actions=[action];
scope.verifiedRolelessRichAbortCandidate=()=>null;
assert.equal(scope.bind(make()),false,"unverified roleless fragment remains rejected");
assert.ok(source.includes('bindNativeReplyScope(active, "terminal")'));
assert.ok(source.includes('bindNativeReplyScope(active, "progress")'));
assert.ok(source.includes('if (active.nativeVerifiedReplyScope?.isConnected)'));

const climbBegin=source.indexOf("  function replyScopeForFinishAction(candidate, button)");
const climbEnd=source.indexOf("  // Request-scoped native reply binding",climbBegin);
assert.ok(climbBegin>0&&climbEnd>climbBegin);
const scopeRuntime={turnFollows:()=>true};
runInNewContext(source.slice(climbBegin,climbEnd)+
  "\nthis.resolveReplyScope=replyScopeForFinishAction;",scopeRuntime);
class DeepNode {
  constructor(type,parent=null){
    this.tagName=type;this.parentElement=parent;this.children=[];
    if(parent)parent.children.push(this);
  }
  contains(target){for(let n=target;n;n=n.parentElement)if(n===this)return true;return false;}
  matches(selector){return this.tagName==="MAIN"&&selector.includes("main");}
  querySelector(){return null;}
  getAttribute(key){return key==="aria-label"?"Copy":"";}
  closest(){return null;}
}
const chatPage=new DeepNode("MAIN");
const deepReply=new DeepNode("DIV",chatPage);
let inner=deepReply;
for(let depth=0;depth<38;depth++)inner=new DeepNode("DIV",inner);
const deepCandidate=new DeepNode("P",inner);
const deepCopyButton=new DeepNode("BUTTON",deepReply);
assert.equal(scopeRuntime.resolveReplyScope(deepCandidate,deepCopyButton),deepReply,
  "35+ nested rich reply must bind to its toolbar without returning page root");
deepReply.querySelector=()=>({user:true});
assert.equal(scopeRuntime.resolveReplyScope(deepCandidate,deepCopyButton),null,
  "deep reply scope containing an explicit user message must remain rejected");

console.log("v260 request-bound native ownership, deep rich container, and negative guards passed");
