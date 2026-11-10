// A bounded MAIN-world model observer must NOT pin an unread ReadableStream.tee
// clone, cancel ChatGPT's native fetch, or fabricate a "done" event at 16MB.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source = readFileSync("chrome_extension/model_evidence_main_v144.js", "utf8");
const contentSource = readFileSync("chrome_extension/content_model_evidence_v144.js", "utf8");
const start = source.indexOf("  function observeResponse(response, streamId, requestInfo)");
const end = source.indexOf("\n  globalThis.addEventListener(", start);
assert.ok(start > 0 && end > start, "the actual bounded model clone reader must be tested");
assert.match(source, /const MAX_BUFFER = 16 \* 1024 \* 1024/);
assert.match(contentSource, /detail\.phase === "limit"/);

const blank = () => ({
  served_model: null, model_field: null, model_conflict: false,
  default_model: null, default_model_field: null, default_model_conflict: false,
  reasoning: null, reasoning_field: null, reasoning_conflict: false,
});
const makeData = slug => "data: " + JSON.stringify({
  served_model_slug: slug, metadata: {resolved_model_slug: slug}
}) + "\n\n";

async function run(mode) {
  const events=[],actions=[];
  const chunks = mode === "limit"
    ? [makeData("gpt-5.6-sol"),"data: "+JSON.stringify({padding:"X".repeat(400)})+"\n\n",makeData("gpt-6"),makeData("gpt-6")]
    : [makeData("gpt-5.6-sol"),makeData("gpt-5.6-sol")];
  let cursor=0, cloneReads=0, nativeCancels=0, cloneCancels=0, releases=0;
  const reader={
    async read() {
      cloneReads++;
      if(mode==="error" && cursor>=1) throw new Error("private upstream message");
      if(cursor>=chunks.length) return {done:true};
      return {done:false,value:new TextEncoder().encode(chunks[cursor++])};
    },
    cancel() {
      actions.push("cancel-clone");
      cloneCancels++;
      // A tee branch's cancellation may be PENDING until the original
      // ChatGPT stream ends. Waiting for it is a deadlock.
      return new Promise(()=>{});
    },
    releaseLock(){ actions.push("release-clone"); releases++; },
  };
  const response={
    ok:true,status:200,headers:{get:()=>null},
    nativeCancel(){ nativeCancels++; },
    clone(){ return {
      ok:true,status:200,headers:{get:()=>null},
      body:{getReader:()=>reader},
    };},
  };
  const ctx={
    MAX_BUFFER: 240, TextDecoder, Promise, state:{evidence:0},
    normalizeModel:x=>x,
    parseEvent:raw=>JSON.parse(raw.slice(raw.indexOf(":")+1).trim()),
    inspectObjects:rows=>{
      const result=blank();
      if(rows.length&&typeof rows[0].served_model_slug==="string"){
        result.served_model=rows[0].served_model_slug;
        result.model_field="served_model_slug";
      }
      return result;
    },
    post:event=>events.push(event),
  };
  runInNewContext(source.slice(start,end)+"\nthis.observeResponse=observeResponse;",ctx);
  ctx.observeResponse(response,"same-request",{
    requestModel:"gpt-5.6-sol",requestWireModel:"gpt-5-6-thinking",
    rewritten:false,authorityViolation:false,
  });
  for(let i=0;i<10&&!events.some(x=>["done","limit","error"].includes(x.phase));i++)
    await new Promise(resolve=>setImmediate(resolve));
  return {events,actions,cloneReads,cloneCancels,releases,nativeCancels};
}

const capped=await run("limit");
assert.equal(capped.events.at(-1).phase,"limit");
assert.equal(capped.events.at(-1).observation_limit_hit,true);
assert.equal(capped.cloneCancels,1,"cancel only the unused evidence tee branch");
assert.equal(capped.releases,1,"release clone reader even if bounded");
assert.equal(capped.nativeCancels,0,"never cancel ChatGPT original fetch");
assert.deepEqual(capped.actions,["cancel-clone","release-clone"]);
assert.ok(capped.events.some(x=>x.phase==="evidence" && x.served_model==="gpt-5.6-sol"));
assert.ok(!capped.events.some(x=>x.phase==="evidence" && x.terminal===true),
  "16MB observation cap is not a model-verified stream EOF");
assert.ok(!capped.events.some(x=>x.phase==="done"));
assert.ok(capped.cloneReads<4,"stop reading once the evidence bound is reached");

const error=await run("error");
assert.equal(error.events.at(-1).phase,"error");
assert.equal(error.cloneCancels,1);
assert.equal(error.releases,1);
assert.equal(error.nativeCancels,0);
assert.ok(!error.events.some(x=>x.phase==="done"));
assert.ok(!error.events.some(x=>x.phase==="evidence"&&x.terminal===true));

const complete=await run("done");
assert.equal(complete.events.at(-1).phase,"done");
assert.equal(complete.cloneCancels,0,"clean clone EOF must not cancel original");
assert.equal(complete.releases,1);
assert.equal(complete.nativeCancels,0);
assert.ok(complete.events.some(x=>x.phase==="evidence"&&x.terminal===true));
console.log("v333 bounded model SSE clone cleanup + truthful EOF: limit, abort, clean EOF passed");
