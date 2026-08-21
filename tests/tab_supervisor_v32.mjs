import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../chrome_extension/background_tab_supervisor_v32.js", import.meta.url), "utf8");
const storage = {};
const tabs = new Map();
const tabCreated = [];
const tabUpdated = [];
const tabRemoved = [];
const windowRemoved = [];
const storageChanged = [];
const alarmListeners = [];
const removedTabs = [];
let statusReports = 0;

for (let id = 1; id <= 36; id += 1) {
  tabs.set(id, { id, windowId: 100 + id, url: "https://chatgpt.com/", pendingUrl: "", active: id === 1, status: "complete" });
}
tabs.set(900, { id: 900, windowId: 900, url: "https://example.com/", active: false, status: "complete" });

const reserveSlots = new Map([
  ["reserve:1", { tab_id: 2, window_id: 102, created_at_ms: 100 }],
  ["reserve:2", { tab_id: 3, window_id: 103, created_at_ms: 200 }],
]);
const routes = {
  api: { tab_id: 4, window_id: 104, inflight_request_id: "req_active", last_active_at: Date.now() },
};
const activeRequests = new Map([["req_active", { tab_id: 4 }]]);

function fireStorage(values) {
  const changes = {};
  for (const [key, value] of Object.entries(values)) {
    changes[key] = { oldValue: storage[key], newValue: value };
    storage[key] = value;
  }
  for (const listener of storageChanged) listener(changes, "local");
}

const chrome = {
  storage: {
    local: {
      async get(defaults = {}) {
        if (typeof defaults === "string") return { [defaults]: storage[defaults] };
        return { ...(defaults || {}), ...storage };
      },
      async set(values = {}) { fireStorage(values); },
    },
    onChanged: { addListener(listener) { storageChanged.push(listener); } },
  },
  tabs: {
    async query(query = {}) {
      const rows = [...tabs.values()];
      if (query.active) return rows.filter(tab => tab.active).map(tab => ({ ...tab }));
      if (Number.isInteger(query.windowId)) return rows.filter(tab => tab.windowId === query.windowId).map(tab => ({ ...tab }));
      return rows.map(tab => ({ ...tab }));
    },
    async get(tabId) {
      const tab = tabs.get(tabId);
      if (!tab) throw new Error("tab not found");
      return { ...tab };
    },
    async remove(tabId) {
      if (!tabs.has(tabId)) return;
      tabs.delete(tabId);
      removedTabs.push(tabId);
      for (const listener of tabRemoved) listener(tabId, {});
    },
    onCreated: { addListener(listener) { tabCreated.push(listener); } },
    onUpdated: { addListener(listener) { tabUpdated.push(listener); } },
    onRemoved: { addListener(listener) { tabRemoved.push(listener); } },
  },
  windows: {
    async create() { throw new Error("The supervisor should adopt an existing orphan as initialization tab"); },
    onRemoved: { addListener(listener) { windowRemoved.push(listener); } },
  },
  alarms: {
    create() { return Promise.resolve(); },
    onAlarm: { addListener(listener) { alarmListeners.push(listener); } },
  },
};

const sandbox = {
  console,
  URL,
  chrome,
  setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 5)),
  clearTimeout,
  isChatGptUrl: value => {
    try { return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname); }
    catch { return false; }
  },
  chatTabs: async () => [...tabs.values()].filter(tab => /chatgpt\.com/.test(tab.url)).map(tab => ({ ...tab })),
  socketReady: () => true,
  sendExtensionStatus: async () => { statusReports += 1; return true; },
  __CHAT2API_RESERVE_POOL_V29__: { target: 3, reserveSlots },
  __CHAT2API_CONVERSATION_ROUTING_V1__: { routes, activeRequests },
  __CHAT2API_CONVERSATION_WARM_POOL_V2__: { warmSlots: new Map() },
  __CHAT2API_CONVERSATION_WORKERS_V24__: { maxWorkers: 3, requestRoutes: new Map() },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "background_tab_supervisor_v32.js" });
await new Promise(resolve => setTimeout(resolve, 15));

const supervisor = sandbox.__CHAT2API_TAB_SUPERVISOR_V32__;
assert.ok(supervisor, "tab supervisor must install");
supervisor.startedAt = Date.now() - 5000;
const snapshot = await supervisor.reconcile();
await new Promise(resolve => setTimeout(resolve, 20));

assert.equal(snapshot.target, 3);
assert.equal(snapshot.managed_worker_tabs, 3, "managed request capacity must honor the configured target");
assert.equal(snapshot.active_worker_tabs, 1, "active routed work must remain protected");
assert.equal(storage.chat2apiInitializationTabIdV32, 1, "one restored ChatGPT tab should be adopted as the initialization authority");
assert.ok(tabs.has(1), "initialization tab must remain open");
assert.ok(tabs.has(2) && tabs.has(3) && tabs.has(4), "three managed Worker tabs must remain open");
assert.ok(tabs.has(900), "non-ChatGPT tabs are outside supervisor ownership");
assert.equal([...tabs.values()].filter(tab => /chatgpt\.com/.test(tab.url)).length, 4, "idle steady state is target workers plus one initialization tab");
assert.equal(removedTabs.length, 32, "all restored unmanaged ChatGPT tabs must be removed");

const workerVisibleTabs = await sandbox.chatTabs();
assert.deepEqual(workerVisibleTabs.map(tab => tab.id).sort((a, b) => a - b), [2, 3, 4], "initialization tab must not participate in request routing or popup worker-tab counts");
assert.ok(statusReports >= 1, "cleanup should trigger a fresh extension status report");

// Agent remote login may expose a user-controlled ChatGPT tab that is not yet
// owned by route/warm/reserve state. Active interactive tabs must survive
// indefinitely; once no longer active/owned they become eligible for cleanup.
tabs.get(1).active = false;
tabs.set(50, { id: 50, windowId: 150, url: "https://chatgpt.com/", pendingUrl: "", active: true, status: "complete" });
const interactive = await supervisor.reconcile();
assert.ok(tabs.has(50), "active remote-login ChatGPT tab must never be reclaimed as an orphan");
assert.equal(interactive.protected_interactive_tabs, 1);
tabs.get(50).active = false;
await supervisor.reconcile();
assert.ok(!tabs.has(50), "inactive unowned ChatGPT tab should be reclaimed after interaction ends");

console.log("tab_supervisor_v32 VM contract passed");
