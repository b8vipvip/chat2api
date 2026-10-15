import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../chrome_extension/conversation_persistent_pool_v132.js", import.meta.url), "utf8");
const tabs = new Map([[11, {id:11,windowId:101,url:"https://chatgpt.com/",status:"loading"}]]);
const windows = new Map([[101,{id:101,tabId:11}]]);
const storage = {
  chat2apiPersistentWindowPoolV132: {target:2,source:"test"},
  chatgptLoginState:"ready",chatgptLoginComposerReady:true,
  chat2apiWorkerMasterDisabledV61:false,
};
const tabUpdated = [], tabRemoved = [], windowCreated = [], windowRemoved = [];
let created=0, removed=0, checked=0, healthy=false;
const chrome = {
  storage:{
    local:{
      async get(defaults={}) {return {...(typeof defaults==="object" && defaults || {}),...storage};},
      async set(values) {Object.assign(storage,values);},
      async remove(key) {delete storage[key];},
    },
    onChanged:{addListener(){}},
  },
  windows:{
    async getAll() {return [...windows.values()].map(win=>({
      id:win.id,tabs:[{...tabs.get(win.tabId)}],
    }));},
    async create(options) {
      const id=200+(++created), tabId=300+created;
      tabs.set(tabId,{id:tabId,windowId:id,url:options.url,status:"loading"});
      windows.set(id,{id,tabId});
      return {id,tabs:[{...tabs.get(tabId)}]};
    },
    async remove(id) {removed++;const win=windows.get(id);if(win)tabs.delete(win.tabId);windows.delete(id);},
    onCreated:{addListener(fn){windowCreated.push(fn);}},
    onRemoved:{addListener(fn){windowRemoved.push(fn);}},
  },
  tabs:{
    async get(id){if(!tabs.has(id))throw Error("missing tab");return {...tabs.get(id)};},
    async update(id,change){Object.assign(tabs.get(id),change);return {...tabs.get(id)};},
    async query({windowId}={}){return [...tabs.values()].filter(t=>windowId==null||t.windowId===windowId);},
    onUpdated:{addListener(fn){tabUpdated.push(fn);}},
    onRemoved:{addListener(fn){tabRemoved.push(fn);}},
  },
  alarms:{create:async()=>{},clear:async()=>{},onAlarm:{addListener(){}}},
  runtime:{onMessage:{addListener(){}}},
};
const sandbox={
  chrome,URL,Date,console,
  setTimeout:()=>1,clearTimeout:()=>{},
  setInterval:()=>1,clearInterval:()=>{},
  ensureContent:async()=>{checked++;if(!healthy)throw Error("document not ready");},
  resolveTargetTabForRequest:async()=>null,
  __CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__:{revision:71},
  __CHAT2API_CONVERSATION_ROUTING_V1__:{loaded:true,routes:{},activeRequests:new Map()},
};
sandbox.globalThis=sandbox;
vm.createContext(sandbox);
vm.runInContext(source,sandbox,{filename:"conversation_persistent_pool_v132.js"});
const pool=sandbox.__CHAT2API_PERSISTENT_WINDOW_POOL_V132__;
assert.ok(pool);
await pool.reconcile("windows-loading-initial");
assert.equal(windows.size,2,"one existing loading tab must count toward physical capacity");
assert.equal(created,1);
assert.equal(checked,0,"runtime preflight must not touch loading tabs");
assert.equal(removed,0);
await pool.reconcile("windows-loading-repeat");
assert.equal(created,1,"loading tabs cannot trigger repeated creation");
assert.equal(removed,0);

await assert.rejects(
  pool.claimSlotForRequest({routing:{api_key_id:"key_1"}}),
  e=>e.code==="worker_window_runtime_pending",
  "admission must wait for an existing loading tab instead of replacing it",
);
assert.equal(created,1);

for(const tab of tabs.values()){
  tab.status="complete";
  for(const fn of tabUpdated)fn(tab.id,{status:"complete"},tab);
}
await pool.reconcile("document-just-completed");
assert.equal(checked,0,"document_idle modules must receive a settling grace period");
assert.equal(removed,0);
for(const id of tabs.keys())pool.runtimePageCompleteAt.set(id,Date.now()-6000);
await pool.reconcile("document-runtime-stale");
assert.equal(checked,2,"one runtime check per settled tab");
assert.equal(removed,0,"a runtime failure is not a window deletion signal");
assert.equal(created,1,"runtime failures must not create replacement tabs");
const checkedAfterFailure=checked;
await pool.reconcile("runtime-cooldown");
assert.equal(checked,checkedAfterFailure,"preflight reloads must be rate-limited during recovery");
healthy=true;
for(const id of tabs.keys())pool.runtimeRetryAfter.delete(id);
await pool.reconcile("runtime-recovered");
const snapshot=await pool.snapshot();
assert.equal(snapshot.standby,2,"healthy existing pages become available without reopening");
assert.equal(snapshot.runtime_ready_total,2);
assert.equal(removed,0);
assert.equal(created,1);
console.log("Windows loading window lifecycle VM contract passed");
