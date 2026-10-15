import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("app/admin_unified_workers_v153.js","utf8");
const start = source.indexOf("  function workerState(textValue,tone){");
const end = source.indexOf("  function workerVersion(client, worker){",start);
assert.ok(start >= 0 && end > start);
const context = {esc: String, text: x => String(x ?? "").trim(), Date, Number, globalThis:{}};
vm.runInNewContext(source.slice(start,end) +
  "\nglobalThis.probe = {windowsWorkerState,linuxWorkerState};", context);
const {windowsWorkerState:win,linuxWorkerState:linux} = context.probe;
const ext = {online:true,connection_enabled:true,capacity:{used_units:0}};
const staleAgent = {enabled:true,status:"offline",last_seen_at:"2020-01-01T00:00:00Z"};
assert.match(linux(staleAgent,ext),/运行正常/,"online dispatch bridge must override stale Agent heartbeat");
assert.match(linux({...staleAgent,enabled:false},ext),/停用/,"server Worker switch owns disable status");
assert.match(linux({...staleAgent,revoked_at:"2026-01-01"},ext),/已吊销/);
assert.match(linux(staleAgent,{...ext,online:false}),/离线/);
assert.match(win(ext),/运行正常/);
assert.match(win({...ext,online:false}),/离线/);
assert.match(win({...ext,connection_enabled:false}),/停用/);
assert.match(source,/if\(!extensionsLoaded\|\|!linuxLoaded\)/);
assert.match(source,/正在同步 Worker 状态/);
assert.match(source,/extensionsLoaded=true;render\(\)/);
assert.match(source,/linuxLoaded=true;render\(\)/);
console.log("Worker status hydration and canonical transport authority checks passed");
