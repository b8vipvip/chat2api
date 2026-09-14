import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/conversation_route_recovery_v136.js", import.meta.url), "utf8");
const events = [];
const routes = {
  key_bot2: {
    api_key_id: "key_bot2",
    inflight_request_id: "req_stale_owner_12345678",
    window_id: 9,
    tab_id: 7,
  },
};
const router = {
  loaded: true,
  routes,
  activeRequests: new Map(),
  retiredRequests: new Set(),
  async failRequest(requestId, reason) {
    events.push(["fail", requestId, reason]);
    if (this.retiredRequests.has(requestId)) return false;
    this.retiredRequests.add(requestId);
    for (const route of Object.values(this.routes)) {
      if (route.inflight_request_id === requestId) {
        route.inflight_request_id = null;
        this.activeRequests.delete(requestId);
        return true;
      }
    }
    return false;
  },
};

const context = {
  console,
  Date,
  Map,
  Set,
  URL,
  Promise,
  setTimeout,
  clearTimeout,
  chrome: {
    storage: {
      local: {
        async get() { return { chat2apiConversationRoutesV1: routes }; },
      },
    },
  },
  async resolveTargetTabForRequest(message) {
    const route = routes[message?.routing?.api_key_id];
    if (route?.inflight_request_id && route.inflight_request_id !== message.request_id) {
      throw new Error(`Server scheduler invariant violated: logical API ${message.routing.api_key_id} already owns request ${route.inflight_request_id}`);
    }
    events.push(["resolve", message.request_id]);
    return { id: 7, windowId: 9 };
  },
  async handleServerMessage(message) {
    events.push(["handle", message.type, message.request_id || ""]);
    return { ok: true };
  },
  __CHAT2API_CONVERSATION_ROUTING_V1__: router,
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: "conversation_route_recovery_v136.js" });

const recovery = context.__CHAT2API_CONVERSATION_ROUTE_RECOVERY_V136__;
assert.equal(recovery.revision, 140);

// v58-authoritative replacement retires an orphaned persisted owner.
const authorized = {
  type: "chat.request",
  request_id: "req_replacement_12345678",
  routing: {
    api_key_id: "key_bot2",
    logical_api_key_id: "key_bot2",
    scheduler_authority: "server-single-authority-scheduler-v58",
    server_api_fifo_revision: 58,
  },
};
let tab = await context.resolveTargetTabForRequest(authorized);
assert.equal(tab.id, 7);
assert.equal(routes.key_bot2.inflight_request_id, null);
assert.deepEqual(events[0], ["fail", "req_stale_owner_12345678", "server-authority-stale-inflight-v136"]);
assert.deepEqual(events[1], ["resolve", "req_replacement_12345678"]);
assert.equal(recovery.stale_inflight_recoveries, 1);
assert.equal(recovery.server_authority_recoveries, 1);

// Regression: deployed messages can omit the v58 metadata. A persisted owner
// that is absent from the current process' activeRequests map is still orphaned
// and must not permanently lock the logical API key.
routes.key_bot2.inflight_request_id = "req_orphan_without_metadata_12345678";
tab = await context.resolveTargetTabForRequest({
  type: "chat.request",
  request_id: "req_other_12345678",
  routing: { api_key_id: "key_bot2" },
});
assert.equal(tab.id, 7);
assert.equal(routes.key_bot2.inflight_request_id, null);
assert.ok(events.some(row => row[0] === "fail" && row[1] === "req_orphan_without_metadata_12345678" && row[2] === "orphaned-persisted-inflight-v140"));
assert.equal(recovery.orphan_owner_recoveries, 1);

// A truly live owner in this service-worker process must still reject a second
// request. The recovery is not a concurrency bypass.
routes.key_bot2.inflight_request_id = "req_live_owner_12345678";
router.activeRequests.set("req_live_owner_12345678", { key: "key_bot2" });
await assert.rejects(
  context.resolveTargetTabForRequest({
    type: "chat.request",
    request_id: "req_parallel_12345678",
    routing: { api_key_id: "key_bot2" },
  }),
  /Server scheduler invariant violated/,
);
assert.equal(routes.key_bot2.inflight_request_id, "req_live_owner_12345678");
router.activeRequests.delete("req_live_owner_12345678");

// A stale de-dup marker from a late terminal callback must not make the owner
// immortal. Recovery removes only the exact orphan marker before retrying.
routes.key_bot2.inflight_request_id = "req_retired_but_stuck_12345678";
router.retiredRequests.add("req_retired_but_stuck_12345678");
tab = await context.resolveTargetTabForRequest({
  type: "chat.request",
  request_id: "req_after_retired_marker_12345678",
  routing: { api_key_id: "key_bot2" },
});
assert.equal(tab.id, 7);
assert.equal(routes.key_bot2.inflight_request_id, null);
assert.ok(events.some(row => row[0] === "fail" && row[1] === "req_retired_but_stuck_12345678"));

// Explicit server cancellation still reaches transport first, then performs
// exact-owner retirement.
routes.key_bot2.inflight_request_id = "req_cancel_12345678";
router.retiredRequests.delete("req_cancel_12345678");
const cancelResult = await context.handleServerMessage({ type: "chat.cancel", request_id: "req_cancel_12345678" });
assert.deepEqual(cancelResult, { ok: true });
const handleIndex = events.findIndex(row => row[0] === "handle" && row[1] === "chat.cancel");
const cancelRetireIndex = events.findIndex(row => row[0] === "fail" && row[1] === "req_cancel_12345678");
assert.ok(handleIndex >= 0 && cancelRetireIndex > handleIndex, "cancel must reach the transport before route retirement");
assert.equal(routes.key_bot2.inflight_request_id, null);
assert.equal(recovery.explicit_cancel_retirements, 1);

console.log("qnbot route recovery v140 VM contract passed");