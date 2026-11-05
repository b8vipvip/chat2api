import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import {webcrypto} from "node:crypto";

const source=readFileSync("chrome_extension/background_media_fetch_v175.js","utf8");
const listeners=[];let attempts=0;
const raw=Uint8Array.from([255,216,255,224,10,20,30,255,217]);
runInNewContext(source,{
  URL,AbortController,Uint8Array,setTimeout,clearTimeout,btoa,crypto:webcrypto,
  chrome:{runtime:{onMessage:{addListener(fn){listeners.push(fn)}}}},
  fetch:async(_url,options)=>{
    attempts++;assert.equal(options.credentials,"omit");
    assert.equal(options.redirect,"error");
    return {ok:true,headers:{get:k=>k==="content-type"?"image/jpeg"
      :k==="content-length"?String(raw.length):null},
      arrayBuffer:async()=>raw.buffer.slice(0)};
  },
});
assert.equal(listeners.length,3);
const hashListener=listeners[1];
const trusted={url:"https://chatgpt.com/c/request",tab:{id:7}};
function send(msg,sender=trusted){
  return new Promise(resolve=>{
    const keepAlive=hashListener(msg,sender,resolve);
    if(!keepAlive)resolve({ok:false});
  });
}
for(const url of ["http://127.0.0.1/admin","https://attacker.example/secret",
                  "https://user:pass@cdn.openai.com/private"]){
  const result=await send({type:"chat2api.media.hash-v2",url});
  assert.equal(result.ok,false);
}
assert.equal(attempts,0,"never contact untrusted origins");
const result=await send({type:"chat2api.media.hash-v2",
  url:"https://media.oaiusercontent.com/c/img"});
assert.equal(result.ok,true);
assert.equal(result.bytes,raw.length);
assert.ok(/^[0-9a-f]{64}$/.test(result.sha256));
const hash=await webcrypto.subtle.digest("SHA-256",raw);
assert.equal(result.sha256,Buffer.from(hash).toString("hex"));
assert.equal(Object.hasOwn(result,"data_url"),false);
assert.equal(Object.hasOwn(result,"url"),false);
assert.equal((await send({type:"chat2api.media.hash-v2",
  url:"https://cdn.openai.com/image.jpg"},
  {url:"https://outsider.example/x",tab:{id:7}})).ok,false);
// The archive accepts up to 20 MiB originals, while byte relay remains
// capped at 4 MiB. Identity must hash a real >4 MiB image without copying
// those bytes into the extension reply. Missing Content-Length is safe only
// via a bounded streaming read.
const largeRaw=new Uint8Array(5*1024*1024);
largeRaw[0]=137;largeRaw[largeRaw.length-1]=42;
const tooBig=[new Uint8Array(19*1024*1024),new Uint8Array(2*1024*1024)];
const boundedListeners=[];
let streamWasRead=0,overflowReads=0;
runInNewContext(source,{
  URL,AbortController,Uint8Array,setTimeout,clearTimeout,btoa,crypto:webcrypto,
  chrome:{runtime:{onMessage:{addListener(fn){boundedListeners.push(fn)}}}},
  fetch:async(url,options)=>{
    assert.equal(options.credentials,"omit");
    assert.equal(options.redirect,"error");
    const oversized=url.includes("overflow");
    const unknown=url.includes("unknown-length");
    const chunks=oversized?tooBig:[largeRaw];
    let index=0;
    return {ok:true,headers:{get:key=>key==="content-type"?"image/png":
      key==="content-length"?(unknown||oversized?null:String(largeRaw.length)):null},
      body:{getReader:()=>({
        read:async()=>{
          if(index>=chunks.length)return {done:true};
          streamWasRead++;
          if(oversized)overflowReads++;
          return {done:false,value:chunks[index++]};
        },
        cancel:async()=>{},
      })},
      arrayBuffer:async()=>{throw Error("stream must not buffer unbounded response")},
    };
  },
});
assert.equal(boundedListeners.length,3);
const boundedHash=boundedListeners[1];
function sendBounded(url){
  return new Promise(resolve=>boundedHash(
    {type:"chat2api.media.hash-v2",url},trusted,resolve));
}
const largeIdentity=await sendBounded("https://media.oaiusercontent.com/large.png");
assert.equal(largeIdentity.ok,true);
assert.equal(largeIdentity.bytes,largeRaw.length);
assert.equal(largeIdentity.sha256,Buffer.from(
  await webcrypto.subtle.digest("SHA-256",largeRaw)).toString("hex"));
assert.ok(!("data_url" in largeIdentity) && !("url" in largeIdentity));
const missingLength=await sendBounded("https://media.oaiusercontent.com/unknown-length.png");
assert.equal(missingLength.ok,true);
const overflow=await sendBounded("https://media.oaiusercontent.com/overflow.png");
assert.equal(overflow.ok,false);
assert.equal(overflowReads,2);
assert.ok(streamWasRead>=4);

console.log("Original raster SHA only; no bytes or URL leave trusted background bridge");
