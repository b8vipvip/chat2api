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
console.log("Original raster SHA only; no bytes or URL leave trusted background bridge");
