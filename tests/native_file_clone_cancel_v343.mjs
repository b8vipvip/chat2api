// v343: A file-fetch observer must NEVER await cancellation of its cloned
// ReadableStream. The native page's own download is an independent tee branch.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/native_file_main_v210.js","utf8");
const ORIGIN="https://chatgpt.com";
const FILE="Report.xlsx";
const MIME="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const VALID=Uint8Array.from([80,75,3,4,0,1]);

async function exercise({overflowFirst=false}={}){
  const events=[],listeners=new Map(),waiters=[];
  let fetches=0,cancels=0,releases=0;
  const waitFor=predicate=>{
    const existing=events.find(predicate);
    if(existing)return Promise.resolve(existing);
    return new Promise(resolve=>waiters.push({predicate,resolve}));
  };
  const window={
    addEventListener(name,cb){listeners.set(name,cb);},
    postMessage(message){
      events.push(message);
      for(const waiter of [...waiters]){
        if(waiter.predicate(message)){
          waiters.splice(waiters.indexOf(waiter),1);
          waiter.resolve(message);
        }
      }
    },
  };
  class FakeURL extends URL{}
  FakeURL.createObjectURL=()=>`blob:${ORIGIN}/blob`;
  FakeURL.revokeObjectURL=()=>{};
  class FakeAnchor {click(){}}
  const original=[];
  const makeResponse=oversized=>{
    const bodyBytes=oversized?new Uint8Array(13*1024*1024):VALID;
    let reads=0;
    const reader={
      async read(){
        reads++;
        if(reads===1)return {done:false,value:bodyBytes};
        return {done:true};
      },
      cancel(){
        cancels++;
        // Browser/tee cancellation may not settle until the PAGE's original
        // download stream completes. Never wait for this Promise.
        return new Promise(()=>{});
      },
      releaseLock(){releases++;},
    };
    return {
      ok:true,status:200,url:"https://media.oaiusercontent.com/Report.xlsx",
      headers:{get:k=>{
        if(k==="content-type")return MIME;
        if(k==="content-disposition")return 'attachment; filename="Report.xlsx"';
        if(k==="content-length")return String(bodyBytes.length);
        return null;
      }},
      clone(){return {body:{getReader(){return reader;}}};},
    };
  };
  const context={
    window,location:{origin:ORIGIN,href:ORIGIN+"/c/verified"},
    document:{addEventListener(){}},
    URL:FakeURL,HTMLAnchorElement:FakeAnchor,
    Blob,Uint8Array,TextDecoder,TextEncoder,Date,Math,Map,Set,
    btoa,atob,queueMicrotask,setTimeout,clearTimeout,
    fetch:async(...args)=>{
      fetches++;
      const response=makeResponse(overflowFirst&&fetches===1);
      original.push(response);
      return response;
    },
  };
  runInNewContext(source,context);
  const nonce="verifiedfileabcdef123";
  listeners.get("message")({
    source:window,origin:ORIGIN,
    data:{kind:"chat2api.native-file.v210",direction:"arm",nonce,filename:FILE},
  });
  assert.ok(events.some(x=>x.direction==="armed"));
  const firstResponse=await context.fetch("https://media.oaiusercontent.com/Report.xlsx");
  assert.equal(firstResponse,original[0],
    "native download response must remain untouched and consumable by ChatGPT");
  if(overflowFirst){
    const overflow=await Promise.race([
      waitFor(x=>x.direction==="probe"&&x.stage==="fetch-size-exceeded"),
      new Promise((_,reject)=>setTimeout(
        ()=>reject(Error("oversized file clone hung during cancellation")),1500)),
    ]);
    assert.equal(overflow.stage,"fetch-size-exceeded");
    // Finish the same observer's finally block before retrying the
    // already-armed request; no second user click or new request terminal.
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(events.filter(x=>x.direction==="captured").length,0);
    assert.equal(cancels,1,"only the failed clone should be cancelled");
  }
  const actual=overflowFirst
    ?await context.fetch("https://media.oaiusercontent.com/Report.xlsx")
    :firstResponse;
  assert.equal(actual,original[original.length-1]);
  const captured=await Promise.race([
    waitFor(x=>x.direction==="captured"),
    new Promise((_,reject)=>setTimeout(
      ()=>reject(Error("valid XLSX fetch did not emit bytes; cancellation hung")),1500)),
  ]);
  assert.equal(captured.filename,FILE);
  assert.equal(captured.bytes,VALID.length);
  assert.equal(Buffer.from(captured.base64,"base64").toString("hex"),
    Buffer.from(VALID).toString("hex"));
  assert.equal(fetches,overflowFirst?2:1);
  assert.equal(events.filter(x=>x.direction==="captured").length,1);
  assert.equal(cancels,overflowFirst?1:0,
    "clean clone EOF must not trigger reader.cancel()");
  assert.equal(releases,overflowFirst?2:1,
    "every clone must releaseLock irrespective of EOF or oversized failure");
  assert.ok(!source.includes("chat.completed"),
    "this file observer has zero business-terminal authority");
}
await exercise();
await exercise({overflowFirst:true});
console.log("v343: clean file clone EOF emits bytes; overflow cleanup cannot block verified same-arm retry");
