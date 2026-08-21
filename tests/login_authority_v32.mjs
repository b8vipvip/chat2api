import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../chrome_extension/background_login_v27.js", import.meta.url), "utf8");
const storage = { chat2apiInitializationTabIdV32: 1 };
const storageListeners = [];
const runtimeListeners = [];
const tabs = new Map([
  [1, { id: 1, windowId: 11, url: "https://chatgpt.com/", status: "complete", active: false }],
  [2, { id: 2, windowId: 12, url: "https://chatgpt.com/", status: "complete", active: true }],
  [3, { id: 3, windowId: 13, url: "https://chatgpt.com/", status: "complete", active: false }],
]);
const responses = new Map([
  [1, { state: "ready", confidence: "high", strategy: "visible-composer", composer_ready: true, document_ready: true, checked_at_ms: Date.now() }],
  [2, { state: "login_required", confidence: "medium", strategy: "visible-auth-control", composer_ready: false, document_ready: true, checked_at_ms: Date.now() + 1 }],
  [3, { state: "ready", confidence: "high", strategy: "visible-composer", composer_ready: true, document_ready: true, checked_at_ms: Date.now() + 2 }],
]);

const chrome = {
  storage: {
    local: {
      async get(defaults = {}) {
        if (typeof defaults === "string") return { [defaults]: storage[defaults] };
        return { ...(defaults || {}), ...storage };
      },
      async set(values = {}) { Object.assign(storage, values); },
    },
    onChanged: { addListener(listener) { storageListeners.push(listener); } },
  },
  scripting: { async executeScript() { return []; } },
  tabs: {
    async get(tabId) {
      const tab = tabs.get(tabId);
      if (!tab) throw new Error("tab not found");
      return { ...tab };
    },
    async query(query = {}) {
      const rows = [...tabs.values()];
      if (query.active) return rows.filter(tab => tab.active).map(tab => ({ ...tab }));
      if (Number.isInteger(query.windowId)) return rows.filter(tab => tab.windowId === query.windowId).map(tab => ({ ...tab }));
      return rows.map(tab => ({ ...tab }));
    },
    async sendMessage(tabId) { return { ok: true, data: responses.get(tabId) }; },
    async update(tabId, changes) { Object.assign(tabs.get(tabId), changes); return { ...tabs.get(tabId) }; },
    onUpdated: { addListener() {} },
    onRemoved: { addListener() {} },
  },
  windows: {
    async create() { throw new Error("not expected"); },
    async update() { return {}; },
    async remove() {},
    onRemoved: { addListener() {} },
    onFocusChanged: { addListener() {} },
  },
  runtime: { onMessage: { addListener(listener) { runtimeListeners.push(listener); } } },
};

const sandbox = {
  console,
  URL,
  chrome,
  setTimeout,
  clearTimeout,
  sleep: async () => {},
  config: async () => ({ boundTabId: 3, socketState: "connected" }),
  chatTabs: async () => [...tabs.values()].map(tab => ({ ...tab })),
  isChatGptUrl: value => {
    try { return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname); }
    catch { return false; }
  },
  socketReady: () => true,
  sendExtensionStatus: async () => true,
  trySendSocket: async () => true,
  sendSocket: async () => true,
  __CHAT2API_NETWORK_GATE_V26__: { async allowPrewarm() { return true; } },
  __CHAT2API_CONVERSATION_WARM_POOL_V2__: { async onAffinityChanged() { return true; } },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "background_login_v27.js" });

const login = sandbox.__CHAT2API_LOGIN_READINESS_V27__;
const result = await login.detect(true);
assert.equal(result.state, "login_required", "explicit auth UI in the active authoritative page must beat stale ready composers");
assert.equal(result.strategy, "visible-auth-control");
assert.equal(storage.chatgptLoginState, "login_required");
assert.equal(storage.chatgptLoginComposerReady, false);

const markerStart = source.indexOf("async function candidateTabs");
const markerEnd = source.indexOf("async function retireAutomaticProbeIfReady");
assert.ok(markerStart >= 0 && markerEnd > markerStart);
const candidateBlock = source.slice(markerStart, markerEnd);
assert.ok(candidateBlock.includes("INIT_TAB_KEY"), "initialization tab must be an authoritative login candidate");
assert.ok(!candidateBlock.includes("for (const tab of await chatTabs())"), "all worker tabs must not vote on account login state");

console.log("login_authority_v32 VM contract passed");
