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

// Regression for 0.22.175 TX03: both ~800-char prompts were verified after
// native insertion, then acquired one extra editor-layout character before
// submission. The Send button was actually present and enabled.
const shortPrompt = "Policy section uses ordinary English words and fixed IDs. ".repeat(11) +
  "\\n\\n[User]\\n帮我生成小红帽的头像图片\\n\\n[Assistant]\\n已有图片\\n\\n[User]\\n帮我生成小红花的头像图片";
const normalizedShort = shortPrompt.replace(/\\s+/g, " ").trim().normalize("NFC");
const shortState = {prompt: shortPrompt, promptNormalized: normalizedShort};
assert.ok(normalizedShort.length > 500 && normalizedShort.length < 2048);
const layoutDrift = normalizedShort.replace("生成小红花的头像", "生成小红 花的头像");
assert.equal(layoutDrift.length, normalizedShort.length + 1);
assert.equal(contract.promptMatchEvidence(layoutDrift, shortState), "rich-editor-layout-whitespace");
assert.equal(contract.promptMatchesText(layoutDrift, shortState), true);
const safe = contract.promptDriftSummary(layoutDrift, shortState);
assert.equal(safe.send_prompt_drift_kind, "whitespace_inserted");
assert.equal(safe.send_prompt_drift_delta, 1);
assert.equal(safe.send_prompt_nonwhitespace_equal, true);
assert.equal(safe.send_prompt_layout_allowed, true);
assert.equal(JSON.stringify(safe).includes("小红"), false);

// Only multiline rich-editor layout may qualify: no unsafe widening of
// single-line prompts, tokens, URLs, numeric IDs, arbitrary added characters,
// deleted spaces, or a genuine 1-character edit to the user's Chinese request.
assert.equal(contract.promptMatchesText(layoutDrift, {
  prompt: shortPrompt.replaceAll("\\n", " "), promptNormalized: normalizedShort,
}), false);
assert.equal(contract.promptMatchesText(normalizedShort.replace("小红花", "小红莓"), shortState), false);
assert.equal(contract.promptMatchesText(normalizedShort.replace("小红花", "小红花!"), shortState), false);
assert.equal(contract.promptMatchesText(normalizedShort.replace("123", "123 "), shortState), false);
const urlPrompt = shortPrompt + " image: https://example.com/api/media/item/token";
const urlNormalized = urlPrompt.replace(/\\s+/g, " ").trim().normalize("NFC");
const urlState = {prompt: urlPrompt, promptNormalized: urlNormalized};
const brokenUrl = urlNormalized.replace("example.com", "example. com");
assert.equal(contract.promptMatchesText(brokenUrl, urlState), false);
assert.equal(contract.promptDriftSummary(brokenUrl, urlState).send_prompt_layout_allowed, false);
assert.equal(contract.promptMatchesText(normalizedShort.replace("小红花", "小红"), shortState), false);
assert.equal(contract.promptMatchesText(normalizedShort.replace("头像", "头 像"), {prompt: "头像", promptNormalized: "头像"}), false);
assert.ok(listeners.length > 0);
console.log("Composer strict content match plus rich-editor whitespace reconciliation passed");
