import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../chrome_extension/background_external_warm_v28.js", import.meta.url), "utf8");

const storage = {
  socketState: "disconnected",
  networkExternalReady: true,
  clientId: "ext_test",
  clientToken: "token_test",
  accountType: "free",
};
const storageListeners = [];
const tabUpdatedListeners = [];
const tabRemovedListeners = [];
const tabs = new Map();
const createdWindows = [];
const removedWindows = [];
let nextTabId = 101;
let nextWindowId = 201;
let socketOpen = false;
let statusReports = 0;
let loginSnapshot = {
  state: "checking",
  composer_ready: false,
  checked_at_ms: Date.now(),
};
let baseAffinityCalls = 0;

function fireStorage(changes) {
  for (const listener of storageListeners) listener(changes, "local");
}

const chrome = {
  storage: {
    local: {
      async get(defaults = {}) {
        if (typeof defaults === "string") return { [defaults]: storage[defaults] };
        return { ...(defaults || {}), ...storage };
      },
      async set(values = {}) {
        const changes = {};
        for (const [key, value] of Object.entries(values)) {
          const oldValue = storage[key];
          storage[key] = value;
          if (oldValue !== value) changes[key] = { oldValue, newValue: value };
        }
        if (Object.keys(changes).length) fireStorage(changes);
      },
    },
    onChanged: { addListener(listener) { storageListeners.push(listener); } },
  },
  tabs: {
    async get(tabId) {
      const tab = tabs.get(tabId);
      if (!tab) throw new Error("tab not found");
      return { ...tab };
    },
    async query(query = {}) {
      const rows = [...tabs.values()];
      if (Number.isInteger(query.windowId)) return rows.filter(tab => tab.windowId === query.windowId).map(tab => ({ ...tab }));
      return rows.map(tab => ({ ...tab }));
    },
    onUpdated: { addListener(listener) { tabUpdatedListeners.push(listener); } },
    onRemoved: { addListener(listener) { tabRemovedListeners.push(listener); } },
  },
  windows: {
    async create(options) {
      const id = nextWindowId++;
      const tabId = nextTabId++;
      const tab = { id: tabId, windowId: id, url: String(options.url), status: "loading" };
      tabs.set(tabId, tab);
      createdWindows.push({ id, tabId, options: { ...options } });
      return { id, tabs: [{ ...tab }] };
    },
    async remove(windowId) {
      removedWindows.push(windowId);
      for (const [tabId, tab] of [...tabs.entries()]) {
        if (tab.windowId === windowId) tabs.delete(tabId);
      }
    },
  },
};

const warmPool = {
  warmSlots: new Map(),
  openingSlots: new Map(),
  async onAffinityChanged() {
    baseAffinityCalls += 1;
    return true;
  },
};

const login = {
  async detect() { return { ...loginSnapshot }; },
  async snapshot() { return { ...loginSnapshot }; },
};

const sandbox = {
  console,
  URL,
  setTimeout,
  clearTimeout,
  chrome,
  socketReady: () => socketOpen,
  isChatGptUrl: value => {
    try { return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname); }
    catch { return false; }
  },
  sendExtensionStatus: async () => { statusReports += 1; },
  __CHAT2API_LOGIN_READINESS_V27__: login,
  __CHAT2API_CONVERSATION_WARM_POOL_V2__: warmPool,
  __CHAT2API_MODEL_AFFINITY_V23__: { presets: [] },
  chat2apiModelAffinityV23: {
    presetsForAccount(presets) { return presets || []; },
  },
};
sandbox.self = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "background_external_warm_v28.js" });
await new Promise(resolve => setTimeout(resolve, 10));

const coordinator = sandbox.__CHAT2API_EXTERNAL_WARM_BOOTSTRAP_V28__;
assert.ok(coordinator, "External warm bootstrap must install");
assert.equal(createdWindows.length, 0, "A disconnected extension must not open ChatGPT even on an external network");
assert.equal(warmPool.external_warm_bootstrap_v28, true, "Warm pool should be wrapped for bootstrap adoption before reconcile");

socketOpen = true;
const oldSocketState = storage.socketState;
storage.socketState = "connected";
fireStorage({ socketState: { oldValue: oldSocketState, newValue: "connected" } });
await new Promise(resolve => setTimeout(resolve, 20));

assert.equal(createdWindows.length, 1, "Connected + external must immediately create one dedicated ChatGPT warm window");
assert.equal(createdWindows[0].options.url, "https://chatgpt.com/");
assert.equal(createdWindows[0].options.focused, false, "Automatic warm window must not steal focus");
assert.equal(storage.chatgptExternalWarmTabIdV28, createdWindows[0].tabId);
assert.ok(statusReports >= 1, "Opening the warm window must immediately publish extension status/login detection state");

await coordinator.ensure();
assert.equal(createdWindows.length, 1, "Repeated eligibility checks must be idempotent while the bootstrap window exists");

const tabId = createdWindows[0].tabId;
const windowId = createdWindows[0].id;
const tab = tabs.get(tabId);
tab.status = "complete";
loginSnapshot = {
  state: "ready",
  composer_ready: true,
  confidence: "high",
  strategy: "visible-composer",
  checked_at_ms: Date.now(),
};
for (const listener of tabUpdatedListeners) listener(tabId, { status: "complete" }, { ...tab });
await new Promise(resolve => setTimeout(resolve, 20));

assert.ok(statusReports >= 2, "Completed ChatGPT load must publish the confirmed login state to the server");
assert.equal(warmPool.warmSlots.size, 1, "The same bootstrap window must be adopted into the warm pool after login is ready");
const adopted = [...warmPool.warmSlots.values()][0];
assert.equal(adopted.tab_id, tabId);
assert.equal(adopted.window_id, windowId);
assert.equal(adopted.strategy, "external-network-bootstrap-generic");
assert.equal(storage.chatgptExternalWarmTabIdV28, null, "Tracking must be released after warm-pool adoption");
assert.equal(removedWindows.length, 0, "The ready bootstrap window must not be closed and reopened before warm-pool use");

await warmPool.onAffinityChanged();
assert.ok(baseAffinityCalls >= 1, "Existing warm-pool reconcile behavior must still run after bootstrap adoption");
assert.equal(createdWindows.length, 1, "Warm-pool affinity callback must not create a second bootstrap window");

console.log("external_warm_bootstrap_v28 VM contract passed");
