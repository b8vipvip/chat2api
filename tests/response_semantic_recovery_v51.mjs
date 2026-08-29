import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("chrome_extension/content_response_semantic_recovery_v51.js", "utf8");
const cleared = [];
const context = {
  console,
  Date,
  Promise,
  Map,
  Set,
  String,
  Number,
  Boolean,
  RegExp,
  Math,
  __CHAT2API_RESPONSE_STREAM_RECOVERY_V49__: {timer: 77},
  setInterval: () => 88,
  clearInterval: value => cleared.push(value),
  chrome: {runtime: {sendMessage: async () => ({ok: true})}},
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, {filename: "content_response_semantic_recovery_v51.js"});

const recovery = context.__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__;
assert.equal(recovery.version, 51);
assert.deepEqual(cleared, [77]);
assert.equal(context.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__.timer, null);
assert.equal(context.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__.superseded_by, "response-semantic-v51");

assert.equal(recovery.sanitize("ChatGPT said:").text, "");
assert.equal(recovery.sanitize("ChatGPT said:").filtered, true);
assert.equal(recovery.sanitize("ChatGPT said: 成功").text, "成功");
assert.equal(recovery.sanitize("Assistant said: real answer").text, "real answer");
assert.equal(recovery.sanitize("真实回答").text, "真实回答");
assert.equal(recovery.sanitize("真实回答").filtered, false);

console.log("response semantic recovery v51 contract ok");
