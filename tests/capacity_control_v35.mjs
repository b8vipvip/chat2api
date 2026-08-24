import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../chrome_extension/background_capacity_control_v35.js', import.meta.url), 'utf8');
const forwarded = [];
const sent = [];
const calls = [];

let total = 1;
let active = 0;
let authoritativeTarget = 1;

const reserve = {
  target: 1,
  refreshConfig: async force => {
    calls.push(['refreshConfig', force]);
    reserve.target = authoritativeTarget;
    return authoritativeTarget;
  },
  reconcile: async () => {
    calls.push(['reserve.reconcile']);
    if (total < reserve.target) total += 1;
    else if (total > reserve.target && active <= reserve.target) total -= 1;
    return {};
  },
  report: async force => calls.push(['reserve.report', force]),
  snapshot: async () => ({
    total,
    active,
    target: reserve.target,
    own: Math.max(0, total - active),
    warm: 0,
    routed: active,
    live: new Set(Array.from({length: total + 1}, (_, index) => index + 1)),
  }),
};

const supervisor = {
  reconcile: async () => {
    calls.push(['supervisor.reconcile']);
    if (total > reserve.target && active <= reserve.target) total = reserve.target;
    return {};
  },
};

globalThis.__CHAT2API_RESERVE_POOL_V29__ = reserve;
globalThis.__CHAT2API_TAB_SUPERVISOR_V32__ = supervisor;
globalThis.__CHAT2API_NATIVE_CAPACITY_CONTROL_VERSION__ = 36;
globalThis.__CHAT2API_NATIVE_CAPACITY_DISPATCH_V37__ = true;
globalThis.handleServerMessage = async message => forwarded.push(message);
globalThis.trySendSocket = async payload => {
  sent.push(payload);
  return true;
};

// Native background.js must be able to consume v35 even if the historical
// global handleServerMessage overlay is unavailable in an MV3 worker binding.
assert.ok(
  source.indexOf('state.handle = handleControl;') < source.indexOf('const baseHandler = globalThis.handleServerMessage;'),
  'controller API must be published before optional legacy handler wrapping',
);

vm.runInThisContext(source, { filename: 'background_capacity_control_v35.js' });
assert.ok(globalThis.__CHAT2API_CAPACITY_CONTROL_V35__, 'capacity control should install');
assert.equal(typeof globalThis.__CHAT2API_CAPACITY_CONTROL_V35__.handle, 'function');
assert.equal(typeof globalThis.__CHAT2API_CAPACITY_CONTROL_V35__.snapshot, 'function');

await globalThis.handleServerMessage({ type: 'chat.request', request_id: 'req_passthrough' });
assert.equal(forwarded.length, 1);
assert.equal(forwarded[0].request_id, 'req_passthrough');

await globalThis.handleServerMessage({
  type: 'extension.control',
  control_id: 'ctl_snapshot',
  action: 'windows.snapshot',
  payload: {},
});
let result = sent.at(-1);
assert.equal(result.type, 'extension.control.result');
assert.equal(result.control_id, 'ctl_snapshot');
assert.equal(result.ok, true);
assert.equal(result.metadata.extension_control_version, 36);
assert.equal(result.metadata.extension_control_ready, true);
assert.equal(result.metadata.extension_control_transport, 'capacity-result-v35-via-native-v37');
assert.equal(result.metadata.extension_control_capability_reporter, 37);
assert.equal(result.metadata.extension_control_result.control_id, 'ctl_snapshot');
assert.equal(result.metadata.reserve_window_total, 1);
assert.equal(result.metadata.reserve_window_active, 0);
assert.equal(result.data.window_snapshot.all_chatgpt_windows, 2);

sent.length = 0;
calls.length = 0;
authoritativeTarget = 3;
reserve.target = 1;
total = 1;
active = 0;
await globalThis.handleServerMessage({
  type: 'extension.control',
  control_id: 'ctl_grow',
  action: 'workers.resize',
  payload: { target: 3 },
});
result = sent.at(-1);
assert.equal(result.ok, true);
assert.equal(result.data.target_reached, true);
assert.equal(result.data.window_snapshot.total, 3);
assert.equal(result.data.window_snapshot.target, 3);
assert.ok(calls.some(row => row[0] === 'refreshConfig' && row[1] === true));
assert.ok(calls.some(row => row[0] === 'reserve.reconcile'));
assert.ok(calls.some(row => row[0] === 'supervisor.reconcile'));

sent.length = 0;
calls.length = 0;
authoritativeTarget = 2;
reserve.target = 3;
total = 4;
active = 4;
await globalThis.handleServerMessage({
  type: 'extension.control',
  control_id: 'ctl_shrink_busy',
  action: 'workers.resize',
  payload: { target: 2 },
});
result = sent.at(-1);
assert.equal(result.ok, true);
assert.equal(result.data.target_reached, false);
assert.equal(result.data.pending_reason, 'active-windows-protected');
assert.equal(result.data.window_snapshot.total, 4);
assert.equal(result.data.window_snapshot.active, 4);
assert.equal(result.data.window_snapshot.target, 2);

sent.length = 0;
authoritativeTarget = 5;
reserve.target = 5;
await globalThis.handleServerMessage({
  type: 'extension.control',
  control_id: 'ctl_mismatch',
  action: 'workers.resize',
  payload: { target: 4 },
});
result = sent.at(-1);
assert.equal(result.ok, false);
assert.match(result.error, /runtime target mismatch/i);
assert.equal(result.metadata.extension_control_result.control_id, 'ctl_mismatch');

console.log('capacity_control_v35 VM contract passed');
