import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../chrome_extension/background_capacity_control_v35.js', import.meta.url), 'utf8');
const forwarded = [];
const sent = [];
let activeRows = [
  { window_id: 101, tab_id: 201, status: 'ready' },
  { window_id: 102, tab_id: 202, status: 'in_use' },
  { window_id: 103, tab_id: 203, status: 'ready' },
];
let reports = 0;
let windowTarget = 3;
let windowSource = 'explicit';

const observer = {
  report: async () => { reports += 1; return true; },
  snapshot: () => ({ active: activeRows, decision_authority: false }),
};

const limiter = {
  setLimit: async (target, source) => {
    windowTarget = Number(target);
    windowSource = String(source || 'explicit');
    return { limit: windowTarget, source: windowSource };
  },
  snapshot: () => ({ limit: windowTarget, source: windowSource }),
};

const pool = {
  setTarget: async (target, source) => {
    windowTarget = Number(target);
    windowSource = String(source || 'explicit');
    while (activeRows.length < windowTarget) {
      const n = activeRows.length + 101;
      activeRows.push({ window_id: n, tab_id: n + 100, status: 'ready' });
    }
    while (activeRows.length > windowTarget) activeRows.pop();
    return { ok: true, target: windowTarget, source: windowSource };
  },
  snapshot: async () => {
    const active = activeRows.filter(row => row.status === 'in_use').length;
    return {
      version: 132,
      revision: 132,
      policy: 'persistent-prewarmed-total-window-pool-v132',
      target: windowTarget,
      configured_target: windowTarget,
      effective_target: windowTarget,
      target_reached: activeRows.length === windowTarget,
      total: activeRows.length,
      active,
      idle: activeRows.length - active,
      own: activeRows.length,
      warm: Math.max(0, activeRows.length - active),
      standby: Math.max(0, activeRows.length - active),
      routed: active,
      all_chatgpt_windows: activeRows.length,
      login_ready: true,
      worker_disabled: false,
      speculative_windows: false,
      route_window_authority: 'conversation-routing-v30+persistent-pool-v132',
      window_decision_authority: 'persistent-window-pool-v132',
      observed_at: new Date().toISOString(),
    };
  },
};

globalThis.__CHAT2API_WINDOW_OBSERVER_V90__ = observer;
globalThis.__CHAT2API_ROUTED_WINDOW_LIMIT_V121__ = limiter;
globalThis.__CHAT2API_PERSISTENT_WINDOW_POOL_V132__ = pool;
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
assert.equal(result.data.window_snapshot.total, 3);
assert.equal(result.data.window_snapshot.active, 1);
assert.equal(result.data.window_snapshot.target, 3);
assert.equal(result.data.window_snapshot.prewarmed_windows, true);
assert.equal(result.data.window_snapshot.speculative_windows, false);
assert.equal(result.data.window_snapshot.route_window_authority, 'conversation-routing-v30+persistent-pool-v132');
assert.equal(result.metadata.reserve_window_target, 3);
assert.equal(result.metadata.window_decision_authority, 'persistent-window-pool-v132');
assert.equal(result.metadata.prewarmed_windows, true);
assert.equal(result.metadata.speculative_windows, false);
assert.equal(result.metadata.extension_control_version, 36);

await globalThis.handleServerMessage({
  type: 'extension.control', control_id: 'ctl_resize', action: 'workers.resize', payload: { target: 7 },
});
result = sent.at(-1);
assert.equal(result.ok, true);
assert.equal(result.data.target, 7, 'server concurrency target should be acknowledged');
assert.equal(result.data.target_reached, true);
assert.equal(result.data.rounds, 0);
assert.equal(result.data.window_policy, 'persistent-window-target-independent-v132');
assert.equal(result.data.window_snapshot.total, 3, 'concurrency resize must not resize physical windows directly');
assert.equal(result.data.window_snapshot.target, 3);

await globalThis.handleServerMessage({
  type: 'extension.control', control_id: 'ctl_windows', action: 'windows.limit', payload: { target: 5, source: 'explicit' },
});
result = sent.at(-1);
assert.equal(result.ok, true);
assert.equal(result.data.target, 5);
assert.equal(result.data.target_reached, true);
assert.equal(result.data.window_policy, 'persistent-prewarmed-total-window-pool-v132');
assert.equal(result.data.window_snapshot.total, 5);
assert.equal(result.data.window_snapshot.target, 5);
assert.equal(result.data.window_snapshot.window_decision_authority, 'persistent-window-pool-v132');

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

console.log('capacity_control_v35 persistent-pool VM contract passed');
