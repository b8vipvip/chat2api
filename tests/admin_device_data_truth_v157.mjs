import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../app/admin_unified_workers_v153.js", import.meta.url), "utf8");
const context = {
  __CHAT2API_TEST_MODE__: true,
  document: { readyState: "loading", addEventListener() {} },
  setTimeout() {},
};
context.globalThis = context;
vm.runInNewContext(source, context, { filename: "admin_unified_workers_v153.js" });

const helpers = context.__CHAT2API_WORKER_DEVICE_DATA_V157__;
assert.ok(helpers, "test helpers must be available in test mode only");

const clients = [
  {
    client_id: "stale-offline",
    online: false,
    last_seen_at: "2026-10-11T10:03:00+08:00",
    metadata: { network_country_code: "JP", network_probe_status: "external" },
  },
  {
    client_id: "live-newest",
    online: true,
    last_seen_at: "2026-10-11T10:05:00+08:00",
    metadata: {},
  },
  {
    client_id: "live-network",
    online: true,
    last_seen_at: "2026-10-11T10:04:00+08:00",
    metadata: { bridge: { network_country_code: "US", network_probe_status: "external" } },
  },
];

assert.equal(helpers.preferredClient(clients).client_id, "live-newest");
assert.equal(helpers.deviceNetworkLabel(clients), "US");
assert.equal(helpers.latestSeen(clients), "2026-10-11T10:05:00+08:00");
assert.equal(helpers.deviceNetworkLabel([clients[0]]), "JP（上次报告）");
assert.equal(helpers.latestSeen([{ last_seen_at: null }, {}]), "");
