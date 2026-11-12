// R-05: policy thresholds and monotonic elapsed-time boundaries are executable.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const policySource = fs.readFileSync(
  new URL("../chrome_extension/short_reply_stability_policy_v1.js", import.meta.url), "utf8",
);
let monotonicNow = 100;
const context = vm.createContext({ performance: { now: () => monotonicNow } });
vm.runInContext(policySource, context);
const policy = context.__CHAT2API_SHORT_REPLY_STABILITY_POLICY_V1__;
assert.ok(policy, "standalone short-reply policy must install its immutable API");

assert.equal(policy.requiredStableMs({ shortTextVerified: true }), 4500);
assert.equal(policy.requiredStableMs({ shortTextVerified: true, rolelessRichVerified: true }), 8000);
assert.equal(policy.requiredStableMs({ shortTextVerified: false }), 8000);
assert.equal(policy.requiredStableMs({}), 8000);
assert.equal(policy.requiredStableMs({ shortTextVerified: "true" }), 8000);

// Exact boundary: 4,499ms is not stable; 4,500ms is.
assert.equal(policy.isStable({ shortTextVerified: true }, 100, 4599), false);
assert.equal(policy.isStable({ shortTextVerified: true }, 100, 4600), true);
// Long/unknown/rich candidates must not inherit the short window.
assert.equal(policy.isStable({ shortTextVerified: true, rolelessRichVerified: true }, 100, 4599), false);
assert.equal(policy.isStable({ shortTextVerified: false }, 100, 8099), false);
assert.equal(policy.isStable({ shortTextVerified: false }, 100, 8100), true);
assert.equal(policy.isStable({}, 100, 8099), false);
assert.equal(policy.isStable({}, 100, 8100), true);
// A wall/clock regression or malformed timestamp is unknown, never stable.
assert.equal(policy.elapsedMs(500, 499), null);
assert.equal(policy.isStable({ shortTextVerified: true }, 500, 499), false);
assert.equal(policy.elapsedMs(Number.NaN, 1000), null);
assert.equal(policy.elapsedMs(null, 10000), null);
assert.equal(policy.isStable({ shortTextVerified: true }, null, 10000), false);
monotonicNow = 4700;
assert.equal(policy.now(), 4700);

// The manifest must load the pure policy before the single request-v6 owner.
const manifest = JSON.parse(fs.readFileSync(
  new URL("../chrome_extension/manifest.json", import.meta.url), "utf8",
));
const requestScripts = manifest.content_scripts.find(item =>
  item.js.includes("content_request_v6.js")
).js;
assert.ok(
  requestScripts.indexOf("short_reply_stability_policy_v1.js")
    < requestScripts.indexOf("content_request_v6.js"),
  "policy must be loaded before request-v6",
);

const source = fs.readFileSync(new URL("../chrome_extension/content_request_v6.js", import.meta.url), "utf8");
assert.ok(source.includes("globalThis.__CHAT2API_SHORT_REPLY_STABILITY_POLICY_V1__"));
assert.ok(!source.includes("+8s-stable"), "no fixed 8s claim for 4.5s short replies");
const expression = "${abortedReplyStableThreshold(rendered)}ms-stable";
assert.equal(source.split(expression).length - 1, 4);
assert.match(source, /response_terminal_stable_ms: abortedReplyStableElapsedMs\(abortedRenderedCandidateSince\)/);
assert.match(source, /response_terminal_required_stable_ms: abortedReplyStableThreshold\(rendered\)/);
assert.match(source, /abortedReplyStableSince\(abortedRenderedCandidateSince, rendered\)/);
assert.ok(!source.includes("Date.now() - abortedRenderedCandidateSince"));
assert.ok(!source.includes("abortedRenderedCandidateSince = 0;"), "reset uses a null sentinel, never an ancient time origin");

// Short timing remains confined to request-v6's aborted text-only recovery;
// media/file/image creation, active generation, tool payloads and cancellation
// cannot be completed by this short-reply policy.
assert.match(source, /if \(!captureMedia && !fileCreationRequested && !imageCreationRequested/);
assert.match(source, /!generating && !upstreamStillWorking\(\)/);
assert.match(source, /while \(!active.cancelled/);
assert.match(source, /!internalToolPayload\(finalText\)/);

console.log("R-05 standalone monotonic stability policy and boundaries: PASS");
