import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../chrome_extension/background_login_v27.js", import.meta.url), "utf8");

const storage = {};
const storageListeners = [];
const runtimeListeners = [];
const tabUpdatedListeners = [];
const tabRemovedListeners = [];
const windowRemovedListeners = [];
const windowFocusListeners = [];
const tabs = new Map();
const detectorResponses = new Map();
const createdWindows = [];
const removedWindows = [];
const focusedWindows = [];
let nextTabId = 101;
let nextWindowId = 201;
let networkAllowed = true;
let warmAffinityCalls = 0;

function fireStorage(changes) {
  for (const listener of storageListeners) listener(changes, "local");
}

const chrome = {
  runtime: {
    onMessage: { addListener(listener) { runtimeListeners.push(listener); } },
  },
  storage: {
    local: {
      async get(defaults = {}) {
        if (typeof defaults === "string") return { [defaults]: storage[defaults] };
        return { ...(defaults || {}), ...storage };
      },
      async set(values = {}) {
        const changes = {};
        for (const [key, value] of Object.entries(values)) {
          changes[key] = { oldValue: storage[key], newValue: value };
          storage[key] = value;
        }
        fireStorage(changes);
      },
    },
    onChanged: { addListener(listener) { storageListeners.push(listener); } },
  },
  scripting: {
    async executeScript() { return []; },
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
      if (query.active) return rows.filter(tab => tab.active).map(tab => ({ ...tab }));
      return rows.map(tab => ({ ...tab }));
    },
    async sendMessage(tabId, message) {
      assert.equal(message.type, "chat2api.login.detect.v27");
      const response = detectorResponses.get(tabId);
      if (!response) throw new Error("detector unavailable");
      return { ok: true, data: response };
    },
    async update(tabId, changes) {
      const tab = tabs.get(tabId);
      if (!tab) throw new Error("tab not found");
      Object.assign(tab, changes);
      return { ...tab };
    },
    onUpdated: { addListener(listener) { tabUpdatedListeners.push(listener); } },
    onRemoved: { addListener(listener) { tabRemovedListeners.push(listener); } },
  },
  windows: {
    async create(options) {
      const windowId = nextWindowId++;
      const tabId = nextTabId++;
      const tab = { id: tabId, windowId, url: String(options.url), pendingUrl: "", status: "loading", active: Boolean(options.focused) };
      tabs.set(tabId, tab);
      createdWindows.push({ id: windowId, tabId, options: { ...options } });
      return { id: windowId, tabs: [{ ...tab }] };
    },
    async update(windowId, changes) {
      if (changes?.focused) focusedWindows.push(windowId);
      return { id: windowId, ...changes };
    },
    async remove(windowId) {
      removedWindows.push(windowId);
      const removedTabIds = [...tabs.values()].filter(tab => tab.windowId === windowId).map(tab => tab.id);
      for (const tabId of removedTabIds) tabs.delete(tabId);
      for (const listener of tabRemovedListeners) for (const tabId of removedTabIds) listener(tabId, { windowId, isWindowClosing: true });
      for (const listener of windowRemovedListeners) listener(windowId);
    },
    onRemoved: { addListener(listener) { windowRemovedListeners.push(listener); } },
    onFocusChanged: { addListener(listener) { windowFocusListeners.push(listener); } },
  },
};

const sandbox = {
  console,
  URL,
  setTimeout,
  clearTimeout,
  chrome,
  sleep: ms => new Promise(resolve => setTimeout(resolve, Math.min(ms, 1))),
  config: async () => ({ boundTabId: null, socketState: "connected" }),
  chatTabs: async () => [...tabs.values()].filter(tab => /chatgpt\.com|chat\.openai\.com/.test(tab.url || "")).map(tab => ({ ...tab })),
  isChatGptUrl: value => {
    try { return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname); }
    catch { return false; }
  },
  socketReady: () => true,
  sendExtensionStatus: async () => true,
  trySendSocket: async () => true,
  sendSocket: async () => true,
  __CHAT2API_NETWORK_GATE_V26__: {
    async allowPrewarm() { return networkAllowed; },
  },
  __CHAT2API_CONVERSATION_WARM_POOL_V2__: {
    async onAffinityChanged() { warmAffinityCalls += 1; return true; },
  },
};
sandbox.self = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "background_login_v27.js" });
await new Promise(resolve => setTimeout(resolve, 5));

const login = sandbox.__CHAT2API_LOGIN_READINESS_V27__;
const networkGate = sandbox.__CHAT2API_NETWORK_GATE_V26__;
const warmPool = sandbox.__CHAT2API_CONVERSATION_WARM_POOL_V2__;
assert.ok(login, "Login readiness coordinator must install");
assert.equal(networkGate.login_readiness_gate_v27, true, "Network prewarm gate must be wrapped by login readiness");
assert.equal(warmPool.login_readiness_gate_v27, true, "Affinity prewarm path must also be login-gated");

networkAllowed = false;
assert.equal(await networkGate.allowPrewarm(), false);
assert.equal(createdWindows.length, 0, "No login probe should open when the network gate rejects proactive prewarm");

networkAllowed = true;
assert.equal(await networkGate.allowPrewarm(), false, "First external prewarm check should create a readiness probe, not declare ready");
assert.equal(createdWindows.length, 1);
assert.equal(createdWindows[0].options.url, "https://chatgpt.com/");
assert.equal(createdWindows[0].options.focused, false, "Startup readiness window must remain unfocused");
assert.equal(storage.chatgptLoginState, "checking");
assert.equal(storage.chatgptLoginProbeAdoptable, true);

const probeTabId = createdWindows[0].tabId;
const probeWindowId = createdWindows[0].id;
const probeTab = tabs.get(probeTabId);
probeTab.status = "complete";
detectorResponses.set(probeTabId, {
  state: "ready",
  confidence: "high",
  strategy: "visible-composer",
  composer_ready: true,
  document_ready: true,
  checked_at_ms: Date.now(),
});
for (const listener of tabUpdatedListeners) listener(probeTabId, { status: "complete" }, { ...probeTab });
await new Promise(resolve => setTimeout(resolve, 20));
assert.equal(storage.chatgptLoginState, "ready");
assert.equal(storage.chatgptLoginComposerReady, true);
assert.equal(storage.chatgptLoginProbeTabId, null, "Automatic readiness probe must release its tracking after login is confirmed");
assert.ok(removedWindows.includes(probeWindowId), "Automatic readiness probe window should be retired before dedicated warm windows take over");
assert.ok(warmAffinityCalls >= 1, "Login transition to ready must kick the gated warm pool");
assert.equal(await networkGate.allowPrewarm(), true, "Fresh confirmed login state must allow proactive prewarm on an external network");

const authTabId = 303;
const authWindowId = 403;
tabs.set(authTabId, { id: authTabId, windowId: authWindowId, url: "https://auth.openai.com/authorize", status: "complete", active: false });
await chrome.storage.local.set({
  chatgptLoginProbeTabId: authTabId,
  chatgptLoginProbeWindowId: authWindowId,
  chatgptLoginProbeAdoptable: true,
});
login.bootValidated = false;
const loginRequired = await login.detect(true);
assert.equal(loginRequired.state, "login_required");
assert.equal(loginRequired.strategy, "auth-redirect-url");

const runtimeHandler = runtimeListeners.find(listener => {
  let handled = false;
  try { handled = listener({ type: "popup.login.open" }, {}, () => {}) === true; } catch {}
  return handled;
});
assert.ok(runtimeHandler, "Popup login-open handler must be registered");
let popupResponse = null;
runtimeHandler({ type: "popup.login.open" }, {}, response => { popupResponse = response; });
await new Promise(resolve => setTimeout(resolve, 10));
assert.equal(popupResponse?.ok, true);
assert.equal(popupResponse?.data?.existing, true, "Manual login action must reuse the existing auth window");
assert.ok(focusedWindows.includes(authWindowId));
assert.equal(storage.chatgptLoginProbeAdoptable, false, "A user-visible login window must never be auto-retired as a background probe");
assert.equal(createdWindows.length, 1, "Manual login action must not create a duplicate auth window when one already exists");

console.log("login_readiness_v27 VM contract passed");
