import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync("chrome_extension/content_probe_supervisor_v1.js", "utf8");
const sent = [];
const fakeDocument = {
  readyState: "complete",
  querySelector(selector) { return null; },
  querySelectorAll(selector) {
    if (selector.includes("data-testid*='model-selector'")) return [{}];
    if (selector.includes("data-testid*='reasoning'")) return [{}];
    if (selector.includes("data-testid*='tool'")) return [{}];
    if (selector.includes("input[type='file']")) return [{}];
    if (selector.includes("data-testid*='edit'")) return [];
    if (selector.includes("#prompt-textarea")) return [{}];
    if (selector.includes("data-testid='send-button'")) return [{}];
    return [];
  },
};
const context = {
  document: fakeDocument,
  performance: {now: () => 14},
  setTimeout,
  chrome: {runtime: {sendMessage: async message => { sent.push(message.event); return {ok:true}; }}},
};
runInNewContext(source, context);
const probes = context.__CHAT2API_PROBE_SUPERVISOR_V1__;
assert.ok(probes, "P1 must reuse canonical P0 supervisor");
const tools = ["media_delivery_v1", "conversation_dom_v1", "network_evidence_v1",
  "model_tool_state_v1", "selector_compat_v1"];
const active = {
  requestId: "req_testp1abcdef01",
  networkStreamSeen: true,
  networkStreamChunks: 16,
  networkStreamBytes: 2440,
  networkTerminalAt: 0,
  networkTerminalChunks: 0,
  networkObservedAt: Date.now() - 1200,
  networkStreamEndedWithoutTerminalAt: 0,
  options: {probe_policy: {
    schema_version: 1,
    tools: Object.fromEntries(tools.map(id => [id, {enabled: true, max_snapshots: 8}])),
  }},
};
probes.begin(active);
await Promise.resolve();
await Promise.resolve();
assert.equal(sent.length, 5, "all five explicitly enabled probes run");
assert.deepEqual(sent.map(x => x.tool_id).sort(), [...tools].sort());
const facts = sent[0].facts;
assert.equal(facts.network_stream_seen, true);
assert.equal(facts.network_stream_chunks, 16);
assert.equal(facts.network_stream_bytes, 2440);
assert.equal(facts.network_stream_terminal_seen, false);
assert.equal(facts.model_picker_count, 1);
assert.equal(facts.reasoning_selector_count, 1);
assert.equal(facts.tool_selector_count, 1);
assert.equal(facts.file_upload_selector_count, 1);
assert.equal(facts.selector_send_button_count, 1);
assert.ok(facts.network_stream_last_age_ms >= 1000);
await probes.capture(active, "before_terminal", {completed: false});
assert.equal(sent.length, 10, "final evidence sent to all enabled tools");
assert.ok(sent.slice(5).every(x => x.classification === "request_error_observed"));
assert.ok(sent.every(x => x.type === "probe.event"));
assert.ok(!JSON.stringify(sent).includes("raw_event_data"));
assert.ok(!JSON.stringify(sent).includes("Authorization"));
assert.ok(!JSON.stringify(sent).includes("sk-"));
assert.ok(sent.length <= 32, "server event cap");
assert.ok(!source.includes('type: "chat.completed"'));
assert.ok(!source.includes('type: "chat.error"'));
console.log("P1 network/model/selector probes: bounded metadata-only evidence and single terminal authority");
