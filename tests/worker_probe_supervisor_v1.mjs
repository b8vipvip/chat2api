import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const script = readFileSync("chrome_extension/content_probe_supervisor_v1.js", "utf8");
const sent = [];
const img = {
  currentSrc: "https://media.oaiusercontent.com/private/signed?id=secret",
  naturalWidth: 400, naturalHeight: 400, complete: true,
  getAttribute(name) { return name === "src" ? this.currentSrc : ""; },
  getBoundingClientRect() { return {width: 250, height: 250}; },
};
const button = {getAttribute: name => name === "aria-label" ? "Download" : ""};
const main = {
  id: "thread", tagName: "MAIN",
  querySelectorAll(selector) {
    if (selector === "img") return [img];
    if (selector === "button") return [button];
    if (selector === "picture") return [];
    if (selector === "canvas") return [];
    if (selector.includes("background")) return [];
    return [];
  },
};
const fakeDocument = {
  readyState: "complete",
  querySelector(selector) { if (selector === "main") return main; return null; },
  querySelectorAll(selector) {
    if (selector.includes("main,[role='main']")) return [main];
    if (selector.includes("main img")) return [img];
    return [];
  },
};
const context = {
  document: fakeDocument,
  performance: {now: () => 5}, setTimeout,
  chrome: {runtime: {sendMessage: async message => { sent.push(message); return true; }}},
  getComputedStyle: () => ({display: "block", visibility: "visible"}),
};
runInNewContext(script, context);
const probes = context.__CHAT2API_PROBE_SUPERVISOR_V1__;
assert.ok(probes);
const active = {
  requestId: "req_probe123456",
  options: {probe_policy: {schema_version: 1, tools: {
    media_delivery_v1: {enabled: true, max_snapshots: 8},
    conversation_dom_v1: {enabled: true},
  }}},
};
probes.begin(active);
await Promise.resolve();
await Promise.resolve();
assert.equal(sent.length, 2);
const facts = sent[0].event.facts;
assert.equal(facts.image_natural_width_max, 400);
assert.equal(facts.visible_large_images, 1);
assert.equal(facts.main_found, true);
assert.ok(!JSON.stringify(sent).includes("signed"));
assert.ok(!JSON.stringify(sent).includes("secret"));
assert.ok(!JSON.stringify(sent).includes("media.oaiusercontent.com"));
const disabled = {requestId: "req_disabled12345", options: {probe_policy: {schema_version: 1, tools: {
  media_delivery_v1: {enabled: false},
  conversation_dom_v1: {enabled: false},
}}}};
probes.begin(disabled);
await Promise.resolve();
assert.equal(sent.length, 2, "disabled tools must not run");
probes.observe(active, {handoff: true, generating: true});
await Promise.resolve();
await Promise.resolve();
assert.equal(sent.length, 4);
probes.observe(active, {handoff: true, generating: true});
await Promise.resolve();
assert.equal(sent.length, 4, "handoff should be sampled only once");
const beforeTerminal = await probes.capture(active, "before_terminal", {completed: true, captured: 1});
assert.ok(beforeTerminal);
assert.equal(sent.length, 6);
assert.equal(sent[4].event.stage, "before_terminal");
assert.equal(sent[4].event.classification, "completed_observed");
await probes.capture(active, "before_terminal", {completed: false});
assert.equal(sent.length, 6, "exactly one terminal evidence snapshot per request");
assert.ok(sent.every(x => x.event.type === "probe.event"));
assert.ok(!script.includes('type: "chat.completed"'));
assert.ok(!script.includes('type: "chat.error"'));
console.log("P0 Worker probes: request scoping, no sensitive URLs, bounded sampling, no terminal owner");
