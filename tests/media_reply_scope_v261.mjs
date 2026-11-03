import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

let order=0;
class Element {
  constructor(tag,text="",children=[]) {
    this.tagName=tag;this.innerText=this.textContent=text;
    this.children=children;this.parentElement=null;this.isConnected=true;this.order=order++;
    for(const child of children)child.parentElement=this;
  }
  contains(node){for(let n=node;n;n=n.parentElement)if(n===this)return true;return false;}
  matches(selector){return this.tagName==="MAIN"&&selector.includes("main");}
  closest(){return null;}
  querySelector(){return null;}
  querySelectorAll(){return this.children.flatMap(child=>[child,...child.querySelectorAll()]);}
  getBoundingClientRect(){return {width:768,height:30};}
  compareDocumentPosition(node){return node.order>this.order?4:2;}
}
const context={Element,Node:{DOCUMENT_POSITION_FOLLOWING:4},setTimeout,clearTimeout,
  chrome:{runtime:{onMessage:{addListener(){},removeListener(){}}}},
  document:{querySelectorAll:()=>[]},
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
  __CHAT2API_PROBE_SUPERVISOR_V1__:{snapshot:()=>({
    scene_changed_block_count:446,scene_uncovered_block_count:262,
    scene_uncovered_block_chars:9030,scene_finish_after_all_blocks:false,
  })},
};
runInNewContext(readFileSync("chrome_extension/content_request_v6.js","utf8"),context);
const ready=context.__CHAT2API_REQUEST_CONTENT_V6__.contract.mediaHandoffTerminalReady;
const now=40000;
const reply=new Element("DIV","The complete current assistant response");
const paragraph=new Element("P","Opening and final assistant content");
reply.children=[paragraph];paragraph.parentElement=reply;
const finish=new Element("BUTTON","Copy response");
const baseline=()=>({submitStartedAt:200,baselineCapturedAt:199,
  baselineRenderedText:new Map(),baselineFinishActions:new Set(),
  lastCandidateNode:reply,networkStreamSeen:true,networkObservedAt:now-9000,
  promptNormalized:"a different submitted prompt",options:{},
});
const boundary={replyScope:reply,finishAction:finish};
assert.equal(ready(baseline(),false,false,now),false,
  "page-wide 262 uncovered blocks must remain blocked without verified reply ownership");
const scoped=baseline();
assert.equal(ready(scoped,false,false,now,boundary),true,
  "890e: user prompt blocks outside the verified reply must not force a 900s timeout");
assert.equal(scoped.mediaTerminalReplyScopeVerified,true);
assert.equal(ready(baseline(),true,false,now,boundary),false);
assert.equal(ready(baseline(),false,true,now,boundary),false);
assert.equal(ready({...baseline(),networkObservedAt:now-1000},false,false,now,boundary),false,
  "an active SSE remains blocked even with an existing toolbar");
const stale=baseline();stale.baselineFinishActions.add(finish);
assert.equal(ready(stale,false,false,now,boundary),false,"historical toolbar is never proof");
assert.equal(ready({...baseline(),baselineCapturedAt:201},false,false,now,boundary),false);
assert.equal(ready({...baseline(),lastCandidateNode:new Element("P","other reply")},false,false,now,boundary),false);
const page=new Element("MAIN","user and assistant");
page.children=[reply];reply.parentElement=page;
assert.equal(ready(baseline(),false,false,now,{...boundary,replyScope:page}),false,
  "page-wide main cannot be used to bypass incomplete rich-content guards");
reply.parentElement=null;
reply.querySelector=()=>({user:true});
assert.equal(ready(baseline(),false,false,now,boundary),false,"mixed user/assistant scope is rejected");
reply.querySelector=()=>null;
// A wrapper can contain an old toolbar followed by more assistant content.
// Its subtree is not finished just because root precedes that toolbar.
const tail=new Element("P","More content after an earlier tool completion");
reply.children=[paragraph,finish,tail];finish.parentElement=reply;tail.parentElement=reply;
assert.equal(ready(baseline(),false,false,now,boundary),false,
  "rich text after a toolbar prevents early completion");
console.log("v261: bounded assistant scope clears prompt pollution while active/stale/unfinished replies remain blocked");
