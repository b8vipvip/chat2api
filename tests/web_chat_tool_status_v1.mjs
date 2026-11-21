import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source = readFileSync("app/chat_client_v1.js", "utf8");
const statusMatch = source.match(/function normalizeToolStatus\(value\) \{[\s\S]*?\n  \}/);
const eventsMatch = source.match(/function normalizeToolEvents\(value, role\) \{[\s\S]*?\n  \}/);
const applyMatch = source.match(/function applyToolEvent\(assistant, event\) \{[\s\S]*?\n  \}/);
const refsMatch = source.match(/function referencesForMessage\(message\) \{[\s\S]*?\n  \}/);
const rowMatch = source.match(/function normalizeConversationRow\(row\) \{[\s\S]*?\n  \}/);
assert.ok(statusMatch, "production normalizeToolStatus function must remain extractable");
assert.ok(eventsMatch, "production normalizeToolEvents function must remain extractable");
assert.ok(applyMatch, "production applyToolEvent function must remain extractable");
assert.ok(refsMatch, "production referencesForMessage function must remain extractable");
assert.ok(rowMatch, "production normalizeConversationRow function must remain extractable");

let historyWrites = 0;
const helpers = {
  safeToolName: value => String(value || "tool").slice(0, 80),
  normalizeToolReferences: value => {
    if (!Array.isArray(value)) return [];
    const seen = new Set(), rows = [];
    for (const item of value) {
      if (!item || typeof item.url !== "string") continue;
      const identity = item.id ? "id:" + item.id : "url:" + item.url;
      if (seen.has(identity)) continue;
      seen.add(identity);
      rows.push(item);
      if (rows.length >= 8) break;
    }
    return rows;
  },
  normalizeMessageContent: value => String(value || ""),
  messageAttachmentIds: () => [],
  MAX_MESSAGES: 50,
  state: {selectedModel:"gpt-test"},
  id: () => "generated-id",
  saveHistory: () => { historyWrites += 1; },
};
const api = runInNewContext(
  statusMatch[0] + "\n" + eventsMatch[0] + "\n" + applyMatch[0] +
    "\n" + refsMatch[0] + "\n" + rowMatch[0] +
    "\n({normalizeToolStatus,normalizeToolEvents,applyToolEvent,referencesForMessage,normalizeConversationRow})",
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
assert.equal(api.applyToolEvent(completedAssistant,{
  phase:"call",tool_name:"browser.search",call_id:"call-terminal-1",status:"running",
}),false);
assert.equal(completedAssistant.toolEvents[0].status,"completed");
assert.equal(api.applyToolEvent(completedAssistant,{
  phase:"result",tool_name:"browser.search",call_id:"call-terminal-1",
  result_id:"result-terminal-2",status:"failed",output:"late retry",
}),false);
assert.equal(completedAssistant.toolEvents[0].status,"completed");
assert.equal(completedAssistant.toolEvents[0].output,"done");
assert.equal(completedAssistant.toolEvents[0].resultId,"result-terminal-1");

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
assert.equal(historyWrites,5);
console.log("Web Chat tool status normalization preserves unknown/nonterminal states");


// Citations must outlive the six most recent tool-detail rows.
const citationAssistant = {toolEvents:[]};
for (let index=0; index<8; index+=1) {
  api.applyToolEvent(citationAssistant,{
    phase:"result",tool_name:"browser.search",
    call_id:"citation-call-"+index,result_id:"citation-result-"+index,
    status:"completed",output:"result "+index,
    references:[{
      id:"turn0search"+index,
      url:"https://docs"+index+".example.org/reference",
      title:"Source "+index,
    }],
  });
}
assert.equal(citationAssistant.toolEvents.length,6);
assert.equal(citationAssistant.references.length,8);
assert.equal(api.referencesForMessage(citationAssistant).length,8);
const restored = api.normalizeConversationRow({
  id:"conversation-citations",
  messages:[{
    id:"assistant-citations",role:"assistant",content:"Answer",
    toolEvents:citationAssistant.toolEvents,references:citationAssistant.references,
  }],
});
assert.equal(restored.messages[0].toolEvents.length,6);
assert.equal(restored.messages[0].references.length,8);
assert.equal(api.referencesForMessage(restored.messages[0]).length,8);
