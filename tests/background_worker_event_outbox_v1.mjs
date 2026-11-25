import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

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
assert.equal(handled.length,1);
assert.equal(handled[0].type,"heartbeat.ack");

console.log("Worker nonterminal events persist, replay in request order, and gate only their own terminal");
