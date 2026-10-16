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
const context={
  globalThis:{__CHAT2API_CONVERSATION_ROUTING_V1__:{failRequest:async()=>{retires++;return true;}}},
  resolveRoutedTab:async()=>({id:++allocated,windowId:100+allocated}),
  withCurrentTab:async(_,fn)=>fn(),
  baseHandleServerMessage:async()=>{delivered++;if(delivered===1){const e=new Error("No tab with id: 1");e.code="routed_tab_disappeared_before_ack";throw e;}return "ok";},
  trySendSocket:async()=>true,
  String,
};
vm.runInNewContext(dispatch+"\nglobalThis.dispatch=routedDispatch;",context);
assert.equal(await context.globalThis.dispatch({request_id:"request1"}),"ok");
assert.equal(allocated,2);
assert.equal(retires,1);
assert.equal(delivered,2);
assert.match(dispatchSource,/throw new Error\(\`No tab with id: \$\{routedId\}\`\)/,
  "routed missing tab must not fall back to a different tab");
assert.match(bridgeSource,/error\.code = "routed_tab_disappeared_before_ack"/);
assert.match(bridgeSource,/No tab with id:/);
console.log("canonical route owner close race and pre-ACK bounded redispatch checks passed");
