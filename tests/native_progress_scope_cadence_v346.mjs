import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const worker = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const start = worker.indexOf("  function captureNativeProgressIfDue(");
const end = worker.indexOf("  async function monitor(active)", start);
assert.ok(start >= 0 && end > start);
const calls = [];
const capture = runInNewContext("(" + worker.slice(start, end).trim() + ")", {
  native: {capture: async (active, final) => calls.push([active.requestId, final])},
});
const candidate = {isConnected: true};
const scope = {isConnected: true, contains: node => node === candidate};
const active = {
  requestId: "req_native_cadence", submitStartedAt: 100000, networkStreamSeen: true,
  nativeVerifiedReplyScope: scope,
};
const current = {isNew: true, latest: candidate};
for (const delta of [0, 14999, 15000, 15001, 29999, 30000]) {
  capture(active, current, active.submitStartedAt + delta);
}
assert.deepEqual(calls, [[active.requestId, false], [active.requestId, false]]);
for (const patch of [
  {networkStreamSeen: false}, {completed: true}, {cancelled: true}, {requestId: ""},
  {submitStartedAt: 0}, {nativeTransferInFlight: {}}, {nativeFinalCaptureStarted: true},
  {nativeVerifiedReplyScope: {isConnected: false}},
  {nativeVerifiedReplyScope: {isConnected: true, contains: () => false}},
]) {
  capture({...active, ...patch}, current, 200000);
}
capture(active, {...current, isNew: false}, 200000);
capture(active, {...current, latest: {isConnected: false}}, 200000);
assert.equal(calls.length, 2);
console.log("Native progress only captures a fresh, verified reply at the permitted cadence");
