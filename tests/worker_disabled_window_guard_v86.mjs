import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/background_worker_disabled_window_guard_v86.js", import.meta.url), "utf8");

let disabled = true;
let baseCalls = 0;
let removed = [];
let storageListener = null;
let releaseLate = null;
let lateMode = false;

const context = {
  console,
  setTimeout,
  clearTimeout,
  Promise,
  Date,
  Error,
  chat2apiCreateWindowStaggered: async meta => {
    baseCalls += 1;
    if (lateMode) {
      await new Promise(resolve => { releaseLate = resolve; });
      return { id: 303, meta };
    }
    return { id: 202, meta };
  },
  chrome: {
    storage: {
      local: {
        async get() { return { chat2apiWorkerMasterDisabledV61: disabled }; },
        async set() {},
      },
      onChanged: {
        addListener(listener) { storageListener = listener; },
      },
    },
    windows: {
      async remove(id) { removed.push(id); },
    },
  },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: "background_worker_disabled_window_guard_v86.js" });
await new Promise(resolve => setTimeout(resolve, 0));

await assert.rejects(
  () => context.chat2apiCreateWindowStaggered({ source: "reserve" }),
  /managed window refill is blocked/,
);
assert.equal(baseCalls, 0, "disabled Worker must not call the real window creator");

disabled = false;
storageListener({ chat2apiWorkerMasterDisabledV61: { newValue: false } }, "local");
const normal = await context.chat2apiCreateWindowStaggered({ source: "reserve" });
assert.equal(normal.id, 202);
assert.equal(baseCalls, 1);

lateMode = true;
const pending = context.chat2apiCreateWindowStaggered({ source: "reserve" });
await new Promise(resolve => setTimeout(resolve, 0));
disabled = true;
storageListener({ chat2apiWorkerMasterDisabledV61: { newValue: true } }, "local");
releaseLate();
await assert.rejects(() => pending, /disabled while a managed window was being created/);
assert.deepEqual(removed, [303], "a delayed refill that lands after disable must be closed immediately");

console.log("worker disabled window guard v86: ok");
