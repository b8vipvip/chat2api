import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const storageListeners = [];
const runtimeListeners = [];
const windowRemovedListeners = [];
const tabRemovedListeners = [];
const alarms = new Map();
const sent = [];

const storage = {
  serverUrl: "https://chat2api.example",
  clientId: "ext_test",
  clientToken: "token_test",
  socketState: "connected",
  networkExternalReady: true,
  chatgptLoginState: "ready",
  chatgptLoginComposerReady: true,
  chatgptExternalWarmWindowIdV28: null,
};

async function storageGet(query) {
  if (typeof query === "string") return {[query]: storage[query]};
  if (Array.isArray(query)) return Object.fromEntries(query.map(key => [key, storage[key]]));
  if (!query || typeof query !== "object") return {...storage};
  const out = {};
  for (const [key, fallback] of Object.entries(query)) out[key] = storage[key] === undefined ? fallback : storage[key];
  return out;
}

async function storageSet(values) {
  const changes = {};
  for (const [key, value] of Object.entries(values || {})) {
    const oldValue = storage[key];
    storage[key] = value;
    changes[key] = {oldValue, newValue: value};
  }
  for (const listener of [...storageListeners]) listener(changes, "local");
}

let nextWindowId = 100;
let nextTabId = 1000;
const windows = new Map();
const tabs = new Map();

function cloneTab(tab) {
  return tab ? {...tab} : tab;
}

async function createWindow(options = {}) {
  const id = nextWindowId++;
  const tabId = nextTabId++;
  const tab = {id: tabId, windowId: id, url: options.url || "about:blank", pendingUrl: "", status: "complete"};
  tabs.set(tabId, tab);
  const win = {id, tabs: [tab]};
  windows.set(id, win);
  return {id, tabs: [cloneTab(tab)]};
}

async function removeWindow(id) {
  const win = windows.get(id);
  if (!win) return;
  windows.delete(id);
  for (const tab of win.tabs || []) {
    tabs.delete(tab.id);
    for (const listener of [...tabRemovedListeners]) listener(tab.id, {windowId: id, isWindowClosing: true});
  }
  for (const listener of [...windowRemovedListeners]) listener(id);
}

const chrome = {
  storage: {
    local: {get: storageGet, set: storageSet},
    onChanged: {addListener: listener => storageListeners.push(listener)},
  },
  windows: {
    create: createWindow,
    remove: removeWindow,
    getAll: async ({populate} = {}) => [...windows.values()].map(win => ({
      id: win.id,
      tabs: populate ? (win.tabs || []).map(cloneTab) : undefined,
    })),
    onRemoved: {addListener: listener => windowRemovedListeners.push(listener)},
  },
  tabs: {
    get: async id => {
      const tab = tabs.get(id);
      if (!tab) throw new Error("tab missing");
      return cloneTab(tab);
    },
    query: async ({windowId} = {}) => [...tabs.values()].filter(tab => windowId === undefined || tab.windowId === windowId).map(cloneTab),
    onRemoved: {addListener: listener => tabRemovedListeners.push(listener)},
  },
  scripting: {
    executeScript: async () => [{result: true}],
  },
  alarms: {
    create: async (name, info) => { alarms.set(name, {...info}); },
  },
  runtime: {
    onMessage: {addListener: listener => runtimeListeners.push(listener)},
  },
};

globalThis.chrome = chrome;
globalThis.isChatGptUrl = value => /^https:\/\/(?:www\.)?(?:chatgpt\.com|chat\.openai\.com)\//.test(String(value || ""));
globalThis.ensureContent = async () => true;
globalThis.socketReady = () => storage.socketState === "connected";
globalThis.config = async () => ({
  serverUrl: storage.serverUrl,
  clientId: storage.clientId,
  clientToken: storage.clientToken,
  socketState: storage.socketState,
});
globalThis.trySendSocket = async message => {
  if (storage.socketState !== "connected") return false;
  sent.push(JSON.parse(JSON.stringify(message)));
  return true;
};
globalThis.fetch = async url => {
  assert.match(String(url), /\/api\/extensions\/runtime-config$/);
  return {
    ok: true,
    status: 200,
    json: async () => ({reserve_window_target: 3, route_idle_close_seconds: 600, max_reserve_window_target: 32}),
  };
};

// Do not leave a real interval running in this VM contract.
globalThis.setInterval = () => 1;
globalThis.clearInterval = () => {};

const router = {loaded: true, routes: {}, openings: new Map(), activeRequests: new Map()};
const warmPool = {warmSlots: new Map(), openingSlots: new Map()};
globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__ = router;
globalThis.__CHAT2API_CONVERSATION_WARM_POOL_V2__ = warmPool;

globalThis.resolveTargetTabForRequest = async message => {
  const key = String(message?.routing?.api_key_id || "");
  const route = router.routes[key];
  if (!route?.tab_id) throw new Error("reserve wrapper did not provide a routed tab");
  const tab = await chrome.tabs.get(route.tab_id);
  route.inflight_request_id = message.request_id;
  route.last_active_at = Date.now();
  route.close_after = null;
  router.activeRequests.set(message.request_id, {tab_id: tab.id, window_id: tab.windowId});
  return tab;
};

const reserveSource = fs.readFileSync("chrome_extension/background_reserve_pool_v29.js", "utf8");
vm.runInThisContext(reserveSource, {filename: "background_reserve_pool_v29.js"});
const reconnectSource = fs.readFileSync("chrome_extension/background_reserve_status_reconnect_v29.js", "utf8");
vm.runInThisContext(reconnectSource, {filename: "background_reserve_status_reconnect_v29.js"});

await new Promise(resolve => setTimeout(resolve, 900));
const reserve = globalThis.__CHAT2API_RESERVE_POOL_V29__;
assert.ok(reserve, "reserve pool should install");

let snapshot = await reserve.snapshot();
assert.equal(snapshot.target, 3, "server concurrency should become reserve target");
assert.equal(snapshot.total, 3, "reserve pool should pre-open target window total");
assert.equal(snapshot.active, 0);

// Reproduce the production failure shape: an apparently ready spare survived for
// more than four hours. It must be evicted before route allocation even though its
// tab and composer still exist.
const staleReserve = [...reserve.reserveSlots.values()][0];
assert.ok(staleReserve, "startup should create at least one reserve slot");
staleReserve.ready_at_ms = Date.now() - (4 * 60 * 60 * 1000);
const staleReserveTabId = staleReserve.tab_id;
assert.equal(reserve.isFresh(staleReserve), false, "four-hour reserve slots must fail the request-time freshness gate");

const routed = await globalThis.resolveTargetTabForRequest({
  type: "chat.request",
  request_id: "req-1",
  routing: {api_key_id: "key-1", worker_limit: 3},
});
assert.ok(routed?.id);
assert.notEqual(routed.id, staleReserveTabId, "a stale reserve page must never receive the API request");
await assert.rejects(() => chrome.tabs.get(staleReserveTabId), /tab missing/, "the stale reserve window should be closed");
const routeFreshness = sent.find(item => item?.type === "chat.diagnostics" && item?.request_id === "req-1" && item?.diagnostics?.conversation_reserve_prewarm_hit === true);
assert.ok(routeFreshness, "request diagnostics should record the fresh reserve claim");
assert.equal(routeFreshness.diagnostics.conversation_prewarm_freshness_gate, "spare-max-ready-age-v39");
assert.ok(routeFreshness.diagnostics.conversation_reserve_prewarm_ready_age_ms < 30 * 60 * 1000);
await new Promise(resolve => setTimeout(resolve, 260));
snapshot = await reserve.snapshot();
assert.equal(snapshot.total, 4, "one routed request plus three reserve spares should keep four managed windows");
assert.equal(snapshot.active, 1, "claimed routed window should be counted as active");

const route = router.routes["key-1"];
router.activeRequests.delete("req-1");
route.inflight_request_id = null;
route.last_active_at = Date.now();
route.close_after = route.last_active_at + 5 * 60 * 1000;
await chrome.storage.local.set({chat2apiConversationRoutesV1: router.routes});
await reserve.patchIdleDeadlines();
assert.ok(Math.abs(route.close_after - (route.last_active_at + 5 * 60 * 1000)) <= 1000, "idle deadline must be normalized to five minutes");
assert.ok(alarms.has(`chat2api-route-close:${route.window_id}`), "five-minute route close alarm should replace historical deadline");

const closedWindowId = route.window_id;
await chrome.windows.remove(closedWindowId);
route.tab_id = null;
route.window_id = null;
route.close_after = null;
await chrome.storage.local.set({chat2apiConversationRoutesV1: router.routes});
await reserve.reconcile();
await new Promise(resolve => setTimeout(resolve, 200));
snapshot = await reserve.snapshot();
assert.equal(snapshot.total, 3, "closed idle route should be replenished back to target");
assert.equal(snapshot.active, 0);

const sentBeforeReconnect = sent.length;
await chrome.storage.local.set({socketState: "disconnected"});
await reserve.report(true);
await chrome.storage.local.set({socketState: "connected"});
await new Promise(resolve => setTimeout(resolve, 120));
assert.ok(sent.length > sentBeforeReconnect, "socket reconnect should force reserve telemetry report");

const latest = [...sent].reverse().find(item => item?.metadata?.reserve_window_telemetry_version === 29);
assert.ok(latest, "reserve telemetry status should be emitted");
assert.equal(latest.metadata.reserve_window_total, 3);
assert.equal(latest.metadata.reserve_window_active, 0);
assert.equal(latest.metadata.reserve_window_target, 3);
assert.equal(latest.metadata.reserve_window_idle_close_seconds, 300);
assert.equal(latest.metadata.reserve_window_freshness_version, 39);
assert.equal(latest.metadata.reserve_window_max_ready_age_seconds, 1800);

console.log("reserve_pool_v29 VM contract passed");
