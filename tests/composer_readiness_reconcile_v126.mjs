import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/content_request_v6.js", import.meta.url), "utf8");
const listeners = [];
const context = {
  console, setTimeout, clearTimeout,
  chrome: {runtime: {onMessage: {
    addListener: listener => listeners.push(listener),
    removeListener: () => {},
  }}},
  document: {}, 
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context);
const contract = context.__CHAT2API_REQUEST_CONTENT_V6__?.contract;
assert.equal(typeof contract?.promptMatchesText, "function");
const prompt = "前缀".repeat(1500) + "订单号123456" + "后缀".repeat(1500);
const state = {promptNormalized: prompt};
assert.equal(contract.promptMatchEvidence(prompt, state), "exact");
assert.equal(contract.promptMatchesText(prompt, state), true);
// A hydrated rich editor can insert layout spaces between two block nodes.
const whitespaceDrift = "前缀".repeat(1500) + " 订单号123456 " + "后缀".repeat(1500);
assert.equal(contract.promptMatchEvidence(whitespaceDrift, state), "rich-editor-whitespace");
assert.equal(contract.promptMatchesText(whitespaceDrift, state), true);
// Never route meaningful edits: order numbers, customer data and punctuation
// are semantic content, even if edit distance is only one character.
assert.equal(contract.promptMatchesText(prompt.replace("123456", "123457"), state), false);
assert.equal(contract.promptMatchesText(prompt.replace("订单号", "订单"), state), false);
assert.equal(contract.promptMatchesText("不相关草稿", state), false);
assert.ok(listeners.length > 0);
console.log("Composer strict content match plus rich-editor whitespace reconciliation passed");
