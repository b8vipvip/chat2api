import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source = readFileSync("app/chat_client_v1.js", "utf8");
const statusMatch = source.match(/function normalizeToolStatus\(value\) \{[\s\S]*?\n  \}/);
const eventsMatch = source.match(/function normalizeToolEvents\(value, role\) \{[\s\S]*?\n  \}/);
const applyMatch = source.match(/function applyToolEvent\(assistant, event\) \{[\s\S]*?\n  \}/);
assert.ok(statusMatch, "production normalizeToolStatus function must remain extractable");
assert.ok(eventsMatch, "production normalizeToolEvents function must remain extractable");
assert.ok(applyMatch, "production applyToolEvent function must remain extractable");

let historyWrites = 0;
const helpers = {
  safeToolName: value => String(value || "tool").slice(0, 80),
  normalizeToolReferences: () => [],
  saveHistory: () => { historyWrites += 1; },
};
const api = runInNewContext(
  statusMatch[0] + "\n" + eventsMatch[0] + "\n" + applyMatch[0] +
    "\n({normalizeToolStatus,normalizeToolEvents,applyToolEvent})",
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

const events = [cases[0],cases[8],cases[15],cases[18],cases[24],cases[26]];
const rows = api.normalizeToolEvents(events.map(([status], index) => ({
  toolName: "browser.search",
  callId: "call-" + index,
  status,
})), "assistant");
assert.deepEqual(
  Array.from(rows, row => row.status),
  events.map(([, expected]) => expected),
);
const boundedRows = api.normalizeToolEvents(Array.from({length:8},(_,index)=>({
  toolName:"browser.search",callId:"bounded-"+index,status:"running",
})), "assistant");
assert.equal(boundedRows.length,6);
assert.equal(api.normalizeToolEvents([{callId:"call-1",status:"completed"}], "user").length, 0);

// Replayed/out-of-order events must not reopen a tool after terminal result.
const completedAssistant = {toolEvents:[]};
assert.equal(api.applyToolEvent(completedAssistant,{
  phase:"call",tool_name:"browser.search",call_id:"call-terminal-1",status:"running",
}),true);
assert.equal(completedAssistant.toolEvents[0].status,"running");
assert.equal(api.applyToolEvent(completedAssistant,{
  phase:"result",tool_name:"browser.search",call_id:"call-terminal-1",
  result_id:"result-terminal-1",status:"completed",output:"done",
}),true);
assert.equal(completedAssistant.toolEvents[0].status,"completed");
api.applyToolEvent(completedAssistant,{
  phase:"call",tool_name:"browser.search",call_id:"call-terminal-1",status:"running",
});
assert.equal(completedAssistant.toolEvents[0].status,"completed");
api.applyToolEvent(completedAssistant,{
  phase:"result",tool_name:"browser.search",call_id:"call-terminal-1",
  result_id:"result-terminal-2",status:"failed",output:"late retry",
});
assert.equal(completedAssistant.toolEvents[0].status,"completed");

const failedAssistant = {toolEvents:[]};
api.applyToolEvent(failedAssistant,{
  phase:"result",tool_name:"browser.search",call_id:"call-terminal-2",
  result_id:"result-terminal-3",status:"failed",
});
api.applyToolEvent(failedAssistant,{
  phase:"call",tool_name:"browser.search",call_id:"call-terminal-2",status:"running",
});
assert.equal(failedAssistant.toolEvents[0].status,"failed");

const runningAssistant = {toolEvents:[]};
api.applyToolEvent(runningAssistant,{
  phase:"call",tool_name:"browser.search",call_id:"call-terminal-3",status:"running",
});
api.applyToolEvent(runningAssistant,{
  phase:"result",tool_name:"browser.search",call_id:"call-terminal-3",status:"future_vendor_state",
});
assert.equal(runningAssistant.toolEvents[0].status,"running");
assert.ok(historyWrites >= 8);
console.log("Web Chat tool status normalization preserves unknown/nonterminal states");
