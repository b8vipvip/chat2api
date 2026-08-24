import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../chrome_extension/background_capacity_control_v36.js', import.meta.url), 'utf8');
const forwarded = [];
const handled = [];
const sent = [];
const storageListeners = [];

globalThis.__CHAT2API_CAPACITY_CONTROL_V35__ = {
  handle: async message => {
    handled.push(message);
    return {ok: true};
  },
  snapshot: async () => ({total: 2, active: 0, target: 2}),
};
globalThis.handleServerMessage = async message => forwarded.push(message);
globalThis.trySendSocket = async payload => {
  sent.push(payload);
  return true;
};
globalThis.chrome = {
  runtime: {getManifest: () => ({version: '0.8.2'})},
  storage: {
    onChanged: {addListener: fn => storageListeners.push(fn)},
  },
};

vm.runInThisContext(source, {filename: 'background_capacity_control_v36.js'});
const state = globalThis.__CHAT2API_CAPACITY_CONTROL_V36__;
assert.ok(state, 'v36 dispatcher should install');
assert.equal(state.version, 36);

await globalThis.handleServerMessage({
  type: 'extension.control',
  control_id: 'ctl_test',
  action: 'windows.snapshot',
});
assert.equal(handled.length, 1);
assert.equal(handled[0].control_id, 'ctl_test');
assert.equal(forwarded.length, 0);

await globalThis.handleServerMessage({type: 'chat.request', request_id: 'req_passthrough'});
assert.equal(forwarded.length, 1);
assert.equal(forwarded[0].request_id, 'req_passthrough');

await state.report();
const status = sent.find(item => item.type === 'extension.status');
assert.ok(status, 'dispatcher should proactively report capability');
assert.equal(status.metadata.extension_version, '0.8.2');
assert.equal(status.metadata.extension_control_version, 36);
assert.equal(status.metadata.extension_control_ready, true);
assert.equal(status.metadata.extension_control_transport, 'authoritative-global-dispatch-v36');

assert.equal(storageListeners.length, 1);
storageListeners[0]({socketState: {newValue: 'connected'}}, 'local');
await new Promise(resolve => setTimeout(resolve, 160));
assert.ok(sent.filter(item => item.type === 'extension.status').length >= 2);

console.log('capacity_control_v36 VM contract passed');
