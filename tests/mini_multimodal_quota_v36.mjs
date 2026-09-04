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
assert.equal(api.revision, 91);

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

const screenshotModal = api.parseRecoveryAt(
  "升级以继续添加文件 你的免费版套餐文件上传次数已全部用完。立即升级以获取更多次数，或在 51分钟 内后重试",
  now,
);
assert.equal(screenshotModal, now + 51 * 60 * 1000);
assert.equal(
  api.quotaText('无法上传“ea3d3c3c-b9de-46e5-a7bd-024efd6ac8c6.PNG”。一次最多可上传 0 个文件'),
  true,
  "zero-upload toast must trip the circuit breaker even before a reset time is visible",
);
assert.equal(
  api.parseRecoveryAt('无法上传“demo.PNG”。一次最多可上传 0 个文件', now),
  null,
  "the detector may classify a quota toast without inventing a precise reset time",
);

const tomorrow = api.parseRecoveryAt(
  "You've reached the upload quota. It resets tomorrow at 8:15 AM.",
  now,
);
const expectedTomorrow = new Date(2026, 7, 25, 8, 15, 0, 0).getTime();
assert.equal(tomorrow, expectedTomorrow);

assert.equal(
  api.parseRecoveryAt("You've reached the file upload limit. Upgrade to continue.", now),
  null,
  "no precise cooldown may be invented by the parser when ChatGPT does not expose a reset time",
);
assert.equal(
  api.parseRecoveryAt("Your answer limit resets at 3:30 PM.", now),
  null,
  "non-upload quota text should not be classified as file upload quota",
);

console.log("mini multimodal quota parser v91 contract passed");
