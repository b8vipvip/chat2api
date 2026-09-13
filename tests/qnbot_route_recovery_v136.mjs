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
  async failRequest(requestId, reason) {
    events.push(["fail", requestId, reason]);
    for (const route of Object.values(this.routes)) {
      if (route.inflight_request_id === requestId) {
        route.inflight_request_id = null;
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
const tab = await context.resolveTargetTabForRequest(authorized);
assert.equal(tab.id, 7);
assert.equal(routes.key_bot2.inflight_request_id, null);
assert.deepEqual(events[0], ["fail", "req_stale_owner_12345678", "server-authority-stale-inflight-v136"]);
assert.deepEqual(events[1], ["resolve", "req_replacement_12345678"]);
assert.equal(context.__CHAT2API_CONVERSATION_ROUTE_RECOVERY_V136__.stale_inflight_recoveries, 1);

routes.key_bot2.inflight_request_id = "req_non_authoritative_12345678";
await assert.rejects(
  context.resolveTargetTabForRequest({
    type: "chat.request",
    request_id: "req_other_12345678",
    routing: { api_key_id: "key_bot2" },
  }),
  /Server scheduler invariant violated/,
);
assert.equal(routes.key_bot2.inflight_request_id, "req_non_authoritative_12345678");

routes.key_bot2.inflight_request_id = "req_cancel_12345678";
const cancelResult = await context.handleServerMessage({ type: "chat.cancel", request_id: "req_cancel_12345678" });
assert.deepEqual(cancelResult, { ok: true });
const handleIndex = events.findIndex(row => row[0] === "handle" && row[1] === "chat.cancel");
const cancelRetireIndex = events.findIndex(row => row[0] === "fail" && row[1] === "req_cancel_12345678");
assert.ok(handleIndex >= 0 && cancelRetireIndex > handleIndex, "cancel must reach the transport before route retirement");
assert.equal(routes.key_bot2.inflight_request_id, null);
assert.equal(context.__CHAT2API_CONVERSATION_ROUTE_RECOVERY_V136__.explicit_cancel_retirements, 1);

console.log("qnbot route recovery v136 VM contract passed");
