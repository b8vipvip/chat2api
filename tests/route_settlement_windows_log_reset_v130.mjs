import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const routeSource = fs.readFileSync(new URL("../chrome_extension/conversation_routing.js", import.meta.url), "utf8");
const logSource = fs.readFileSync(new URL("../chrome_extension/background_logging.js", import.meta.url), "utf8");
const wait = ms => new Promise(done => setTimeout(done, ms));

async function routeScenario({ stale = false, strict = true } = {}) {
  const callbacks = {};
  const routes = {
    key_A: {
      api_key_id: "key_A", generation: 1, window_id: 100, tab_id: 101,
      window_owned: true, inflight_request_id: "req_previous", turn_count: 0, text_chars: 0,
      attachment_count: 0, slow_load_strikes: 0, last_active_at: 0, close_delay_ms: 300000,
    },
  };
  const data = { chat2apiConversationRoutesV1: routes };
  let removedWindows = 0, createdWindows = 0, diagnostics = [];
  let now = 1_000_000, nextTimerId = 1, nextWindowId = 200, nextTabId = 201;
  const timers = new Map();
  const windows = new Map([[100, { id: 100 }]]);
  const tabs = new Map([[101, { id: 101, windowId: 100, url: "https://chatgpt.com/c/abc", status: "complete" }]]);
  const alarms = new Map();
  class FakeDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const chrome = {
    storage: { local: {
      get: async key => typeof key === "string" ? ({ [key]: data[key] }) : ({ ...key, ...data }),
      set: async obj => { Object.assign(data, obj); },
      remove: async key => { delete data[key]; },
    } },
    tabs: {
      get: async id => {
        const tab = tabs.get(Number(id));
        if (!tab) throw new Error("tab not found");
        return { ...tab };
      },
      update: async (id, changes) => {
        const tab = tabs.get(Number(id));
        if (!tab) throw new Error("tab not found");
        Object.assign(tab, changes);
        return { ...tab };
      },
      query: async ({ windowId } = {}) => [...tabs.values()].filter(tab => windowId == null || tab.windowId === windowId).map(tab => ({ ...tab })),
      onUpdated: { addListener(fn) { callbacks.updated = fn; } },
      onRemoved: { addListener(fn) { callbacks.tabRemoved = fn; } },
    },
    windows: {
      create: async () => {
        createdWindows++;
        const id = nextWindowId++, tabId = nextTabId++;
        const tab = { id: tabId, windowId: id, url: "https://chatgpt.com/", status: "complete" };
        windows.set(id, { id });
        tabs.set(tabId, tab);
        return { id, tabs: [{ ...tab }] };
      },
      remove: async id => {
        removedWindows++;
        windows.delete(Number(id));
        for (const [tabId, tab] of tabs) if (tab.windowId === Number(id)) tabs.delete(tabId);
      },
      onRemoved: { addListener(fn) { callbacks.windowRemoved = fn; } },
    },
    alarms: {
      create: async (name, info) => { alarms.set(name, { ...info }); },
      clear: async name => alarms.delete(name),
      onAlarm: { addListener(fn) { callbacks.alarm = fn; } },
    },
    runtime: { onMessage: { addListener(fn) { callbacks.message = fn; } } },
  };
  const ctx = {
    chrome, URL, Date: FakeDate, setTimeout: (fn, delay) => {
      const id = nextTimerId++;
      timers.set(id, { fn, delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
    Math, Number, Promise, console,
    ensureContent: async () => true,
    trySendSocket: async event => { diagnostics.push(event); return true; },
    sendExtensionStatus: async () => true,
    resolveTargetTab: async () => ({ id: 101, windowId: 100, url: "https://chatgpt.com/" }),
    config: async () => ({ boundTabId: null }),
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(routeSource, ctx);
  await wait(10);

  const router = ctx.__CHAT2API_CONVERSATION_ROUTING_V1__;
  const policy1 = router.routePolicyForRequest({
    type: "chat.request", request_id: "req_new", routing: { api_key_id: "key_A" },
  });
  const policy2 = router.routePolicyForRequest({
    type: "chat.request", request_id: "req_new2", routing: { api_key_id: "key_A" },
  });
  const livePolicy = router.routePolicyForRequest({
    type: "voice.live.start", request_id: "live_1", routing: { api_key_id: "key_A" },
  });
  assert.notEqual(policy1.key, policy2.key, "different API requests must receive distinct route keys");
  assert.equal(policy1.request_scoped, true);
  assert.equal(policy1.close_delay_ms, 5000);
  assert.equal(livePolicy.lifecycle_mode, "continuous-voice-live");
  assert.equal(livePolicy.continuous, true);
  assert.notEqual(livePolicy.key, policy1.key);

  if (!stale) {
    callbacks.message({ type: "chat2api.event", event: { type: "chat.completed", request_id: "req_previous", text: "ok" } },
      { tab: { id: 101, url: "https://chatgpt.com/c/abc" } });
    const priorSettlement = router.terminalSettlements.get("key_A");
    if (priorSettlement) await priorSettlement;
  }

  const msg = {
    type: "chat.request", request_id: "req_new",
    routing: { api_key_id: "key_A", scheduler_authority: "server-single-authority-scheduler-v58", strict_api_fifo: strict },
    prompt: "hello",
  };
  const tab = await ctx.resolveTargetTabForRequest(msg);
  assert.ok(tab?.id);
  const requestRouteKey = "key_A::request::req_new";
  assert.equal(data.chat2apiConversationRoutesV1[requestRouteKey].inflight_request_id, "req_new");
  assert.equal(data.chat2apiConversationRoutesV1[requestRouteKey].request_scoped, true);
  assert.equal(data.chat2apiConversationRoutesV1[requestRouteKey].logical_api_key_id, "key_A");
  assert.equal(data.chat2apiConversationRoutesV1[requestRouteKey].close_delay_ms, 5000);
  assert.equal(removedWindows, 0, "route retirement must retain the window for the 5-second post-terminal inspection period");
  if (stale) {
    assert.equal(data.chat2apiConversationRoutesV1.key_A.inflight_request_id, null, "stale legacy lease must be detached");
    assert.ok(data.chat2apiConversationRoutesV1.key_A.close_after > now, "stale legacy window should have a deferred close");
  }

  const secondTab = await ctx.resolveTargetTabForRequest({
    ...msg, request_id: "req_new2", prompt: "second independent request",
  });
  assert.notEqual(secondTab.windowId, tab.windowId, "each independent request must use a different physical ChatGPT window");
  assert.equal(createdWindows, 2);

  callbacks.message({ type: "chat2api.event", event: { type: "chat.completed", request_id: "req_new", text: "done" } },
    { tab: { id: tab.id, url: "https://chatgpt.com/c/new-chat" } });
  const routeKey = router.activeRequests.get("req_new")?.key || requestRouteKey;
  const settlement = router.terminalSettlements.get(routeKey);
  if (settlement) await settlement;
  const finishedRoute = data.chat2apiConversationRoutesV1[requestRouteKey];
  assert.equal(finishedRoute.inflight_request_id, null);
  assert.equal(finishedRoute.close_after - now, 5000, "completed request window must close 5 seconds after terminal");
  const timerId = router.closeTimers.get(tab.windowId);
  assert.ok(timers.has(timerId), "5-second close timer should be armed");
  now = finishedRoute.close_after;
  const timer = timers.get(timerId);
  timers.delete(timerId);
  timer.fn();
  await wait(10);
  assert.equal(removedWindows, 1, "completed request window should close when its 5-second timer fires");
  assert.equal(data.chat2apiConversationRoutesV1[requestRouteKey], undefined, "closed request-scoped route should be removed");

  const liveTab = await ctx.resolveTargetTabForRequest({
    type: "voice.live.start", request_id: "live_1",
    routing: { api_key_id: "key_A" }, session_id: "session_live_1",
  });
  const liveKey = "key_A::voice-live::live_1";
  const liveRoute = data.chat2apiConversationRoutesV1[liveKey];
  assert.equal(liveRoute.window_id, liveTab.windowId);
  assert.equal(liveRoute.inflight_request_id, "live_1");
  assert.equal(liveRoute.close_after, null, "continuous Voice must not close while the session is active");
  assert.equal(await router.finishLiveSession("live_1"), true);
  assert.equal(liveRoute.inflight_request_id, null);
  assert.equal(liveRoute.close_after - now, 5000, "Voice window closes 5 seconds after explicit session stop");
}

await routeScenario();
await routeScenario({ stale: true });
await routeScenario({ stale: true, strict: false });

async function workerLogScenario(platform, session, local) {
  const handlers = {};
  const chrome = {
    storage: {
      local: {
        get: async keys => Object.fromEntries((Array.isArray(keys) ? keys : [keys]).map(k => [k, local[k]])),
        set: async obj => Object.assign(local, obj),
        remove: async keys => { for (const k of Array.isArray(keys) ? keys : [keys]) delete local[k]; },
      },
      session: {
        get: async key => ({ [key]: session[key] }),
        set: async obj => Object.assign(session, obj),
      },
    },
    runtime: {
      getPlatformInfo: async () => ({ os: platform }),
      getManifest: () => ({ version: "0.22.130" }),
      onMessage: { addListener(fn) { handlers.msg = fn; } },
    },
    alarms: { clear: async () => {}, create: async () => {}, onAlarm: { addListener(fn) { handlers.alarm = fn; } } },
  };
  const ctx = { chrome, TextEncoder, Date, Math, Number, Promise, console, setTimeout, clearTimeout, crypto: globalThis.crypto };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(logSource, ctx);
  await wait(50);
}
const seed = () => ({
  chat2apiRuntimeLogV1: [{ message: "legacy" }],
  chat2apiRuntimeActiveRunsV4: [],
  chat2apiRuntimeRunIndexV4: [],
  chat2apiRuntimeChunkV2: "legacy-chunk",
  pairingId: "KEEP_PAIRING",
  chatgptLoginProfile: "KEEP_LOGIN",
});
const windowsLogs = seed(), session = {};
await workerLogScenario("win", session, windowsLogs);
assert.equal(windowsLogs.chat2apiRuntimeLogV1.length, 0);
assert.equal(windowsLogs.pairingId, "KEEP_PAIRING");
assert.equal(windowsLogs.chatgptLoginProfile, "KEEP_LOGIN");
windowsLogs.chat2apiRuntimeLogV1 = [{ message: "fresh" }];
await workerLogScenario("win", session, windowsLogs);
assert.equal(windowsLogs.chat2apiRuntimeLogV1.length, 1, "MV3 background wake must retain current logs");
await workerLogScenario("win", {}, windowsLogs);
assert.equal(windowsLogs.chat2apiRuntimeLogV1.length, 0, "extension reload clears old logs");
const linuxLogs = seed();
await workerLogScenario("linux", {}, linuxLogs);
assert.equal(linuxLogs.chat2apiRuntimeLogV1.length, 1, "Linux Worker logs should be retained");
console.log("v190 request-scoped windows, 5s terminal cleanup, continuous Voice lease, and v130 logging regressions passed");
