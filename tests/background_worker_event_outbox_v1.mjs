import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {randomUUID} from "node:crypto";

const terminalSource = readFileSync("chrome_extension/background_transport_recovery_v47.js", "utf8");
const eventSource = readFileSync("chrome_extension/background_worker_event_outbox_v1.js", "utf8");
const storage = {};
const sent = [];
const handled = [];
const timers = new Map();
let timerId = 0;
const context = {
  trySendSocket: async payload => { sent.push(payload); return true; },
  socketReady: () => true,
  handleServerMessage: async message => handled.push(message),
  chrome: {
    storage: {
      local: {
        get: async defaults => ({...defaults, ...storage}),
        set: async value => Object.assign(storage, value),
      },
    },
  },
  TextEncoder,
  setTimeout: callback => {
    const id = ++timerId;
    timers.set(id, callback);
    return id;
  },
  clearTimeout: id => timers.delete(id),
  setInterval: () => 1,
};
vm.runInNewContext(terminalSource, context);
vm.runInNewContext(eventSource, context);

async function drainTimers() {
  for (let round = 0; round < 12 && timers.size; round += 1) {
    const callbacks = [...timers.values()];
    timers.clear();
    for (const callback of callbacks) await callback();
  }
}

await new Promise(resolve => setImmediate(resolve));
await drainTimers();
await context.handleServerMessage({
  type:"server.hello",
  capabilities:["worker_event_ack_v1"],
});

const requestA = "req_worker_events_a";
const requestB = "req_worker_events_b";
const deltaA = {
  type:"chat.delta",
  request_id:requestA,
  worker_event_id:requestA + ":1",
  delta:"first ",
};
assert.equal(await context.trySendSocket(deltaA),true,
  "the content script receives success after the event is durably queued");
assert.equal(await context.trySendSocket({
  type:"chat.delta",request_id:requestA,worker_event_id:requestA + ":2",delta:"second",
}),true);
assert.equal(context.__CHAT2API_BACKGROUND_WORKER_EVENT_OUTBOX_V1__.events.size,2);
assert.equal(storage.chat2apiWorkerEventOutboxV1.length,2);

assert.equal(await context.trySendSocket({
  type:"chat.completed",request_id:requestA,text:"first second",
}),true);
assert.equal(await context.trySendSocket({
  type:"chat.completed",request_id:requestB,text:"independent request",
}),true);
await drainTimers();

const firstA = sent.filter(item => item.request_id === requestA && item.type === "chat.delta");
assert.equal(firstA.length,1,"only the oldest event for a request may be in flight");
assert.equal(firstA[0].worker_event_id,requestA + ":1");
assert.equal(firstA[0].diagnostics.worker_event_transport,"durable-outbox-v1");
assert.equal(sent.some(item => item.request_id === requestA && item.type === "chat.completed"),false,
  "a request terminal must not overtake its unacknowledged deltas");
assert.equal(sent.some(item => item.request_id === requestB && item.type === "chat.completed"),true,
  "an unrelated request terminal must not be blocked by another request's outbox");

await context.handleServerMessage({
  type:"chat.event.ack",request_id:requestA,worker_event_id:requestA + ":1",
  event_id:1,accepted:true,durable:true,
});
await drainTimers();
const secondA = sent.filter(item => item.request_id === requestA && item.type === "chat.delta");
assert.equal(secondA.length,2);
assert.equal(secondA[1].worker_event_id,requestA + ":2");
assert.equal(context.__CHAT2API_BACKGROUND_WORKER_EVENT_OUTBOX_V1__.events.size,1);

await context.handleServerMessage({
  type:"chat.event.ack",request_id:requestA,worker_event_id:requestA + ":2",
  event_id:2,accepted:true,durable:true,
});
await drainTimers();
assert.equal(context.__CHAT2API_BACKGROUND_WORKER_EVENT_OUTBOX_V1__.events.size,0);
assert.equal(storage.chat2apiWorkerEventOutboxV1.length,0);
assert.equal(sent.some(item => item.request_id === requestA && item.type === "chat.completed"),true,
  "the terminal is released after every earlier Worker event has an ACK");

await context.handleServerMessage({
  type:"chat.terminal.ack",request_id:requestA,terminal_type:"chat.completed",accepted:true,
});
await context.handleServerMessage({
  type:"chat.terminal.ack",request_id:requestB,terminal_type:"chat.completed",accepted:true,
});
assert.equal(context.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__.outbox.size,0);

const toolResult = await context.trySendSocket({
  type:"chat.tool.result",
  request_id:"req_tool_outbox_123",
  call_id:"call-123",
  output:"done",
});
assert.equal(toolResult,true);
await drainTimers();
const toolEvent = sent.find(item => item.type === "chat.tool.result");
assert.equal(toolEvent.worker_event_id,"req_tool_outbox_123:tool-result:call-123",
  "native tool events without an explicit result ID receive the same stable call-scoped identity used by the server");
await context.handleServerMessage({
  type:"chat.event.ack",
  request_id:"req_tool_outbox_123",
  worker_event_id:"req_tool_outbox_123:tool-result:call-123",
  event_id:3,accepted:true,durable:false,
});
assert.equal(context.__CHAT2API_BACKGROUND_WORKER_EVENT_OUTBOX_V1__.events.size,0);

await context.handleServerMessage({type:"heartbeat.ack"});
assert.equal(handled.some(message => message.type === "server.hello"),true);
assert.equal(handled.filter(message => message.type === "heartbeat.ack").length,1);

const legacySent = [];
const legacyStorage = {};
const legacyContext = {
  trySendSocket: async payload => { legacySent.push(payload); return true; },
  socketReady: () => true,
  handleServerMessage: async () => {},
  TextEncoder,
  chrome: {
    storage: {
      local: {
        get: async defaults => ({...defaults, ...legacyStorage}),
        set: async value => Object.assign(legacyStorage, value),
      },
    },
  },
  setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id; },
  clearTimeout: id => timers.delete(id),
  setInterval: () => 1,
};
vm.runInNewContext(terminalSource, legacyContext);
vm.runInNewContext(eventSource, legacyContext);
await new Promise(resolve => setImmediate(resolve));
assert.equal(await legacyContext.trySendSocket({
  type:"chat.delta",request_id:"req_legacy_server",worker_event_id:"req_legacy_server:1",delta:"best effort",
}),true);
assert.equal(legacySent.length,1,
  "an older server without the negotiated ACK capability retains best-effort delivery");
assert.equal(legacyContext.__CHAT2API_BACKGROUND_WORKER_EVENT_OUTBOX_V1__.events.size,0);

console.log("Worker nonterminal events persist, replay in request order, and gate only their own terminal");


// Production request-v6 emits chat.delta/chat.snapshot without a source ID.
// Restore a pending pre-restart event while new ID-less events and the terminal
// arrive. A slow storage read must not reorder the old event after new events.
const restartedRequest = "req_restore_order_123";
const persistedId = restartedRequest + ":old";
const restartStorage = {
  chat2apiWorkerEventOutboxV1: [{
    payload: {type:"chat.delta",request_id:restartedRequest,worker_event_id:persistedId,delta:"old "},
    queued_at_ms:Date.now(),last_sent_at_ms:0,attempts:0,order:99,
  }],
  chat2apiWorkerTransportOutboxV47: [],
};
let finishRestore;
const restoreBarrier = new Promise(resolve => { finishRestore = resolve; });
const restartSent = [];
const restartTimers = new Map();
let restartTimerId = 0;
const restartedContext = {
  crypto:{randomUUID},
  TextEncoder,
  trySendSocket:async payload => { restartSent.push(payload); return true; },
  socketReady:() => true,
  handleServerMessage:async () => {},
  chrome:{storage:{local:{
    get:async defaults => {
      const snapshot = JSON.parse(JSON.stringify({...defaults,...restartStorage}));
      if (Object.hasOwn(defaults,"chat2apiWorkerEventOutboxV1")) await restoreBarrier;
      return snapshot;
    },
    set:async value => Object.assign(restartStorage,JSON.parse(JSON.stringify(value))),
  }}},
  setTimeout:callback => { const id = ++restartTimerId; restartTimers.set(id,callback); return id; },
  clearTimeout:id => restartTimers.delete(id),
  setInterval:() => 1,
};
vm.runInNewContext(terminalSource,restartedContext);
vm.runInNewContext(eventSource,restartedContext);
const hello = restartedContext.handleServerMessage({
  type:"server.hello",capabilities:["worker_event_ack_v1"],
});
const newDelta = {type:"chat.delta",request_id:restartedRequest,delta:"same "};
const queuedA = restartedContext.trySendSocket(newDelta);
const queuedB = restartedContext.trySendSocket(newDelta);
const queuedSnapshot = restartedContext.trySendSocket({
  type:"chat.snapshot",request_id:restartedRequest,text:"old same same ",
});
const queuedTerminal = restartedContext.trySendSocket({
  type:"chat.completed",request_id:restartedRequest,text:"old same same ",
});
await new Promise(resolve => setImmediate(resolve));
for (const callback of [...restartTimers.values()]) {
  // Flush can legitimately wait behind restore. Do not await it until the
  // deliberately blocked storage read has been released.
  void callback();
}
assert.equal(restartSent.length,0,"terminal cannot overtake an unrestored event queue");
finishRestore();
await hello;
assert.deepEqual(await Promise.all([queuedA,queuedB,queuedSnapshot,queuedTerminal]),[true,true,true,true]);
const restoredRows = restartStorage.chat2apiWorkerEventOutboxV1;
assert.equal(restoredRows.length,4);
assert.equal(restoredRows[0].payload.worker_event_id,persistedId);
assert.deepEqual(restoredRows.map(row=>row.order),[99,100,101,102]);
assert.equal(new Set(restoredRows.map(row=>row.payload.worker_event_id)).size,4,
  "equal ID-less deltas are separate events, not deduplication candidates");
assert.ok(restoredRows.slice(1).every(row=>/^evt:[a-f0-9-]{36}$/.test(row.payload.worker_event_id)));

async function drainRestartTimers() {
  for (let round=0;round<12 && restartTimers.size;round++) {
    const callbacks=[...restartTimers.values()];
    restartTimers.clear();
    for (const callback of callbacks) await callback();
  }
}
await drainRestartTimers();
assert.equal(restartSent.find(row=>row.type==="chat.delta")?.worker_event_id,persistedId);
for (const row of restoredRows) {
  await restartedContext.handleServerMessage({
    type:"chat.event.ack",request_id:restartedRequest,
    worker_event_id:row.payload.worker_event_id,accepted:true,durable:true,
  });
  await drainRestartTimers();
}
assert.equal(restartStorage.chat2apiWorkerEventOutboxV1.length,0);
assert.equal(restartSent.filter(row=>row.type==="chat.completed").length,1);
assert.deepEqual(restartSent.filter(row=>["chat.delta","chat.snapshot"].includes(row.type))
  .map(row=>row.worker_event_id),restoredRows.map(row=>row.payload.worker_event_id));
console.log("Production ID-less events and delayed restart restoration preserve durable order");
