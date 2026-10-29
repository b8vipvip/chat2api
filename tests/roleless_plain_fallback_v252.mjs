import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
const src=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=src.indexOf("  function verifiedPlainRolelessCaptureFallback(active, rendered, captured) {");
const end=src.indexOf("  // #535: bounded, privacy-safe diagnostics",start);
assert.ok(start>0&&end>start);
function candidate(overrides={}) {
 const body="交付说明：原始页面已经完整生成。".repeat(19);
 const node={
  isConnected:true,innerText:body,
  querySelector:()=>null,
 };
 let uncovered={scan_overflow:false,independent_count:3,
   independent_chars:37,max_independent_chars:19};
 const ctx={
  normalize:x=>String(x||"").replace(/\s+/g," ").trim(),
  transientText:()=>false,internalToolPayload:()=>false,
  renderedResponseBoundaryEvidence:()=>overrides.coverage||uncovered,
  rolelessRichCaptureComplete:(n,text)=>Boolean(n&&text&&text.includes(body.slice(0,20))
    && text.includes(body.slice(-20))),
 };
 vm.runInNewContext(src.slice(start,end)+
  "\nthis.recover=verifiedPlainRolelessCaptureFallback;",ctx);
 const rendered={node,replyScope:{isConnected:true},text:body,
  rolelessRichVerified:true,shortTextVerified:false};
 return {recover:()=>ctx.recover({baselineRenderedText:new Map()},rendered,"incomplete"),
  node,rendered,ctx,body};
}
test("290+ character plain reply recovers if serializer loses trailing text but nearby blocks are tiny",()=>{
 const fixture=candidate();
 assert.equal(fixture.recover(),fixture.body);
});
test("be17 with 255 unbound blocks and 8.8K bytes cannot be marked complete",()=>{
 const fixture=candidate({coverage:{scan_overflow:false,independent_count:255,
  independent_chars:8800,max_independent_chars:743}});
 assert.equal(fixture.recover(),null);
});
test("any rich table/image/code/video/link prevents accidental plain-only recovery",()=>{
 const f=candidate();f.node.querySelector=()=>({tagName:"IMG"});
 assert.equal(f.recover(),null);
});
test("a complete rich serialized reply is not replaced by plain fallback",()=>{
 const f=candidate();
 assert.equal(f.ctx.recover({baselineRenderedText:new Map()},f.rendered,f.body),null);
});
test("no fresh connected reply or same-request scope, no recovery",()=>{
 const f=candidate();f.rendered.replyScope.isConnected=false;
 assert.equal(f.recover(),null);
});
test("only existing request-v6 branch emits chat.completed",()=>{
 assert.match(src,/plainFallback=verifiedPlainRolelessCaptureFallback/);
 assert.match(src,/response_terminal_plain_roleless_fallback/);
 assert.match(src,/!generating && !upstreamStillWorking\(\)/);
 assert.match(src,/rendered.finishAction\?\.isConnected/);
});
