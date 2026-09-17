import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("chrome_extension/content_response_semantic_recovery_v51.js", "utf8");
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
  setInterval: () => { throw new Error("v51 helper must not install an interval"); },
  clearInterval: () => {},
  chrome: {runtime: {sendMessage: async () => ({ok: true})}},
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, {filename: "content_response_semantic_recovery_v51.js"});

const recovery = context.__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__;
assert.equal(recovery.version, 51);
assert.equal(recovery.mode, "semantic-helper-only");
assert.equal(recovery.owner, "request-v6");
assert.equal(recovery.timer, null);
assert.equal(context.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__, undefined);

assert.equal(recovery.sanitize("ChatGPT said:").text, "");
assert.equal(recovery.sanitize("ChatGPT said:").filtered, true);
assert.equal(recovery.sanitize("ChatGPT said: 成功").text, "成功");
assert.equal(recovery.sanitize("Assistant said: real answer").text, "real answer");
assert.equal(recovery.sanitize("真实回答").text, "真实回答");
assert.equal(recovery.sanitize("真实回答").filtered, false);

console.log("response semantic helper v51 request-v6 owner contract ok");
