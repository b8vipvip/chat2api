import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const storage = {};
const storageListeners = [];
const runtimeListeners = [];
const tabRemovedListeners = [];
const windows = new Map();
const tabs = new Map();
const closedWindows = [];

function addWindow(windowId, tabId) {
  const tab = {id: tabId, windowId, url: "https://chatgpt.com/", status: "complete"};
  tabs.set(tabId, tab);
  windows.set(windowId, {id: windowId, tabs: [tab]});
  return tab;
}

async function storageGet(query) {
  if (typeof query === "string") return {[query]: storage[query]};
  if (!query || typeof query !== "object") return {...storage};
  return Object.fromEntries(Object.entries(query).map(([key, fallback]) => [key, storage[key] ?? fallback]));
}

async function storageSet(values) {
  const changes = {};
  for (const [key, value] of Object.entries(values || {})) {
    changes[key] = {oldValue: storage[key], newValue: value};
    storage[key] = value;
  }
  for (const listener of [...storageListeners]) listener(changes, "local");
}

const chrome = {
  storage: {
    local: {get: storageGet, set: storageSet},
    onChanged: {addListener: listener => storageListeners.push(listener)},
  },
  windows: {
    create: async () => { throw new Error("unexpected warm creation in prune-only test"); },
    remove: async windowId => {
      const win = windows.get(windowId);
      if (!win) throw new Error("window missing");
      windows.delete(windowId);
      closedWindows.push(windowId);
      for (const tab of win.tabs || []) {
        tabs.delete(tab.id);
        for (const listener of [...tabRemovedListeners]) listener(tab.id, {windowId, isWindowClosing: true});
      }
    },
  },
  tabs: {
    get: async tabId => {
      const tab = tabs.get(tabId);
      if (!tab) throw new Error("tab missing");
      return {...tab};
    },
    query: async ({windowId} = {}) => [...tabs.values()]
      .filter(tab => windowId === undefined || tab.windowId === windowId)
      .map(tab => ({...tab})),
    onRemoved: {addListener: listener => tabRemovedListeners.push(listener)},
  },
  scripting: {executeScript: async () => [{result: {composer: true, model_picker: true, document_ready: true}}]},
  runtime: {onMessage: {addListener: listener => runtimeListeners.push(listener)}},
};

globalThis.chrome = chrome;
globalThis.isChatGptUrl = value => String(value || "").startsWith("https://chatgpt.com/");
globalThis.ensureContent = async () => true;
globalThis.config = async () => ({clientId: "", clientToken: "", socketState: "disconnected"});
globalThis.trySendSocket = async () => true;
globalThis.resolveTargetTabForRequest = async () => null;

// Prevent the extension's proactive startup timer from opening unrelated windows.
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => {};

const router = {loaded: true, routes: {}};
globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__ = router;

const source = fs.readFileSync("chrome_extension/conversation_warm_pool_v2.js", "utf8");
vm.runInThisContext(source, {filename: "conversation_warm_pool_v2.js"});

const pool = globalThis.__CHAT2API_CONVERSATION_WARM_POOL_V2__;
assert.ok(pool, "warm pool should install");
assert.equal(pool.maxReadyAgeMs, 30 * 60 * 1000);

const now = Date.now();
const staleAgeMs = 15_488_794;
const stale = {slot_key: "stale", tab_id: 1001, window_id: 101, created_at_ms: now - staleAgeMs, ready_at_ms: now - staleAgeMs};
const fresh = {slot_key: "fresh", tab_id: 1002, window_id: 102, created_at_ms: now - 30_000, ready_at_ms: now - 25_000};
const claimedStale = {slot_key: "claimed", tab_id: 1003, window_id: 103, created_at_ms: now - staleAgeMs, ready_at_ms: now - staleAgeMs};
addWindow(stale.window_id, stale.tab_id);
addWindow(fresh.window_id, fresh.tab_id);
addWindow(claimedStale.window_id, claimedStale.tab_id);
router.routes.key_claimed = {tab_id: claimedStale.tab_id, window_id: claimedStale.window_id, inflight_request_id: "req-live"};
pool.warmSlots.set(stale.slot_key, stale);
pool.warmSlots.set(fresh.slot_key, fresh);
pool.warmSlots.set(claimedStale.slot_key, claimedStale);

assert.equal(pool.isFresh(stale, now), false, "the production four-hour spare must be stale");
assert.equal(pool.isFresh(fresh, now), true, "a recent spare must remain claimable");
assert.equal(pool.isFresh({slot_key: "missing-age"}, now), false, "missing freshness evidence must fail closed");

const result = await pool.pruneExpired(now, false);
assert.equal(result.expired_count, 2);
assert.equal(result.closed_windows, 1);
assert.equal(result.detached_routed_windows, 1);
assert.ok(result.max_ready_age_ms >= staleAgeMs);
assert.deepEqual([...pool.warmSlots.keys()], ["fresh"], "only the fresh spare should remain in the pool");
assert.deepEqual(closedWindows, [stale.window_id], "only the unclaimed stale spare should be closed");
assert.equal(tabs.has(stale.tab_id), false);
assert.equal(tabs.has(claimedStale.tab_id), true, "an already-routed page must never be closed by spare cleanup");
assert.deepEqual(storage.chat2apiConversationWarmPoolV2.slots.map(item => item.slot_key), ["fresh"]);

console.log("prewarm_freshness_v39 VM contract passed");
