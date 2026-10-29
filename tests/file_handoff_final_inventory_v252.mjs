import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
const src=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=src.indexOf("  function fileHandoffTypeInventory(required, captured) {");
const end=src.indexOf("  async function monitor(active) {",start);
assert.ok(start>=0&&end>start);
const context={};
vm.runInNewContext(src.slice(start,end)+"\nthis.inventory=fileHandoffTypeInventory;",context);
const inventory=context.inventory;
const staged="chat2api-staged:"+"a".repeat(32);
test("four validated file types count even if native XLSX click previously failed",()=>{
 const captured=[
  "![📎 Report.xlsx]("+staged+")",
  "![📎 Slides.pptx](data:application/zip;base64,UEsDBA==)",
  "![📎 Plan.json](data:application/json;base64,e30=)",
  "![📎 Archive.zip]("+staged+")",
 ].join("\n");
 const got=inventory([".xlsx",".pptx",".json",".zip"],captured);
 assert.deepEqual(Array.from(got.verified),[".xlsx",".pptx",".json",".zip"]);
 assert.deepEqual(Array.from(got.missing),[]);
});
test("HTML preview, markdown mention and unverified pending links never count as binary",()=>{
 const got=inventory([".xlsx",".pptx"],[
  "[📎 Report.xlsx](/api/generated-files/pending/pgen_123/token)",
  "download Book.pptx",
  "![📎 Report.xlsx](https://example.com/preview)",
 ].join("\n"));
 assert.deepEqual(Array.from(got.verified),[]);
 assert.deepEqual(Array.from(got.missing),[".xlsx",".pptx"]);
});
test("terminal and retry diagnostics explicitly separate unresolved native failures",()=>{
 assert.match(src,/media_file_handoff_verified_types: fileHandoffInventory\.verified/);
 assert.match(src,/media_native_file_unresolved_failures: nativeUnresolvedFailedTypes/);
 assert.match(src,/media_delivery_phase: "terminal-verified"/);
 assert.match(src,/media_delivery_phase: "retry-checkpoint"/);
});
