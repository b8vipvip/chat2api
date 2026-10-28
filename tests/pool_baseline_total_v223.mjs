import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const poolSource = fs.readFileSync(new URL('../chrome_extension/conversation_persistent_pool_v132.js', import.meta.url), 'utf8');
const start = poolSource.indexOf('  function requiredWindowTotal(');
const stop = poolSource.indexOf('  function snapshotFrom(', start);
assert.ok(start > 0 && stop > start, 'real v132 policy must exist');
const context = { MAX_TARGET: 32, normalizeTarget: value => Number(value) >= 1 && Number(value) <= 32 ? Math.floor(Number(value)) : null };
const policy = vm.runInNewContext(poolSource.slice(start, stop) + '\nrequiredWindowTotal', context);
const cases = [
  [0, 3, 3], // no requests: three ready
  [1, 3, 2], // one busy: two ready
  [2, 3, 1], // two busy: one ready
  [3, 4, 1], // three busy: create a fourth ready window
  [2, 3, 1], // one request terminates and router closes it
  [0, 3, 3], // normal baseline restored
  [4, 5, 1], // one more request can claim prior standby
];
for (const [busy, total, idle] of cases) {
  assert.equal(policy(3, busy, busy), total, `busy ${busy}: baseline 3 requires total ${total}`);
  assert.equal(total - busy, idle);
}
assert.equal(policy(3, 4, 4), 5);
assert.equal(policy(3, 2, 3), 4, 'leased route ownership protects fourth window until it closes');
assert.equal(policy(3, 32, 32), 32, 'no unbounded physical window creation');
assert.equal(policy(3, 2, 2, true), 1, 'explicit logout compact mode');
assert.match(poolSource, /while \(rows\.length < requiredTotal && rows\.length < MAX_PHYSICAL_WINDOWS\)/);
assert.match(poolSource, /busyWindowIds\(value\)/);
assert.match(poolSource, /await shrinkStandby\(value, rows, standbyTarget, reason\)/);
assert.match(poolSource, /if \(protectedIds\.has\(row\.window_id\)\) continue;/);
const legacy = fs.readFileSync(new URL('../chrome_extension/background_window_limit_v121.js', import.meta.url), 'utf8');
const cap = fs.readFileSync(new URL('../chrome_extension/background_routed_window_cap_v111.js', import.meta.url), 'utf8');
assert.match(legacy, /if \(globalThis\.__CHAT2API_PERSISTENT_WINDOW_POOL_V132__\) return false/);
assert.match(cap, /owner: "persistent-window-pool-v132", closed: 0/);
const router = fs.readFileSync(new URL('../chrome_extension/conversation_routing.js', import.meta.url), 'utf8');
assert.match(router, /const IDLE_CLOSE_MS = 5 \* 1000/);
console.log('v223 baseline total window policy and retired legacy cap verified');
