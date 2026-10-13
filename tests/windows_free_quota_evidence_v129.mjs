import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const code = fs.readFileSync(new URL("../chrome_extension/content_rate_limit_guard_v52.js", import.meta.url), "utf8");
const events = [];
const limitBanner = "你已达到限额。请稍后重试。";
const quotaCard = {
  innerText: limitBanner, textContent: limitBanner,
  getBoundingClientRect: () => ({ width: 250, height: 50 }),
};
const active = { requestId: "req_chinese_quota", cancelled: false };
const store = {};
const ctx = {
  chrome: {
    storage: { local: {
      get: async () => ({ chatgptRateLimitGuardV52: store.chatgptRateLimitGuardV52 || null }),
      set: async next => Object.assign(store, next),
    }},
    runtime: { sendMessage: async event => { events.push(event); return {ok:true}; } },
  },
  document: {
    documentElement: {},
    body: { innerText: "" },
    title: "ChatGPT",
    querySelector: selector => selector === "main" ? {innerText: ""} : null,
    querySelectorAll: selector => selector.includes("role='alert'")
      ? [quotaCard] : [],
  },
  location: {href:"https://chatgpt.com/c/123"},
  getComputedStyle: () => ({display:"block",visibility:"visible",opacity:"1"}),
  MutationObserver: class { observe(){} },
  setInterval: () => 1,
  setTimeout: () => 1,
  clearTimeout: () => {},
  Date, Math, console,
  __CHAT2API_REQUEST_CONTENT_V5__: { active },
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(code,ctx);
const sensor = ctx.__CHAT2API_RATE_LIMIT_CONTENT_V52__;
assert.equal(sensor.matches(limitBanner), true, "Chinese 限额 banner must match");
// The screenshot's red inline error may not have an aria role. The same
// sensor must recognize the notice without any extra terminal watcher.
ctx.document.querySelectorAll = selector => selector.includes("border-red") ? [quotaCard] : [];
assert.match(sensor.text(), /已达到限额/);
assert.equal(sensor.matchesHard(limitBanner), true, "assistant shell should recognize a Chinese account-limit error");
assert.equal(sensor.matches("聊天已暂停，额度将在稍后重置。"), false, "local conversation quota must not claim global cooldown");
assert.equal(sensor.matches("这个话术里提到商品限额，但不限制客户咨询"), false, "ordinary text is not a rate-limit signal");
await sensor.detect();
assert.equal(store.chatgptRateLimitGuardV52.active, true);
assert.equal(active.rateLimitEvidence.chatgpt_rate_limit_detected, true);
assert.match(active.rateLimitEvidence.chatgpt_rate_limit_text, /已达到限额/);
assert.equal(events.filter(e=>e.event?.type === "chat.error").length,0,
 "the sensor must never emit a terminal");
assert.equal(events.filter(e=>e.event?.type === "chat.diagnostics").length,1);
console.log("Chinese Free quota detection and evidence-only request ownership passed");
