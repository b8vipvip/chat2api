// R-05: the provenance label must match the *actual* threshold policy.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/content_request_v6.js", import.meta.url), "utf8");
const match = source.match(/function abortedReplyStableThreshold\(rendered\) \{[\s\S]*?\n  \}/);
assert.ok(match, "request-v6 must retain the single short/long stability policy");
const ctx = vm.createContext({});
vm.runInContext(match[0], ctx);
assert.equal(vm.runInContext("abortedReplyStableThreshold({shortTextVerified:true})", ctx), 4500);
assert.equal(vm.runInContext("abortedReplyStableThreshold({shortTextVerified:false})", ctx), 8000);
assert.equal(vm.runInContext("abortedReplyStableThreshold({})", ctx), 8000);

// A 4.5-second compact/short candidate was formerly exported as
// "...+8s-stable", misleading the operator reviewing request ZIPs.
// Every provenance path must report the same required threshold used by the
// Worker completion guard, while retaining actual elapsed ms independently.
assert.ok(!source.includes("+8s-stable"), "no fixed 8s claim for 4.5s short replies");
const expression = "${abortedReplyStableThreshold(rendered)}ms-stable";
assert.equal(source.split(expression).length - 1, 4);
assert.match(source, /response_terminal_stable_ms: Date\.now\(\) - abortedRenderedCandidateSince/);
assert.match(source, /response_terminal_required_stable_ms: abortedReplyStableThreshold\(rendered\)/);
assert.match(source, /Date\.now\(\) - abortedRenderedCandidateSince >= abortedReplyStableThreshold\(rendered\)/);

console.log("R-05 short-reply threshold and evidence labels: PASS");
