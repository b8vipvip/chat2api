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
