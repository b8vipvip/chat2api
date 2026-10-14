import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const routeSource = fs.readFileSync(new URL("../chrome_extension/conversation_routing.js", import.meta.url), "utf8");
const logSource = fs.readFileSync(new URL("../chrome_extension/background_logging.js", import.meta.url), "utf8");
const wait = ms => new Promise(done => setTimeout(done,ms));

async function routeScenario({stale = false, strict = true} = {}) {
  const callbacks = {};
  const routes = {
    key_A: { api_key_id:"key_A", generation:1, window_id:100, tab_id:101,
      window_owned:true, inflight_request_id:"req_previous", turn_count:0, text_chars:0,
      attachment_count:0, slow_load_strikes:0, last_active_at:0 },
  };
  const data = {chat2apiConversationRoutesV1: routes};
  let removedWindows = 0, createdWindows = 0, diagnostics = [];
  const chrome = {
    storage:{local:{
      get: async k => ({[k]:data[k]}),
      set: async obj => { await wait(2); Object.assign(data,obj); },
    }},
    tabs: {
      get: async id => ({id,windowId:id===101?100:200,url:"https://chatgpt.com/c/abc",status:"complete"}),
      update: async (id) => ({id,windowId:100,url:"https://chatgpt.com/c/abc",status:"complete"}),
      query: async ()=>[],
      onUpdated:{addListener(fn){callbacks.updated=fn}},
      onRemoved:{addListener(fn){callbacks.tabRemoved=fn}},
    },
    windows: {
      create: async () => {createdWindows++;return {id:200,tabs:[{id:201,windowId:200,url:"https://chatgpt.com/",status:"complete"}]};},
      remove: async () => {removedWindows++},
      onRemoved:{addListener(fn){callbacks.windowRemoved=fn}},
    },
    alarms:{
      create:async()=>{}, clear:async()=>{},
      onAlarm:{addListener(fn){callbacks.alarm=fn}},
    },
    runtime:{onMessage:{addListener(fn){callbacks.message=fn}}},
  };
  const ctx={chrome,URL,setTimeout,clearTimeout,Date,Math,Number,Promise,console,
    ensureContent:async()=>true,trySendSocket:async event=>{diagnostics.push(event)},
    sendExtensionStatus:async()=>true,resolveTargetTab:async()=>({id:101,windowId:100,url:"https://chatgpt.com/"})};
  ctx.globalThis=ctx;
  vm.createContext(ctx);
  vm.runInContext(routeSource,ctx);
  await wait(20);
  if (!stale) {
    callbacks.message({type:"chat2api.event",event:{type:"chat.completed",request_id:"req_previous",text:"ok"}},
      {tab:{id:101,url:"https://chatgpt.com/c/abc"}});
  }
  const msg={type:"chat.request",request_id:"req_new",
    routing:{api_key_id:"key_A",scheduler_authority:"server-single-authority-scheduler-v58",strict_api_fifo:strict},
    prompt:"hello"};
  if (stale && !strict) {
    await assert.rejects(ctx.resolveTargetTabForRequest(msg),/route is in use/);
  } else {
    const tab=await ctx.resolveTargetTabForRequest(msg);
    assert.ok(tab?.id);
    assert.equal(data.chat2apiConversationRoutesV1.key_A.inflight_request_id,"req_new");
    if (stale) assert.equal(removedWindows,1,"stale route's old window should be retired");
    else assert.equal(removedWindows,0,"completed request should reuse its existing route");
  }
}
await routeScenario();
await routeScenario({stale:true});
await routeScenario({stale:true,strict:false});

async function workerLogScenario(platform, session, local){
  const handlers={};
  const chrome = {
    storage:{
      local: {
        get:async keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(k=>[k,local[k]])),
        set:async obj=>Object.assign(local,obj),
        remove:async keys=>{for(const k of Array.isArray(keys)?keys:[keys])delete local[k];},
      },
      session:{
        get:async key=>({[key]:session[key]}),
        set:async obj=>Object.assign(session,obj),
      },
    },
    runtime: {
      getPlatformInfo:async()=>({os:platform}),
      getManifest:()=>({version:"0.22.130"}),
      onMessage:{addListener(fn){handlers.msg=fn}},
    },
    alarms:{clear:async()=>{},create:async()=>{},onAlarm:{addListener(fn){handlers.alarm=fn}}},
  };
  const ctx={chrome,TextEncoder,Date,Math,Number,Promise,console,setTimeout,clearTimeout,crypto:globalThis.crypto};
  ctx.globalThis=ctx;
  vm.createContext(ctx);
  vm.runInContext(logSource,ctx);
  await wait(50);
}
const seed=()=>({
  chat2apiRuntimeLogV1:[{message:"legacy"}],
  chat2apiRuntimeActiveRunsV4:[],
  chat2apiRuntimeRunIndexV4:[],
  chat2apiRuntimeChunkV2:"legacy-chunk",
  pairingId:"KEEP_PAIRING",
  chatgptLoginProfile:"KEEP_LOGIN",
});
const windowsLogs=seed(), session={};
await workerLogScenario("win",session,windowsLogs);
assert.deepEqual(windowsLogs.chat2apiRuntimeLogV1,[]);
assert.equal(windowsLogs.pairingId,"KEEP_PAIRING");
assert.equal(windowsLogs.chatgptLoginProfile,"KEEP_LOGIN");
windowsLogs.chat2apiRuntimeLogV1=[{message:"fresh"}];
await workerLogScenario("win",session,windowsLogs);
assert.equal(windowsLogs.chat2apiRuntimeLogV1.length,1,"MV3 background wake must retain current logs");
await workerLogScenario("win",{},windowsLogs);
assert.equal(windowsLogs.chat2apiRuntimeLogV1.length,0,"extension reload clears old logs");
const linuxLogs=seed();
await workerLogScenario("linux",{},linuxLogs);
assert.equal(linuxLogs.chat2apiRuntimeLogV1.length,1,"Linux Worker logs should be retained");
console.log("v130 route settling, stale lease isolation and Windows-only session log cleanup passed");
