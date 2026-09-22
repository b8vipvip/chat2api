import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../chrome_extension/conversation_persistent_pool_guard_v133.js', import.meta.url), 'utf8');

const windows = new Map([
  [101, { id: 101, focused: true, tabs: [{ id: 201, windowId: 101, url: 'https://chatgpt.com/', status: 'complete' }] }],
  [102, { id: 102, focused: false, tabs: [{ id: 202, windowId: 102, url: 'https://chatgpt.com/c/old-a', status: 'complete' }] }],
  [103, { id: 103, focused: false, tabs: [{ id: 203, windowId: 103, url: 'https://chatgpt.com/c/old-b', status: 'complete' }] }],
]);
let loginState = 'login_required';
let reconcileCalls = 0;
let baseResolverCalls = 0;
let persistedRoutes = null;

const routes = {
  a: { window_id: 102, tab_id: 202, inflight_request_id: null, window_owned: false, close_after: null },
  b: { window_id: 103, tab_id: 203, inflight_request_id: null, window_owned: false, close_after: null },
};
const router = { routes, activeRequests: new Map() };
const pool = {
  target: 3,
  reservations: new Map(),
  reconcile: async reason => {
    reconcileCalls += 1;
    assert.ok(['login-state:login_required', 'request-admission-barrier-v133'].includes(reason));
    return { total: windows.size, target: 3 };
  },
};

globalThis.__CHAT2API_PERSISTENT_WINDOW_POOL_V132__ = pool;
globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__ = router;
globalThis.__CHAT2API_LOGIN_READINESS_V27__ = {
  snapshot: async () => ({ state: loginState, composer_ready: false, tab_id: 201, window_id: 101 }),
};
globalThis.resolveTargetTabForRequest = async message => {
  baseResolverCalls += 1;
  return { id: 201, windowId: 101, message };
};
globalThis.sendExtensionStatus = async () => true;
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};

globalThis.chrome = {
  windows: {
    getAll: async () => [...windows.values()].map(row => ({ ...row, tabs: row.tabs.map(tab => ({ ...tab })) })),
    remove: async id => { windows.delete(Number(id)); },
  },
  tabs: {
    get: async id => {
      for (const win of windows.values()) {
        const tab = win.tabs.find(item => item.id === Number(id));
        if (tab) return { ...tab };
      }
      throw new Error('tab not found');
    },
  },
  storage: {
    local: {
      get: async defaults => ({ ...defaults, chatgptLoginState: loginState, chatgptLoginComposerReady: false, chat2apiInitializationTabIdV32: 201 }),
      set: async value => {
        if (value.chat2apiConversationRoutesV1) persistedRoutes = value.chat2apiConversationRoutesV1;
      },
    },
    onChanged: { addListener: () => {} },
  },
  alarms: {
    clear: async () => true,
    create: () => Promise.resolve(),
    onAlarm: { addListener: () => {} },
  },
};

vm.runInThisContext(source, { filename: 'conversation_persistent_pool_guard_v133.js' });
const guard = globalThis.__CHAT2API_PERSISTENT_WINDOW_POOL_GUARD_V133__;
assert.ok(guard);
assert.equal(guard.version, 133);

const compacted = await guard.compactExplicitLogout('vm-test');
assert.equal(compacted.ok, true);
assert.equal(compacted.skipped, true);
assert.equal(compacted.reason, 'delegated-to-persistent-window-pool-v137');
assert.equal(windows.size, 3, 'guard must not mutate physical window cardinality');
assert.equal(routes.a.window_id, 102);
assert.equal(routes.b.window_id, 103);
assert.equal(persistedRoutes, null, 'guard must not mutate persisted route ownership');

loginState = 'ready';
const resolved = await globalThis.resolveTargetTabForRequest({ type: 'chat.request', request_id: 'req_guard' });
assert.equal(resolved.id, 201);
assert.equal(reconcileCalls, 2, 'login state and request admission both delegate reconciliation to the pool');
assert.equal(baseResolverCalls, 1);

console.log('persistent pool guard v133 VM contract passed');
