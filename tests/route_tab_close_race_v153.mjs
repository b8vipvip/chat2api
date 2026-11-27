import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const routerSource=readFileSync("chrome_extension/conversation_routing.js","utf8");
const poolSource=readFileSync("chrome_extension/conversation_persistent_pool_v132.js","utf8");
const dispatchSource=readFileSync("chrome_extension/conversation_dispatch.js","utf8");
const bridgeSource=readFileSync("chrome_extension/background.js","utf8");
function section(source,start,end) {
 const a=source.indexOf(start), b=source.indexOf(end,a+start.length);
 assert.ok(a>=0&&b>a,"missing lifecycle method: "+start);
 return source.slice(a,b);
}
const retirement=section(routerSource,"  async function retireRoute(key, route, requestId, reason) {","  async function failRequest");

// A tab found closed during the pre-dispatch readiness probe is also a
// reallocation. Measure the router retirement plus the second resolution,
// not just the later tabs.sendMessage recovery path.
const routeHelpers=section(dispatchSource,"  function recordRouteReallocation(message, started) {","  function enqueueDispatch(taskFactory) {");
let readinessClock=0,readinessAllocations=0,readinessRetires=0,readinessPersists=0;
const readinessContext={
  globalThis:{
    resolveTargetTabForRequest:async()=>({id:++readinessAllocations,windowId:77}),
    __CHAT2API_CONVERSATION_ROUTING_V1__:{failRequest:async()=>{readinessRetires++;return true;}},
  },
  state:{requestTabs:new Map(),nativeRequestTabs:new Map()},
  persistNativeRequestTabs:async()=>{
    readinessPersists++;
    assert.ok(readinessContext.state.nativeRequestTabs instanceof Map);
  },
  monotonicNow:()=>++readinessClock,
  elapsedMs:started=>Math.max(0,++readinessClock-started),
  timingValue:value=>typeof value==="number"&&Number.isFinite(value)&&value>=0?Math.round(value*10)/10:null,
  chrome:{
    tabs:{get:async id=>(id===1||id===3)?null:{id,windowId:77}},
    storage:{local:{set:async()=>{}}},
  },
  String,Error,
};
vm.runInNewContext(routeHelpers+"\nglobalThis.resolve=resolveRoutedTab;globalThis.record=recordRouteReallocation;",readinessContext);
const readinessMessage={request_id:"readiness-reallocation"};
const readinessTab=await readinessContext.globalThis.resolve(readinessMessage);
assert.equal(readinessTab.id,2);
assert.equal(readinessAllocations,2);
assert.equal(readinessRetires,1);
assert.equal(readinessPersists,1,"routed tab resolution must persist its native action route");
assert.equal(readinessMessage.__chat2api_worker_route_reallocation_ms,1,
  "pre-dispatch tab-readiness retry must expose its own duration");

// A post-ACK recovery may itself encounter a tab already closed during the
// second readiness probe. Suppress the nested timer and let the outer recovery
// timer include the whole operation exactly once.
const outerRecoveryStarted=readinessContext.monotonicNow();
await readinessContext.globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__.failRequest(
  "readiness-reallocation","lost-tab-before-content-ack-v153");
const recoveredAgain=await readinessContext.globalThis.resolve(
  readinessMessage,{recordReallocation:false});
assert.equal(recoveredAgain.id,4);
assert.equal(readinessMessage.__chat2api_worker_route_reallocation_ms,1,
  "nested readiness retry must not record separately when the outer recovery owns timing");
readinessContext.globalThis.record(readinessMessage,outerRecoveryStarted);
assert.equal(readinessMessage.__chat2api_worker_route_reallocation_ms,3,
  "outer recovery timing must include the nested readiness retry exactly once");


async function runRetirement({supersedeDuringAlarm=false,takeDuringPersist=false}={}) {
 const route={window_id:32,tab_id:12,inflight_request_id:"old"};
 const calls=[];
 const state={retiredRequests:new Set(),retiringWindows:new Set(),activeRequests:new Map([["old",{window_id:32}]]),routes:{key:route}};
 const sandbox={
   state,globalThis:{},Date,setTimeout:()=>0,Number,String,
   clearCloseAlarm:async()=>{if(supersedeDuringAlarm)route.inflight_request_id="new";},
   resetClosedRoute:()=>{route.window_id=null;route.tab_id=null;route.inflight_request_id=null;},
   persist:async()=>{assert.equal(state.retiringWindows.has(32),true);if(takeDuringPersist)state.activeRequests.set("new",{window_id:32});},
   sendExtensionStatus:async()=>null,
   chrome:{windows:{remove:async id=>calls.push(id)}},
 };
 vm.runInNewContext(retirement+"\nglobalThis.retire=retireRoute;",sandbox);
 const result=await sandbox.globalThis.retire("key",route,"old","test");
 return {result,calls,state,route};
}
const superseded=await runRetirement({supersedeDuringAlarm:true});
assert.equal(superseded.result,false);
assert.equal(superseded.calls.length,0);
assert.equal(superseded.route.inflight_request_id,"new");
const normal=await runRetirement();
assert.equal(normal.result,true);
assert.equal(normal.calls.join(","),"32");
assert.equal(normal.state.retiringWindows.size,0);
const borrowed=await runRetirement({takeDuringPersist:true});
assert.equal(borrowed.result,true);
assert.equal(borrowed.calls.length,0,"late cleanup must not close a newly leased window");
assert.match(poolSource,/for \(const id of value\?\.retiringWindows \|\| \[\]\)/);
assert.match(poolSource,/busyWindowIds\(router\)\.has\(Number\(windowId\)\)/);

// The dispatcher is allowed one reallocation only when Chrome confirms that
// no content ACK could have reached the tab. It must not become a new owner.
const dispatch=section(dispatchSource,"  async function routedDispatch(message) {","  async function dispatchKnownRequest");
let allocated=0,retires=0,delivered=0;
let monotonicClock = 0;
const context={
  globalThis:{__CHAT2API_CONVERSATION_ROUTING_V1__:{failRequest:async()=>{retires++;return true;}}},
  monotonicNow:()=>++monotonicClock,
  elapsedMs:started=>Math.max(0,++monotonicClock-started),
  timingValue:value=>typeof value==="number"&&Number.isFinite(value)&&value>=0?Math.round(value*10)/10:null,
  recordRouteReallocation:(message,started)=>{const prior=typeof message.__chat2api_worker_route_reallocation_ms==="number"?message.__chat2api_worker_route_reallocation_ms:0;message.__chat2api_worker_route_reallocation_ms=Math.round((prior+Math.max(0,++monotonicClock-started))*10)/10;},
  resolveRoutedTab:async()=>({id:++allocated,windowId:100+allocated}),
  withCurrentTab:async(_,fn)=>fn(),
  baseHandleServerMessage:async()=>{delivered++;if(delivered===1){const e=new Error("No tab with id: 1");e.code="routed_tab_disappeared_before_ack";throw e;}return "ok";},
  trySendSocket:async()=>true,
  String,
};
vm.runInNewContext(dispatch+"\nglobalThis.dispatch=routedDispatch;",context);
const dispatchMessage={request_id:"request1"};
assert.equal(await context.globalThis.dispatch(dispatchMessage),"ok");
assert.equal(allocated,2);
assert.equal(retires,1);
assert.equal(delivered,2);
assert.equal(dispatchMessage.__chat2api_worker_route_reallocation_ms,1,
  "pre-ACK recovery must expose the reallocation duration separately");
assert.equal(dispatchMessage.__chat2api_worker_route_resolve_ms,4,
  "total route time must include the initial allocation and the recovery attempt");
assert.match(bridgeSource,/worker_route_reallocation_ms:\s*timing\(message\.__chat2api_worker_route_reallocation_ms\)/,
  "background diagnostics must preserve the reallocation duration");
assert.match(dispatchSource,/throw new Error\(\`No tab with id: \$\{routedId\}\`\)/,
  "routed missing tab must not fall back to a different tab");
assert.match(bridgeSource,/error\.code = "routed_tab_disappeared_before_ack"/);
assert.match(bridgeSource,/No tab with id:/);
console.log("canonical route owner close race and pre-ACK bounded redispatch checks passed");
