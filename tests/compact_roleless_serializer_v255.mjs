import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function verifiedPlainRolelessCaptureFallback(active, rendered, captured) {");
const end=source.indexOf("  // #535: bounded, privacy-safe diagnostics",start);
assert.ok(start>0&&end>start);
function run({raw="回复".repeat(109),network="回复".repeat(107),captured="回复".repeat(113),
 coverage={scan_overflow:false,independent_count:3,independent_chars:37,max_independent_chars:19},
 rich=false,reason="fresh-roleless-compact-div-v248"}={}){
 const node={isConnected:true,innerText:raw,querySelector:()=>rich?{tagName:"TABLE"}:null};
 const active={baselineRenderedText:new Map(),networkObservedText:network};
 const rendered={node,replyScope:{isConnected:true},text:raw,
 rolelessRichVerified:true,shortTextVerified:true,rolelessHeaderVerified:true,reason};
 const context={normalize:v=>String(v||"").trim(),transientText:()=>false,
  internalToolPayload:()=>false,renderedResponseBoundaryEvidence:()=>coverage,
  shortRolelessSourceOverlap:(answer,observed)=>answer.includes(observed.slice(0,8)),
  rolelessRichCaptureComplete:(candidate,content)=>candidate===node && content===raw};
 vm.runInNewContext(source.slice(start,end)+"\nthis.verify=verifiedPlainRolelessCaptureFallback;",context);
 return context.verify(active,rendered,captured);
}
test("7da9: same-request compact 218 chars survives 226-char markdown expansion",()=>{
 const result=run();assert.equal(result,"回复".repeat(109));
});
test("26-char roleless status text cannot be promoted",()=>{
 assert.equal(run({raw:"没有可交付的有效回复。".repeat(2),network:"没有可交付",captured:"incorrect"}),null);
});
test("network mismatch, rich media, and overflow cannot become completed",()=>{
 assert.equal(run({network:"unrelated streamed payload"}),null);
 assert.equal(run({rich:true}),null);
 assert.equal(run({coverage:{scan_overflow:true,independent_count:3,independent_chars:37,max_independent_chars:19}}),null);
 assert.equal(run({coverage:{scan_overflow:false,independent_count:255,independent_chars:8500,max_independent_chars:340}}),null);
});
test("only current verified compact roleless evidence can use short fallback",()=>{
 assert.equal(run({reason:"unverified-roleless"}),null);
 assert.match(source,/response_recovery_compact_plain_fallback:/);
 assert.match(source,/rendered.finishAction\?\.isConnected/);
});
