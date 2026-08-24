import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/content_multimodal_quota_v36.js", import.meta.url), "utf8");

const context = {
  console,
  Date,
  Math,
  Number,
  String,
  Boolean,
  Array,
  Object,
  RegExp,
  Set,
  Element: class Element {},
  document: {
    documentElement: null,
    querySelectorAll() { return []; },
    querySelector() { return null; },
  },
  getComputedStyle() { return { display: "block", visibility: "visible" }; },
  MutationObserver: class MutationObserver {
    observe() {}
    disconnect() {}
  },
  chrome: {
    runtime: {
      onMessage: { addListener() {} },
      async sendMessage() { return { ok: true }; },
    },
  },
  queueMicrotask() {},
  setTimeout() { return 1; },
  setInterval() { return 1; },
  clearInterval() {},
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: "content_multimodal_quota_v36.js" });

const api = context.__CHAT2API_MULTIMODAL_QUOTA_V36__;
assert.ok(api, "quota detector API should be installed");

const now = new Date(2026, 7, 24, 10, 0, 0, 0).getTime();
const inThirty = api.parseRecoveryAt(
  "You've reached the file upload limit. Try again in 30 minutes.",
  now,
);
assert.equal(inThirty, now + 30 * 60 * 1000);

const chinese = api.parseRecoveryAt(
  "图片上传额度已达到上限，请在下午 3:42 后重试。",
  now,
);
const expectedChinese = new Date(2026, 7, 24, 15, 42, 0, 0).getTime();
assert.equal(chinese, expectedChinese);

const tomorrow = api.parseRecoveryAt(
  "You've reached the upload quota. It resets tomorrow at 8:15 AM.",
  now,
);
const expectedTomorrow = new Date(2026, 7, 25, 8, 15, 0, 0).getTime();
assert.equal(tomorrow, expectedTomorrow);

assert.equal(
  api.parseRecoveryAt("You've reached the file upload limit. Upgrade to continue.", now),
  null,
  "no cooldown may be invented when ChatGPT does not expose a reset time",
);
assert.equal(
  api.parseRecoveryAt("Your answer limit resets at 3:30 PM.", now),
  null,
  "non-quota text should not be classified as multimodal quota",
);

console.log("mini multimodal quota parser v36 contract passed");
