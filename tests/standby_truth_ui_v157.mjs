import assert from "node:assert/strict";
import fs from "node:fs";
const source = fs.readFileSync(new URL("../app/admin_extension_columns.js", import.meta.url), "utf8");
const begin = source.indexOf("  function truthByClient(payload) {");
const end = source.indexOf("  function workerActions(row) {", begin);
assert.ok(begin >= 0 && end > begin, "canonical truth functions exist");
let now = 100_000;
const cache = new Map();
const factory = new Function("verifiedStandby", "STANDBY_GRACE_MS", "Date",
  source.slice(begin, end) + "\nreturn {truthByClient, occupancy};");
const {truthByClient, occupancy} = factory(cache, 45_000, {now: () => now});
const row = {client_id:"ext_test", online:true, connection_enabled:true, capacity:{used_units:0}};
const worker = (data = {}) => ({
  client_id:"ext_test", live_verified:true, chatgpt_routing_ready:true,
  snapshot_updated_at_ms:101, truth_status:"verified", standby_window_count:3, ...data,
});
const payload = data => ({truth_revision:89, workers:[worker(data)]});
const show = data => occupancy(row, truthByClient(data).get("ext_test"));
let out = show(payload());
assert.match(out.html, /data-chat2api-standby-source="live"/);
assert.match(out.html, />3<\/span>/);
assert.doesNotMatch(out.html, /上次核验/);

now += 3000;
out = show(payload({live_verified:false, chatgpt_routing_ready:true, standby_window_count:null, truth_status:"refresh-timeout"}));
assert.match(out.html, /data-chat2api-standby-source="cached"/);
assert.match(out.html, />3<\/span>/);
assert.match(out.html, /上次核验/);
assert.match(out.title, /本次尚未获得实时数据/);

now += 3000;
out = show(null); // HTTP transport failure, do not erase recent proof
assert.match(out.html, /上次核验/);
assert.match(out.html, />3<\/span>/);

now += 1000;
out = show(payload({snapshot_updated_at_ms:102, standby_window_count:2}));
assert.match(out.html, /data-chat2api-standby-source="live"/);
assert.match(out.html, />2<\/span>/);

now += 1000;
out = show(payload({snapshot_updated_at_ms:101, standby_window_count:3}));
assert.match(out.html, /data-chat2api-standby-source="cached"/);
assert.match(out.html, />2<\/span>/); // out-of-order response cannot roll count backward

now += 46000;
out = show(null);
assert.match(out.html, />\?<\/span>/);
assert.doesNotMatch(out.html, /上次核验/);

now += 1000;
show(payload({snapshot_updated_at_ms:103, standby_window_count:3}));
now += 1000;
out = show(payload({live_verified:false, truth_status:"login-required", standby_window_count:null}));
assert.match(out.html, />\?<\/span>/); // explicit logout invalidates cache

now += 1000;
show(payload({snapshot_updated_at_ms:104, standby_window_count:3}));
out = occupancy({...row,online:false}, truthByClient(null).get("ext_test"));
assert.match(out.html, />\?<\/span>/); // cached truth never appears for offline Worker
out = occupancy({...row,connection_enabled:false}, truthByClient(null).get("ext_test"));
assert.match(out.html, />\?<\/span>/); // disabled Worker

now += 1000;
out = show(payload({snapshot_updated_at_ms:105, standby_window_count:null}));
assert.match(out.html, />\?<\/span>/); // JSON null must not become numeric zero
console.log("v157 standby truth UI cases passed");
