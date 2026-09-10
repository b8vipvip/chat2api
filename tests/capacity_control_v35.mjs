import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../chrome_extension/background_capacity_control_v35.js', import.meta.url), 'utf8');
const forwarded = [];
const sent = [];
let activeRows = [
  { window_id: 101, tab_id: 201, status: 'ready' },
  { window_id: 102, tab_id: 202, status: 'in_use' },
];
let reports = 0;

const observer = {
  report: async () => { reports += 1; return true; },
  snapshot: () => ({ active: activeRows, decision_authority: false }),
};

globalThis.__CHAT2API_WINDOW_OBSERVER_V90__ = observer;
globalThis.__CHAT2API_NATIVE_CAPACITY_CONTROL_VERSION__ = 36;
globalThis.__CHAT2API_NATIVE_CAPACITY_DISPATCH_V37__ = true;
globalThis.handleServerMessage = async message => forwarded.push(message);
globalThis.trySendSocket = async payload => { sent.push(payload); return true; };

assert.ok(
  source.indexOf('state.handle = handleControl;') < source.indexOf('const baseHandler = globalThis.handleServerMessage;'),
  'controller API must be published before optional legacy handler wrapping',
);
vm.runInThisContext(source, { filename: 'background_capacity_control_v35.js' });
assert.ok(globalThis.__CHAT2API_CAPACITY_CONTROL_V35__);
assert.equal(typeof globalThis.__CHAT2API_CAPACITY_CONTROL_V35__.handle, 'function');
assert.equal(typeof globalThis.__CHAT2API_CAPACITY_CONTROL_V35__.snapshot, 'function');

await globalThis.handleServerMessage({ type: 'chat.request', request_id: 'req_passthrough' });
assert.equal(forwarded.length, 1);

await globalThis.handleServerMessage({
  type: 'extension.control', control_id: 'ctl_snapshot', action: 'windows.snapshot', payload: {},
});
let result = sent.at(-1);
assert.equal(result.type, 'extension.control.result');
assert.equal(result.ok, true);
assert.equal(result.data.window_snapshot.total, 2);
assert.equal(result.data.window_snapshot.active, 1);
assert.equal(result.data.window_snapshot.target, 0);
assert.equal(result.data.window_snapshot.speculative_windows, false);
assert.equal(result.data.window_snapshot.route_window_authority, 'conversation-routing-v30');
assert.equal(result.metadata.reserve_window_target, 0);
assert.equal(result.metadata.window_decision_authority, 'conversation-routing-v30');
assert.equal(result.metadata.speculative_windows, false);
assert.equal(result.metadata.extension_control_version, 36);

const reportsBeforeResize = reports;
await globalThis.handleServerMessage({
  type: 'extension.control', control_id: 'ctl_resize', action: 'workers.resize', payload: { target: 7 },
});
result = sent.at(-1);
assert.equal(result.ok, true);
assert.equal(result.data.target, 7, 'server concurrency target should be acknowledged');
assert.equal(result.data.target_reached, true);
assert.equal(result.data.rounds, 0);
assert.equal(result.data.window_policy, 'on-demand-single-authority-v30');
assert.equal(result.data.window_snapshot.total, 2, 'resize must not create or close browser windows');
assert.equal(result.data.window_snapshot.target, 0);
assert.ok(reports > reportsBeforeResize);
assert.equal(source.includes('__CHAT2API_RESERVE_POOL_V29__'), false);
assert.equal(source.includes('__CHAT2API_TAB_SUPERVISOR_V32__'), false);
assert.equal(source.includes('chrome.windows.create'), false);
assert.equal(source.includes('chrome.windows.remove'), false);

await globalThis.handleServerMessage({
  type: 'extension.control', control_id: 'ctl_invalid', action: 'workers.resize', payload: { target: 0 },
});
result = sent.at(-1);
assert.equal(result.ok, false);
assert.match(result.error, /between 1 and 32/i);

console.log('capacity_control_v35 single-authority VM contract passed');
