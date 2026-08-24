import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../chrome_extension/background_capacity_capability_v37.js', import.meta.url), 'utf8');
const sent = [];
const storageListeners = [];
const alarmListeners = [];
const globalListeners = new Map();
const handled = [];

globalThis.__CHAT2API_NATIVE_CAPACITY_CONTROL_VERSION__ = 36;
globalThis.__CHAT2API_NATIVE_CAPACITY_DISPATCH_V37__ = true;
globalThis.__CHAT2API_CAPACITY_CONTROL_V35__ = {
  handle: async message => {
    handled.push(message);
    return {ok: true};
  },
  snapshot: async () => ({total: 3, active: 1, target: 5}),
};
globalThis.sendSocket = async payload => {
  sent.push(payload);
};
globalThis.self = {
  addEventListener: (type, handler) => globalListeners.set(type, handler),
};
globalThis.chrome = {
  runtime: {getManifest: () => ({version: '0.8.1'})},
  storage: {onChanged: {addListener: fn => storageListeners.push(fn)}},
  alarms: {onAlarm: {addListener: fn => alarmListeners.push(fn)}},
};

vm.runInThisContext(source, {filename: 'background_capacity_capability_v37.js'});

const state = globalThis.__CHAT2API_CAPACITY_CAPABILITY_V37__;
assert.ok(state, 'v37 capability reporter should install');
assert.equal(state.version, 37);
assert.equal(state.ready, false, 'readiness is computed on first report');
assert.equal(typeof state.report, 'function');
assert.equal(typeof state.installPostControlReporter, 'function');

assert.equal(await state.report('vm-test'), true);
let status = sent.at(-1);
assert.equal(status.type, 'extension.status');
assert.equal(status.metadata.extension_version, '0.8.1');
assert.equal(status.metadata.extension_control_version, 36);
assert.equal(status.metadata.extension_control_ready, true);
assert.equal(status.metadata.extension_control_native_version, 36);
assert.equal(status.metadata.extension_control_native_ready, true);
assert.equal(status.metadata.extension_control_transport, 'background-native-dispatch-v37');
assert.equal(status.metadata.extension_control_capability_reporter, 37);

const beforeControlReports = sent.length;
await globalThis.__CHAT2API_CAPACITY_CONTROL_V35__.handle({type: 'extension.control', action: 'windows.snapshot'});
assert.equal(handled.length, 1);
assert.ok(sent.length > beforeControlReports, 'post-control reporter should restore authoritative capability status');
status = sent.at(-1);
assert.match(status.metadata.extension_control_transport, /native-dispatch-v37/);

assert.equal(typeof globalListeners.get('unhandledrejection'), 'function');
globalListeners.get('unhandledrejection')({reason: new Error('synthetic runtime failure')});
await new Promise(resolve => setTimeout(resolve, 10));
assert.equal(state.runtime_errors.length, 1);
assert.match(state.runtime_errors[0].error, /synthetic runtime failure/);
assert.ok(sent.some(item => item.metadata?.extension_runtime_error_count === 1));

assert.equal(storageListeners.length, 1);
assert.equal(alarmListeners.length, 1);
storageListeners[0]({socketState: {newValue: 'connected'}}, 'local');
alarmListeners[0]({name: 'chat2api-keepalive'});
await new Promise(resolve => setTimeout(resolve, 160));
assert.ok(sent.length >= 4);

console.log('capacity_capability_v37 VM contract passed');
