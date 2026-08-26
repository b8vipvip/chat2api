import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/background_request_recovery_v40.js", import.meta.url), "utf8");

function harness() {
  const listeners = [];
  const removedWindows = [];
  const clearedAlarms = [];
  const storageWrites = [];
  const socketEvents = [];
  let timerId = 0;
  const cancelledTimers = new Set();

  const route = {
    api_key_id: "key_a",
    conversation_id: "conv_a",
    conversation_url: "https://chatgpt.com/c/conv_a",
    generation: 4,
    turn_count: 2,
    text_chars: 120,
    attachment_count: 0,
    slow_load_strikes: 0,
    last_open_ms: 20,
    tab_id: 11,
    window_id: 22,
    window_owned: true,
    inflight_request_id: "req_bad",
    last_active_at: Date.now(),
    close_after: null,
  };

  const router = {
    routes: { key_a: route },
    activeRequests: new Map([["req_bad", { key: "key_a" }]]),
  };
  const dispatch = { requestTabs: new Map([["req_bad", { tabId: 11, windowId: 22 }]]) };

  const context = {
    console,
    Date,
    Promise,
    Map,
    Set,
    Object,
    Number,
    String,
    Boolean,
    globalThis: null,
    handleServerMessage: async () => ({ ok: true }),
    trySendSocket: async event => { socketEvents.push(event); },
    setTimeout: fn => {
      const id = ++timerId;
      Promise.resolve().then(() => { if (!cancelledTimers.has(id)) return fn(); });
      return id;
    },
    clearTimeout: id => { cancelledTimers.add(id); },
    chrome: {
      runtime: { onMessage: { addListener: fn => listeners.push(fn) } },
      storage: { local: { set: async value => { storageWrites.push(value); } } },
      alarms: { clear: async name => { clearedAlarms.push(name); return true; } },
      windows: { remove: async id => { removedWindows.push(id); } },
    },
    __CHAT2API_CONVERSATION_ROUTING_V1__: router,
    __CHAT2API_CONVERSATION_DISPATCH_V1__: dispatch,
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "background_request_recovery_v40.js" });
  return { context, listeners, route, router, dispatch, removedWindows, clearedAlarms, storageWrites, socketEvents };
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
}

{
  const h = harness();
  assert.equal(h.listeners.length, 1);
  h.listeners[0]({ type: "chat2api.event", event: { type: "chat.error", request_id: "req_bad", error: "stalled" } });
  await flush();

  assert.deepEqual(h.removedWindows, [22]);
  assert.equal(h.route.tab_id, null);
  assert.equal(h.route.window_id, null);
  assert.equal(h.route.inflight_request_id, null);
  assert.equal(h.route.conversation_id, null);
  assert.equal(h.route.turn_count, 0);
  assert.equal(h.route.generation, 5);
  assert.equal(h.route.last_rotation_reason, "chat.error-recycle");
  assert.equal(h.router.activeRequests.has("req_bad"), false);
  assert.equal(h.dispatch.requestTabs.has("req_bad"), false);
  assert.ok(h.clearedAlarms.includes("chat2api-route-close:22"));
  assert.ok(h.storageWrites.some(value => value.chat2apiRequestRecoveryV40?.request_id === "req_bad"));
}

{
  const h = harness();
  h.listeners[0]({ type: "chat2api.event", event: { type: "chat.completed", request_id: "req_bad", text: "ok" } });
  await flush();
  assert.deepEqual(h.removedWindows, []);
  assert.equal(h.route.window_id, 22);
}

{
  const h = harness();
  await h.context.handleServerMessage({ type: "chat.cancel", request_id: "req_bad" });
  await flush();
  assert.deepEqual(h.removedWindows, [22]);
  assert.ok(h.socketEvents.some(event => event.type === "chat.cancelled" && event.request_id === "req_bad"));
}

console.log("request recovery v40 tests passed");
