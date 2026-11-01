import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function mediaTerminalPayloadIntegrity(text) {");
const end=source.indexOf("  function conversationTurns() {",start);
assert.ok(start>0 && end>start);
const ctx={};
runInNewContext(source.slice(start,end)+
  ";globalThis.check=mediaTerminalPayloadIntegrity;",ctx);
const check=ctx.check;
assert.equal(check("No generated media, just text").ok,true);
assert.equal(check("![image](data:image/png;base64,YWJjZA==)").ok,true);
assert.equal(check("![image](data:image/png;base64,YWJjZA==)"+
  "\n![📎 plan.xlsx](chat2api-staged:"+"a".repeat(32)+")").ok,true);
assert.equal(check("![📎 plan.xlsx](data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,YQ==)").ok,true);
assert.deepEqual(JSON.parse(JSON.stringify(check(
  "![image](data:image/jpeg;base64,YWI=)")))
 ,{ok:true,reason:"",imageMarkers:1,validImageMarkers:1,
    fileMarkers:0,validFileMarkers:0});
for(const malformed of [
  "![Aquascape](data:image/png;base64,AA",
  "![photo](data:image/avif;base64,YWJjZA==)",
  "data:image/png;base64,QUJD",
  "![📎 plan.xlsx](chat2api-staged:short)",
  "![📎 plan.pptx](data:application/octet-stream;base64,",
  "![good](data:image/png;base64,YQ==)\n![bad](data:image/png;base64,",
  "![📎 done.csv](chat2api-staged:"+"b".repeat(32)+")\n![📎 broken.csv](data:application/octet-stream;base64,",
]) {
  const r=check(malformed);
  assert.equal(r.ok,false,malformed.slice(0,70));
  assert.match(r.reason,/incomplete-(image|file)-marker/);
  // Reason and counters only; never return the media's embedded bytes.
  assert.ok(!JSON.stringify(r).includes("base64"));
}
assert.ok(source.includes("mediaTerminalPayloadIntegrity(captured)"));
assert.ok(source.includes('imageCandidate="";'));
assert.ok(source.indexOf("mediaTerminalPayloadIntegrity(captured)")
  <source.indexOf('responseStarted = await ensureResponseStarted(active, responseStarted, "verified-media-dom-v176")'));
console.log("Pre-terminal inline media integrity: valid accepted, truncated rejected");
