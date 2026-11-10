// Read-only network SSE observation must never strand a tee clone after a
// parser exception. Cancel the observer branch, not ChatGPT's native fetch.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/network_stream_main_v55.js","utf8");
const first=source.indexOf("  async function observeConversation(response, streamId, signal = null)");
const end=source.indexOf("  globalThis.fetch = async function",first);
assert.ok(first>0&&end>first);
assert.ok(!source.includes("signal.abort()"),
  "the extension must never call abort on the page's original signal");

async function check(mode) {
  const events=[],winHandlers=new Map(),docHandlers=new Map(),actions=[];
  const signal=new AbortController();
  let cloneCancel=0,nativeCancel=0,cloneRelease=0,reads=0;
  const document={
    visibilityState:"hidden",
    addEventListener(name,cb){docHandlers.set(name,cb)},
    removeEventListener(name,cb){if(docHandlers.get(name)===cb)docHandlers.delete(name)}
  };
  const reader={
    async read(){
      reads++;
      if(mode==="page-abort"){
        signal.abort();
        const err=new Error("private page info");
        err.name="AbortError";
        throw err;
      }
      if(reads>1) return {done:true};
      return {done:false,value:new TextEncoder().encode("data: {}\n\n")};
    },
    cancel(){
      actions.push("cancel-observer");
      cloneCancel++;
      return new Promise(()=>{}); // cannot await this while native branch runs
    },
    releaseLock(){actions.push("release-observer");cloneRelease++},
  };
  const response={
    ok:true,status:200,
    headers:{get:()=> "text/event-stream"},
    body:{locked:false},
    nativeCancel(){nativeCancel++},
    clone(){return {body:{getReader:()=>reader}}},
  };
  const context={
    CHANNEL:"conversation-fetch-v55",Date,document,TextDecoder,
    post:message=>events.push(message),
    contentType:()=>"text/event-stream",
    decoderFactory:()=>new TextDecoder(),
    streamParser:()=>({
      push(){
        if(mode==="parse-error") throw new SyntaxError("private answer");
      },
      peek:()=>({assistant_chars:0,assistant_role_seen:true,completion_hint:false}),
      finish:()=>({latestText:"safe model response",sawDone:true,assistantRoleSeen:true}),
    }),
    addEventListener(name,cb){winHandlers.set(name,cb)},
    removeEventListener(name,cb){if(winHandlers.get(name)===cb)winHandlers.delete(name)},
  };
  runInNewContext(source.slice(first,end)+"\nthis.observe=observeConversation",context);
  await context.observe(response,"request-bound-sse",signal.signal);
  return {events,actions,cloneCancel,nativeCancel,cloneRelease,reads,winHandlers,docHandlers,signal};
}

const broken=await check("parse-error");
assert.equal(broken.events.at(-1).phase,"error");
assert.equal(broken.events.at(-1).failure_stage,"parse");
assert.equal(broken.cloneCancel,1);
assert.equal(broken.cloneRelease,1);
assert.equal(broken.nativeCancel,0);
assert.deepEqual(broken.actions,["cancel-observer","release-observer"]);
assert.equal(broken.signal.signal.aborted,false);
assert.equal(broken.winHandlers.size,0);
assert.equal(broken.docHandlers.size,0);
assert.ok(!broken.events.some(x=>x.phase==="done"));

const abort=await check("page-abort");
assert.equal(abort.signal.signal.aborted,true);
assert.equal(abort.events.at(-1).phase,"error");
assert.equal(abort.events.at(-1).failure_stage,"read");
assert.equal(abort.cloneCancel,1);
assert.equal(abort.nativeCancel,0);
assert.equal(abort.cloneRelease,1);
assert.ok(!JSON.stringify(abort.events).includes("private page info"));

const done=await check("done");
assert.equal(done.events.at(-1).phase,"done");
assert.equal(done.cloneCancel,0);
assert.equal(done.nativeCancel,0);
assert.equal(done.cloneRelease,1);
assert.equal(done.reads,2);
console.log("v334 native SSE read-only clone cleanup: parse-error, page-owned abort, clean EOF passed");
