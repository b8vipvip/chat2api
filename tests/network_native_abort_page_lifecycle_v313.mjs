import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/network_stream_main_v55.js","utf8");
const start=source.indexOf("  async function observeConversation(response, streamId, signal = null)");
const end=source.indexOf("  globalThis.fetch = async function",start);
assert.ok(start>0 && end>start);
const observed=source.slice(start,end);

async function run({pagehide=false,hidden=false}={}){
  const events=[],windowHandlers=new Map(),documentHandlers=new Map();
  const controller=new AbortController();
  const document={
    visibilityState:"visible",
    addEventListener(name,fn){documentHandlers.set(name,fn);},
    removeEventListener(name,fn){if(documentHandlers.get(name)===fn)documentHandlers.delete(name);},
  };
  const ctx={
    post:e=>events.push(e), CHANNEL:"conversation-fetch-v55",
    contentType:()=> "text/event-stream", Date, document, TextDecoder,
    addEventListener(name,fn){windowHandlers.set(name,fn);},
    removeEventListener(name,fn){if(windowHandlers.get(name)===fn)windowHandlers.delete(name);},
    decoderFactory:()=>new TextDecoder(),
    streamParser:()=>({push:()=>{},peek:()=>({
      assistant_chars:0,assistant_role_seen:true,completion_hint:false
    })}),
  };
  runInNewContext(observed+"\nthis.observe=observeConversation;",ctx);
  const response={
    ok:true,status:200,headers:{get:()=> "text/event-stream"},
    bodyUsed:false,body:{locked:false},
    clone(){return {body:{getReader(){return {
      async read(){
        // Events originate from the actual ChatGPT page, NOT from
        // the extension. They are read-only observed before the native
        // fetch signal is aborted by the fixture's PAGE controller.
        if(hidden){
          document.visibilityState="hidden";
          documentHandlers.get("visibilitychange")?.();
        }
        if(pagehide)windowHandlers.get("pagehide")?.({persisted:false});
        controller.abort();
        const e=new Error("private user text must not be exported");
        e.name="AbortError";
        throw e;
      },
      releaseLock(){}
    }}}};},
  };
  await ctx.observe(response,"same-request-stream-1",controller.signal);
  const failure=events.find(e=>e.phase==="error");
  assert.ok(failure,"the page-owned abort cannot masquerade as clean EOF");
  assert.equal(failure.native_signal_abort_observed,true);
  assert.equal(failure.native_signal_initially_aborted,false);
  assert.ok(failure.native_signal_abort_after_ms>=0);
  assert.equal(failure.native_signal_abort_reason_kind,"AbortError");
  assert.equal(failure.native_signal_abort_visibility,hidden?"hidden":"visible");
  assert.equal(failure.native_pagehide_during_stream,pagehide);
  assert.equal(failure.native_pagehide_before_abort,pagehide);
  assert.equal(failure.native_visibility_hidden_during_stream,hidden);
  assert.equal(failure.clone_reader_failure_kind,"page-fetch-signal-aborted");
  assert.equal(events.filter(e=>e.phase==="done").length,0);
  assert.equal(windowHandlers.size,0,"pagehide listener must be removed");
  assert.equal(documentHandlers.size,0,"visibility listener must be removed");
  const lifecycleOnly=Object.fromEntries(
    Object.entries(failure).filter(([k])=>k.startsWith("native_")));
  assert.ok(!JSON.stringify(lifecycleOnly).includes("private user text"),
    "new lifecycle fields must never contain raw exceptions or page contents");
  return events;
}

const active=await run({pagehide:true,hidden:true});
const offscreen=await run({pagehide:false,hidden:false});
assert.ok(active.some(e=>e.native_pagehide_during_stream===true));
assert.ok(offscreen.some(e=>e.native_pagehide_during_stream===false));
assert.ok(!source.includes("signal.abort()"),
  "the extension observes the PAGE AbortSignal; it never cancels it");
console.log("v313: page lifecycle evidence distinguishes original abort with/without pagehide, no extra terminal");
