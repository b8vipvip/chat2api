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

let nested=new Element("P",[{nodeType:3,nodeValue:"deep"}]);
for(let i=0;i<15;i++)nested=new Element("DIV",[nested]);
assert.equal(capture.snapshot(candidateFor([nested])),null,"over-depth text must fail closed");

const overChildren=candidateFor(Array.from({length:81},(_,i)=>({
  nodeType:3,nodeValue:"chunk "+i,
})));
assert.equal(capture.snapshot(overChildren),null,"over 80 children must fail closed");

const overImages=candidateFor(Array.from({length:41},()=>new Element("IMG")));
assert.equal(capture.snapshot(overImages),null,"over 40 images must fail closed");
