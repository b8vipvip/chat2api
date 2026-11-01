import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import {webcrypto} from "node:crypto";

const code=readFileSync("chrome_extension/content_native_content_v1.js","utf8");
class Element {
  constructor(tagName,children=[]) {
    this.tagName=tagName;this.childNodes=children;this.isConnected=true;
  }
  getBoundingClientRect(){return {width:440,height:200};}
  matches(){return false;}
  closest(){return null;}
}
const chunked=[];
const ctx={Element,Node:{TEXT_NODE:3},Date,setTimeout,TextEncoder,crypto:webcrypto,btoa,
  getComputedStyle:()=>({display:"flex",visibility:"visible",
    fontSize:"16px",color:"#123456",lineHeight:"24px",marginBottom:"12px"}),
  chrome:{runtime:{sendMessage:async message=>{
    chunked.push(message.event);return {ok:true,committed:true};
  }}},
};
runInNewContext(code,ctx);
const groups=Array.from({length:110},(_,g)=>new Element("DIV",
  Array.from({length:130},(_,i)=>new Element("P",[{
    nodeType:3,nodeValue:"p"+g+"-"+i,
  }]))));
const root=new Element("DIV",groups);
const active={requestId:"req_chunkedv2transport",submitStartedAt:Date.now(),
  lastCandidateNode:root};
await ctx.__CHAT2API_NATIVE_CONTENT_V1__.capture(active,true);
assert.ok(chunked.length>10,chunked.length);
assert.ok(chunked.every(e=>e.type==="chat.native.chunk"));
assert.equal(chunked[0].count,chunked.length);
assert.equal(new Set(chunked.map(e=>e.revision)).size,1);
assert.equal(new Set(chunked.map(e=>e.sha256)).size,1);
assert.equal(active.nativeCaptureFailure,"");
const bytes=[];
for(let i=0;i<chunked.length;i++){
  assert.equal(chunked[i].index,i);
  bytes.push(Buffer.from(chunked[i].data,"base64"));
}
const raw=Buffer.concat(bytes);
assert.equal(raw.byteLength,chunked[0].byte_length);
const restored=JSON.parse(raw.toString("utf8"));
assert.equal(restored.root.children.length,110);
assert.equal(restored.root.children[109].children[129].children[0].text,"p109-129");
const hash=await webcrypto.subtle.digest("SHA-256",raw);
assert.equal(Buffer.from(hash).toString("hex"),chunked[0].sha256);
console.log("Large assistant reply: "+raw.length+" bytes / "+chunked.length+" authenticated chunks");
