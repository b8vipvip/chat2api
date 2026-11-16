import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");
const policySource=fs.readFileSync("chrome_extension/short_reply_stability_policy_v1.js","utf8");
const helperStart=source.indexOf("  const shortReplyStabilityPolicy =");
const helperEnd=source.indexOf("\n  // #560:",helperStart);
const monitorStart=source.indexOf("\n  async function monitor(active) {",helperEnd);
assert.ok(helperStart>=0&&helperEnd>helperStart&&monitorStart>helperEnd);
let monotonicNow=100;
const context={performance:{now:()=>monotonicNow}};
vm.runInNewContext(policySource,context);
vm.runInNewContext(source.slice(helperStart,helperEnd)+
  "\nthis.threshold=abortedReplyStableThreshold;"+
  "\nthis.stableSince=abortedReplyStableSince;",context);
const threshold=context.threshold;
assert.equal(threshold({shortTextVerified:true}),4500);
assert.equal(threshold({shortTextVerified:true,rolelessRichVerified:true}),8000);
assert.equal(threshold({shortTextVerified:false,rolelessRichVerified:true}),8000);
assert.equal(threshold({}),8000);
assert.equal(threshold(null),8000);
assert.equal(context.stableSince(100,{shortTextVerified:true}),false);
monotonicNow=4600;
assert.equal(context.stableSince(100,{shortTextVerified:true}),true);
monotonicNow=8099;
assert.equal(context.stableSince(100,{shortTextVerified:false}),false);
monotonicNow=8100;
assert.equal(context.stableSince(100,{shortTextVerified:false}),true);

const monitor=source.slice(monitorStart);
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
console.log("Short-only stable gate, monotonic policy and rich recovery evidence passed");
