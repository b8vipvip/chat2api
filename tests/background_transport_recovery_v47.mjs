import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/background_transport_recovery_v47.js", "utf8");
const storage = {};
let failStorageWrites = false;
const sent = [];
const handled = [];
const timers = new Map();
let nextTimer = 0;
const context = {
  trySendSocket: async payload => {
    sent.push(payload);
    return true;
  },
  socketReady: () => true,
  handleServerMessage: async message => handled.push(message),
  chrome: {
    storage: {
      local: {
        get: async defaults => ({...defaults, ...storage}),
        set: async value => {
          if (failStorageWrites) throw new Error("storage quota unavailable");
          Object.assign(storage, value);
        },
      },
    },
  },
  setTimeout: callback => {
    const id = ++nextTimer;
    timers.set(id, callback);
    return id;
  },
  clearTimeout: id => timers.delete(id),
  setInterval: () => 1,
};
vm.runInNewContext(source, context);

async function drainTimers() {
  for (let round = 0; round < 8 && timers.size; round += 1) {
    const callbacks = [...timers.values()];
    timers.clear();
    for (const callback of callbacks) await callback();
  }
}

// Let the asynchronous chrome.storage restore complete.
await new Promise(resolve => setImmediate(resolve));
await drainTimers();

const terminal = {
  type: "chat.completed",
  request_id: "req_durable_terminal_123",
  text: "final Worker response",
};
assert.equal(await context.trySendSocket(terminal), true);
assert.equal(context.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__.outbox.size, 1);
await drainTimers();

assert.equal(sent.length, 1);
assert.equal(sent[0].diagnostics.worker_transport_replayed, true);
assert.equal(sent[0].diagnostics.worker_transport_recovery, "transport-outbox-v47");
assert.equal(context.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__.outbox.size, 1,
  "successful WebSocket.send is not a server acknowledgement");
assert.equal(storage.chat2apiWorkerTransportOutboxV47.length, 1);

await context.handleServerMessage({
  type: "chat.terminal.ack",
  request_id: "req_durable_terminal_123",
  terminal_type: "chat.completed",
  accepted: false,
});
assert.equal(context.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__.outbox.size, 1,
  "negative acknowledgement must retain the terminal outbox");
assert.equal(storage.chat2apiWorkerTransportOutboxV47.length, 1);

// A fresh service-worker context restores the unacknowledged terminal and
// replays it again; only a positive server ACK clears persistent storage.
const restoredSent = [];
const restoredTimers = new Map();
let restoredTimerId = 0;
const lastSentAt = Number(storage.chat2apiWorkerTransportOutboxV47[0]?.last_sent_at_ms || Date.now());
const restoredNow = lastSentAt + 3000;
const RestoredDate = class extends Date {
  static now() { return restoredNow; }
};
const restoredContext = {
  Date: RestoredDate,
  trySendSocket: async payload => { restoredSent.push(payload); return true; },
  socketReady: () => true,
  handleServerMessage: async () => {},
  chrome: {
    storage: {
      local: {
        get: async defaults => ({...defaults, ...storage}),
        set: async value => Object.assign(storage, value),
      },
    },
  },
  setTimeout: callback => {
    const id = ++restoredTimerId;
    restoredTimers.set(id, callback);
    return id;
  },
  clearTimeout: id => restoredTimers.delete(id),
  setInterval: () => 1,
};
vm.runInNewContext(source, restoredContext);
await new Promise(resolve => setImmediate(resolve));
for (let round = 0; round < 8 && restoredTimers.size; round += 1) {
  const callbacks = [...restoredTimers.values()];
  restoredTimers.clear();
  for (const callback of callbacks) await callback();
}
assert.equal(restoredContext.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__.outbox.size, 1);
assert.equal(restoredSent.length, 1);
assert.equal(restoredSent[0].request_id, "req_durable_terminal_123");
await restoredContext.handleServerMessage({
  type: "chat.terminal.ack",
  request_id: "req_durable_terminal_123",
  terminal_type: "chat.completed",
  accepted: true,
});
assert.equal(storage.chat2apiWorkerTransportOutboxV47.length, 0);

await context.handleServerMessage({
  type: "chat.terminal.ack",
  request_id: "req_durable_terminal_123",
  terminal_type: "chat.completed",
  accepted: true,
});
assert.equal(context.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__.outbox.size, 0);
assert.equal(storage.chat2apiWorkerTransportOutboxV47.length, 0);

await context.handleServerMessage({type: "heartbeat.ack"});
assert.equal(handled.length, 1);
assert.equal(handled[0].type, "heartbeat.ack");

await context.trySendSocket({
  type: "chat.delta", request_id: "req_durable_terminal_123", delta: "nonterminal",
});
assert.equal(sent.length, 2, "non-terminal events keep the original best-effort transport");
assert.equal(sent[1].type, "chat.delta");
assert.equal(context.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__.outbox.size, 0);

failStorageWrites = true;
const rejected = await context.trySendSocket({
  type: "chat.error", request_id: "req_storage_failure_123", error: "storage failed",
});
assert.equal(rejected, false, "the Worker must not claim durability when storage rejects the write");
assert.equal(context.__CHAT2API_BACKGROUND_TRANSPORT_RECOVERY_V47__.outbox.size, 0);
assert.equal(sent.length, 2, "an unpersisted terminal must never be sent");

console.log("Worker terminal outbox waits for server acknowledgement and durable storage");
