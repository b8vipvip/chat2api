import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/conversation_dispatch.js", import.meta.url), "utf8");
const events = [];
const route = { inflight_request_id: "req_old_12345678", tab_id: 42, window_id: 41 };
let releaseCancel;
let deferCancel = true;
const cancelGate = new Promise(resolve => { releaseCancel = resolve; });
const router = {
  routes: { key_demo: route },
  activeRequests: new Map([["req_old_12345678", { key: "key_demo" }]]),
  async failRequest(requestId, reason) {
    events.push(["retire", requestId, reason]);
    if (route.inflight_request_id !== requestId) return false;
    route.inflight_request_id = null;
    route.tab_id = null;
    route.window_id = null;
    this.activeRequests.delete(requestId);
    return true;
  },
};
let tabExists = true;
const ctx = {
  console, Date, Map, Set, Promise,
  chrome: {
    runtime: { onMessage: { addListener() {} } },
    tabs: { async get(id) {
      if (id === 42 && tabExists) return { id: 42, windowId: 41 };
      if (id === 99) return { id: 99, windowId: 98 };
      throw new Error("No tab with id: " + id);
    } },
    storage: { local: { async set() {} } },
  },
  async ensureContent() {},
  async trySendSocket(event) { events.push(["socket", event.type]); return true; },
  async resolveTargetTab() { events.push(["UNSAFE_UNBOUND_FALLBACK"]); return { id: 99, windowId: 98 }; },
  async resolveTargetTabForRequest(message) {
    const id = route.inflight_request_id ? 42 : 99;
    events.push(["allocate", message.request_id, id]);
    route.inflight_request_id = message.request_id;
    route.tab_id = id;
    route.window_id = id === 99 ? 98 : 41;
    router.activeRequests.set(message.request_id, { key: "key_demo" });
    return { id, windowId: route.window_id };
  },
  async handleServerMessage(message) {
    events.push(["handle", message.type, message.request_id]);
    if (message.type === "chat.cancel" && deferCancel) await cancelGate;
    return { ok: true };
  },
  __CHAT2API_CONVERSATION_ROUTING_V1__: router,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(source, ctx, { filename: "conversation_dispatch.js" });
const dispatcher = ctx.__CHAT2API_CONVERSATION_DISPATCH_V1__;
dispatcher.requestTabs.set("req_old_12345678", { tabId: 42, windowId: 41 });

const cancel = ctx.handleServerMessage({ type: "chat.cancel", request_id: "req_old_12345678" });
const incoming = ctx.handleServerMessage({
  type: "chat.request", request_id: "req_new_12345678",
  routing: { api_key_id: "key_demo" },
});
await new Promise(resolve => setImmediate(resolve));
assert.equal(events.some(e => e[0] === "allocate"), false, "replacement must wait for cancel transport");
releaseCancel();
await cancel;
await incoming;
const retired = events.findIndex(e => e[0] === "retire" && e[1] === "req_old_12345678");
const allocated = events.findIndex(e => e[0] === "allocate" && e[1] === "req_new_12345678");
assert.ok(retired >= 0 && allocated > retired);
assert.deepEqual(events[allocated], ["allocate", "req_new_12345678", 99]);
assert.equal(dispatcher.lastCancelHandoff.target_tab_id, 42);
assert.equal(dispatcher.lastCancelHandoff.retired_by_router, true);
assert.ok(!events.some(e => e[0] === "UNSAFE_UNBOUND_FALLBACK"));

deferCancel = false;
tabExists = false;
const oldHandlerCount = events.filter(e => e[0] === "handle" && e[1] === "chat.cancel").length;
// If cancellation arrives after the old request lost its mapping, it must
// never be sent to the currently bound new request's window.
await ctx.handleServerMessage({ type: "chat.cancel", request_id: "req_missing_12345678" });
assert.equal(events.filter(e => e[0] === "handle" && e[1] === "chat.cancel").length, oldHandlerCount);
assert.equal(route.inflight_request_id, "req_new_12345678");
assert.equal(dispatcher.lastCancelHandoff.target_found, false);

dispatcher.requestTabs.set("req_stale_12345678", { tabId: 42, windowId: 41 });
await ctx.handleServerMessage({ type: "chat.cancel", request_id: "req_stale_12345678" });
assert.equal(route.inflight_request_id, "req_new_12345678");
assert.equal(dispatcher.lastCancelHandoff.retired_by_router, false);
assert.ok(!events.some(e => e[0] === "UNSAFE_UNBOUND_FALLBACK"));
console.log("request-bound cancellation handoff and window isolation passed");
