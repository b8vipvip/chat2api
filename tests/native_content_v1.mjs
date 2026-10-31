import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const code = readFileSync("chrome_extension/content_native_content_v1.js","utf8");
const sent = [];
class Element {
  constructor(tagName, children=[]) { this.tagName=tagName; this.childNodes=children;
    this.isConnected=true; this.parentElement=null;
    for(const c of children) if(c instanceof Element)c.parentElement=this;
  }
  getBoundingClientRect(){return {width:100,height:100};}
  matches(s){return false;}
  closest(s){return null;}
}
const text={nodeType:3,nodeValue:"Hello safe rich text"};
const img=new Element("IMG");
const node=new Element("DIV",[text,img]);
const ctx={Element,Node:{TEXT_NODE:3},Date,setTimeout,
 getComputedStyle:()=>({display:"flex",visibility:"visible",flexDirection:"row",
    gap:"8px",color:"rgb(0, 0, 0)",backgroundImage:"url(https://secret.example/token)"}),
 chrome:{runtime:{sendMessage:async m=>{sent.push(m);return {ok:true}}}},
};
runInNewContext(code,ctx);
const capture=ctx.__CHAT2API_NATIVE_CONTENT_V1__;
assert.ok(capture);
const active={requestId:"req_native1234567",submitStartedAt:Date.now(),lastCandidateNode:node};
await capture.capture(active,true);
assert.equal(sent.length,1);
const payload=sent[0].event;
assert.equal(payload.type,"chat.native.snapshot");
assert.equal(payload.snapshot.root.children[0].text,"Hello safe rich text");
assert.equal(payload.snapshot.root.children[1].media_index,0);
assert.ok(!JSON.stringify(payload).includes("secret.example"));
// The full reply can consist of several sibling cards while the request-v6
// candidate points only at a paragraph in the first card. A verified fresh
// assistant heading scope must include all sibling cards and their images.
const scopedSent=[];
const scopeNode=new Element("DIV",[
  new Element("DIV",[{nodeType:3,nodeValue:"GameFlow category card"}]),
  new Element("IMG"),
  new Element("DIV",[{nodeType:3,nodeValue:"Second game example"}]),
]);
const scopedCtx={...ctx,__CHAT2API_NATIVE_DOM_EVIDENCE_V1__:{
  replyRoots:()=>({scope:"roleless_reply_siblings",roots:[scopeNode],boundary:"end_of_reply_parent"})
},chrome:{runtime:{sendMessage:async m=>{scopedSent.push(m);return {ok:true};}}}};
delete scopedCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,scopedCtx);
const scopedCandidate=scopeNode.childNodes[0];
await scopedCtx.__CHAT2API_NATIVE_CONTENT_V1__.capture({
  requestId:"req_scopedrich12345",submitStartedAt:Date.now(),lastCandidateNode:scopedCandidate
},true);
assert.equal(scopedSent.length,1);
const scopedRoot=scopedSent[0].event.snapshot.root;
assert.equal(scopedRoot.children[0].children.length,3,
  "sibling groups and images cannot be omitted just because the candidate is a nested paragraph");
assert.equal(scopedRoot.children[0].children[1].media_index,0);
// Modern ChatGPT layout may have no "ChatGPT said" H4. request-v6 can
// positively verify this request's own container by its finish toolbar.
// The visual sidecar must use exactly the same trusted scope as the DOM
// evidence recorder, otherwise the latter sees images but MV3C gets none.
const verifiedSent=[];
const verifiedCtx={...ctx,__CHAT2API_NATIVE_DOM_EVIDENCE_V1__:{
  replyRoots:()=>({scope:"verified_reply_container",
    roots:[scopeNode],boundary:"same_request_verified_finish_action"}),
},chrome:{runtime:{sendMessage:async msg=>{verifiedSent.push(msg);return {ok:true}}}}};
delete verifiedCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,verifiedCtx);
await verifiedCtx.__CHAT2API_NATIVE_CONTENT_V1__.capture({
  requestId:"req_verifiedrich12345",submitStartedAt:Date.now(),
  lastCandidateNode:scopeNode.childNodes[0],
},true);
assert.equal(verifiedSent.length,1);
assert.equal(verifiedSent[0].event.snapshot.root.children[0].children.length,3);
assert.equal(verifiedSent[0].event.snapshot.root.children[0].children[1].media_index,0);

await capture.capture(active,false);
assert.equal(sent.length,1);
console.log("Native rich observer: bounded capture, no DOM URLs, independent request status");

function candidateFor(children) {
  return {requestId:"req_boundedtest1234",submitStartedAt:Date.now(),
    lastCandidateNode:new Element("DIV",children)};
}
// Native snapshots must not silently omit trailing content. The canonical
// Markdown path remains the sole complete answer when DOM caps are reached.
const overNodes=candidateFor(Array.from({length:20},(_,group)=>
  new Element("DIV",Array.from({length:80},(_,i)=>({
    nodeType:3,nodeValue:"paragraph "+group+"-"+i,
  })))));
assert.equal(capture.snapshot(overNodes),null,"over 1200 nodes must fail closed");
await capture.capture(overNodes,true);
assert.equal(sent.length,1,"truncated content must never be published");

// A complex reply that previously exceeded 420 nodes is still bounded but
// must now retain every safe text block instead of falling back prematurely.
const largeValid=candidateFor(Array.from({length:10},(_,group)=>
  new Element("DIV",Array.from({length:60},(_,i)=>({
    nodeType:3,nodeValue:"full block "+group+"-"+i,
  })))));
const expanded=capture.snapshot(largeValid);
assert.ok(expanded && expanded.root.children.length === 10,
  "600+ node rich page must be representable");

const overText=candidateFor([{nodeType:3,nodeValue:"A".repeat(96001)}]);
assert.equal(capture.snapshot(overText),null,"over 96K text must fail closed");
await capture.capture(overText,true);
assert.equal(sent.length,1);

// Production f7c8: full source DOM has 16+ nested presentation levels,
 // 4 images, a table and grid wrappers. Previously 14-depth rejected the
 // complete final reply and left an older 90-node/zero-image sidecar.
let nested=new Element("DIV",[new Element("IMG"),new Element("P",[
  {nodeType:3,nodeValue:"deep card text"},
])]);
for(let i=0;i<17;i++)nested=new Element("DIV",[nested]);
const deepReply=capture.snapshot(candidateFor([nested]));
assert.ok(deepReply,"bounded 17-deep visual cards must be serialized");
function findImgs(node) {
  return (node.tag==="img"?1:0)+(node.children||[]).reduce((n,child)=>n+findImgs(child),0);
}
assert.equal(findImgs(deepReply.root),1,"deep image ordinal must remain in final tree");
let overDeep=new Element("P",[{nodeType:3,nodeValue:"over-depth"}]);
for(let i=0;i<26;i++)overDeep=new Element("DIV",[overDeep]);
assert.equal(capture.snapshot(candidateFor([overDeep])),null,
  "more than 24 nested levels must still fail closed");

const overChildren=candidateFor(Array.from({length:81},(_,i)=>({
  nodeType:3,nodeValue:"chunk "+i,
})));
assert.equal(capture.snapshot(overChildren),null,"over 80 children must fail closed");

const overImages=candidateFor(Array.from({length:41},()=>new Element("IMG")));
assert.equal(capture.snapshot(overImages),null,"over 40 images must fail closed");
