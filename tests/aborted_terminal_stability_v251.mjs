import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function abortedReplyStableThreshold(rendered) {");
const end=source.indexOf("\n  async function monitor(active) {",start);
assert.ok(start>=0&&end>start);
const context={};
vm.runInNewContext(source.slice(start,end)+
  "\nthis.threshold=abortedReplyStableThreshold;",context);
const threshold=context.threshold;
assert.equal(threshold({shortTextVerified:true}),4500);
assert.equal(threshold({shortTextVerified:false,rolelessRichVerified:true}),8000);
assert.equal(threshold({}),8000);
assert.equal(threshold(null),8000);

const monitor=source.slice(end);
assert.ok(monitor.includes("!captureMedia && !fileCreationRequested && !imageCreationRequested"),
  "media-producing requests never use aborted text-only fast completion");
assert.ok(monitor.includes("!generating && !upstreamStillWorking()"),
  "active generation must never complete from a static DOM");
assert.ok(monitor.includes("rendered.finishAction?.isConnected"),
  "same-request finish toolbar must exist");
assert.ok(monitor.includes("rolelessRichCaptureComplete(rendered.node, finalText)"),
  "rich reply must pass first/last block integrity check");
assert.ok(monitor.includes("response_recovery_rich_tail_complete:"),
  "rich serialization failures need read-only evidence");
assert.ok(monitor.includes("response_recovery_serializer_chars:"),
  "capture length must be measurable without logging user content");
console.log("Short-only stable gate, media safety and rich recovery evidence passed");
