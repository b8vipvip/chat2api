import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source = readFileSync("app/chat_client_v1.js", "utf8");
const statusMatch = source.match(/function normalizeToolStatus\\(value\\) \\{[\\s\\S]*?\\n  \\}/);
const eventsMatch = source.match(/function normalizeToolEvents\\(value, role\\) \\{[\\s\\S]*?\\n  \\}/);
assert.ok(statusMatch, "production normalizeToolStatus function must remain extractable");
assert.ok(eventsMatch, "production normalizeToolEvents function must remain extractable");

const helpers = {
  safeToolName: value => String(value || "tool").slice(0, 80),
  normalizeToolReferences: () => [],
};
const api = runInNewContext(
  statusMatch[0] + "\\n" + eventsMatch[0] + "\\n({normalizeToolStatus,normalizeToolEvents})",
  helpers,
);

const cases = [
  ["running", "running"],
  ["in_progress", "running"],
  ["pending", "running"],
  ["streaming", "running"],
  ["queued", "running"],
  ["waiting", "running"],
  ["retrying", "running"],
  ["requires_action", "running"],
  ["failed", "failed"],
  ["error", "failed"],
  ["errored", "failed"],
  ["incomplete", "failed"],
  ["timeout", "failed"],
  ["timed_out", "failed"],
  ["rejected", "failed"],
  ["cancelled", "cancelled"],
  ["canceled", "cancelled"],
  ["aborted", "cancelled"],
  ["completed", "completed"],
  ["complete", "completed"],
  ["succeeded", "completed"],
  ["success", "completed"],
  ["finished", "completed"],
  ["done", "completed"],
  ["future_vendor_state", "unknown"],
  ["", "unknown"],
  [null, "unknown"],
];
for (const [input, expected] of cases) {
  assert.equal(api.normalizeToolStatus(input), expected, String(input));
}

const events = cases.slice(0, 4).concat(cases.slice(8, 10), cases.slice(15, 18), cases.slice(24));
const rows = api.normalizeToolEvents(events.map(([status], index) => ({
  toolName: "browser.search",
  callId: "call-" + index,
  status,
})), "assistant");
assert.deepEqual(
  Array.from(rows, row => row.status),
  events.map(([, expected]) => expected),
);
assert.deepEqual(api.normalizeToolEvents([{callId:"call-1",status:"completed"}], "user"), []);
console.log("Web Chat tool status normalization preserves unknown/nonterminal states");
