import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import {webcrypto} from "node:crypto";

const source=readFileSync("chrome_extension/background_native_page_diagnostic_v1.js","utf8");
const events=[],opens=[],ownedWindows=[],closed=[],activated=[],navigated=[],focused=[];
const localStorage={boundTabId:8};
const hotCalls=[];
let userScriptsAvailable=true;
let activeTabId=8;
const answer={schema:"windows-native-page-evidence-v1",version:"windows-dom-1.1.2",
  boundary_verified:true,request_bound:false,complete:true,
  message_id:"reply-abc",counts:{nodes:12,text_chars:50,tables:1,images:2},
  probe:{ready_state:"complete",assistant_role_nodes:1},
  root:{tag:"div",children:[{tag:"p",children:[{tag:"#text",text:"safe assistant answer"}]}]},
  limitations:["No full-page screenshot","No source image bytes","No API request identity validation"]};
const scope={
  setTimeout:fn=>{Promise.resolve().then(fn);return 1;},
  chrome:{
    storage:{local:{
      get:async input=>typeof input==="string"?{[input]:localStorage[input]}:{boundTabId:localStorage.boundTabId},
      set:async value=>{Object.assign(localStorage,value);}
    }},
    tabs:{
      query:async options=>options?.active===true ? [{id:activeTabId,windowId:26}] : [
        {id:7,windowId:25,url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"},
        {id:8,windowId:26,url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"}
      ],
      create:async options=>{opens.push(options);if(options.active)activeTabId=101;return {id:101};},
      get:async id=>({id,status:"complete",url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"}),
      remove:async id=>{closed.push(id);if(activeTabId===id)activeTabId=8;},
      update:async(id,options)=>{if(options.url)navigated.push({id,url:options.url});if(options.active){activated.push({id,...options});activeTabId=id;}}
    },
    scripting:{executeScript:async()=>[{result:{ok:true,answer}}]},
    userScripts:{
      getScripts:async()=>{if(!userScriptsAvailable)throw new Error("not allowed");return [];},
      execute:async args=>{hotCalls.push(args);return [{result:{ok:true,
        answer:{...answer,version:"windows-hot-1.0.0",collector_version:"1.0.0"}}}]}
    },
    windows:{update:async(id,options)=>{focused.push({id,...options});},
      create:async options=>{ownedWindows.push(options);return {id:35,tabs:[{id:301,windowId:35,url:"about:blank"}]};}}
  },
  TextEncoder,crypto:webcrypto,btoa,
  URL,Date,
  trySendSocket:async event=>{events.push(event);return true;},
  handleServerMessage:async message=>{events.push({type:"old.handler",message});}
};
runInNewContext(source,scope);
const diag=scope.__CHAT2API_WINDOWS_NATIVE_PAGE_DIAG_V1__;
assert.ok(diag);
assert.equal(diag.version,"windows-dom-1.1.2");
assert.equal(diag.validUrl("https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"),true);
for(const url of ["https://evil.example/c/01234567-89ab-cdef-0123-456789abcdef",
  "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef?token=1",
  "http://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"]){
  assert.equal(diag.validUrl(url),false,"Must reject untrusted URL");
}
await diag.execute({job_id:"b".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"});
assert.equal(opens.length,1);
assert.equal(opens[0].active,false,"Never steal an active API tab");
assert.equal(opens[0].url,"about:blank","Register the lease before navigating");
assert.equal(navigated[0].id,101);
assert.equal(opens[0].windowId,26,"Reuse the explicitly bound Worker session, not an unrelated ChatGPT window");
assert.deepEqual(closed,[101],"Owned diagnostic tab removed after capture");
assert.equal(scope.__CHAT2API_NATIVE_PAGE_DIAG_TAB_IDS_V1__?.size,0,
  "Remove the diagnostic tab lease even after completion");
assert.equal(events.filter(x=>x.type==="native.page.windows.finished"&&x.ok).length,1);
assert.ok(events.some(x=>x.type==="native.page.windows.chunk"));
assert.ok(events.some(x=>x.type==="native.page.windows.progress"&&x.stage==="tab_opened"),
  "Must emit real tab creation evidence");
assert.ok(events.some(x=>x.type==="native.page.windows.progress"&&x.stage==="tab_closed"),
  "Must emit tab cleanup evidence");
assert.equal(events.some(x=>x.type==="chat.completed"||x.type==="chat.request"),false,
  "Diagnostics never own the request terminal or issue a model request");
assert.ok(events.every(x=>!JSON.stringify(x).includes("token=1")));
// A signed-out account/redirect must not be misattributed to the target history.
const goodGet=scope.chrome.tabs.get;
scope.chrome.tabs.get=async()=>({id:102,status:"complete",
  url:"https://chatgpt.com/c/ffffffff-ffff-ffff-ffff-ffffffffffff"});
const beforeRedirect=events.length;
await diag.execute({job_id:"c".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"});
const redirected=events.slice(beforeRedirect);
assert.ok(redirected.some(x=>x.type==="native.page.windows.finished"&&
  x.ok===false&&x.error==="diagnostic_conversation_redirected"));
assert.ok(!redirected.some(x=>x.type==="native.page.windows.chunk"),
  "Wrong conversation must never export assistant content");
scope.chrome.tabs.get=goodGet;

// No explicitly bound or routed tab: create a dedicated diagnostic window,
// rather than selecting an unrelated ChatGPT tab or failing instantly.
scope.chrome.storage.local.get=async()=>({boundTabId:null});
const openedBefore=opens.length;
const closedBefore=closed.length;
const beforeUnbound=events.length;
await diag.execute({job_id:"d".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"});
assert.equal(opens.length,openedBefore,"Do not borrow existing Chrome windows");
assert.equal(ownedWindows.length,1,"Idle Worker must get a dedicated diagnosis window");
assert.equal(ownedWindows[0].url,"about:blank","Avoid lifecycle ownership race on creation");
assert.equal(ownedWindows[0].focused,false,"Background diagnosis must not steal focus");
assert.ok(navigated.some(x=>x.id===301&&x.url.includes("/c/01234567")));
assert.ok(closed.slice(closedBefore).includes(301),"Close only the dedicated diagnostic tab");
assert.equal(scope.__CHAT2API_NATIVE_PAGE_DIAG_TAB_IDS_V1__.size,0);
assert.ok(events.slice(beforeUnbound).some(x=>x.type==="native.page.windows.progress"&&x.stage==="window_created"));
assert.ok(events.slice(beforeUnbound).some(x=>x.type==="native.page.windows.finished"&&x.ok));

// Opt-in visible mode without an owned Worker tab: open only a new focused
// same-profile window. Never activate an unrelated Chrome tab.
const beforeForeground=ownedWindows.length;
await diag.execute({job_id:"4".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",show_tab:true});
assert.equal(ownedWindows.length,beforeForeground+1);
assert.equal(ownedWindows.at(-1).focused,true);
assert.ok(events.some(x=>x.type==="native.page.windows.finished"&&x.ok&&x.job_id==="4".repeat(32)));

// Foreground mode with an inflight API request must not create any new
// Chrome window, even when the Worker is currently unbound.
scope.__CHAT2API_CONVERSATION_ROUTING_V1__={routes:{},activeRequests:new Map([["inflight",{}]])};
const beforeBusyWindow=ownedWindows.length;
await diag.execute({job_id:"5".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",show_tab:true});
assert.equal(ownedWindows.length,beforeBusyWindow);
assert.ok(events.some(x=>x.type==="native.page.windows.finished"&&x.ok===false&&
  x.error==="worker_busy_visual_diagnosis"&&x.job_id==="5".repeat(32)));
delete scope.__CHAT2API_CONVERSATION_ROUTING_V1__;

// Router-owned tabs remain usable even when the legacy boundTabId is absent.
scope.__CHAT2API_CONVERSATION_ROUTING_V1__={routes:{active:{tab_id:7}}};
await diag.execute({job_id:"e".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"});
assert.equal(opens.at(-1).windowId,25);
assert.ok(events.some(x=>x.type==="native.page.windows.finished"&&x.ok&&x.job_id==="e".repeat(32)));

// An idle Worker can still diagnose using a previously runtime-verified pool
// standby, without borrowing a random ChatGPT tab.
delete scope.__CHAT2API_CONVERSATION_ROUTING_V1__;
scope.__CHAT2API_PERSISTENT_WINDOW_POOL_V132__={
  runtimeReadyTabs:new Map([[8,true]])
};
await diag.execute({job_id:"f".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"});
assert.equal(opens.at(-1).windowId,26);
assert.ok(events.some(x=>x.type==="native.page.windows.finished"&&x.ok&&x.job_id==="f".repeat(32)));
// Foreground is explicitly opt-in. It must use an already owned Worker
// window, reveal the diagnostic tab, and restore the previous active tab.
scope.chrome.storage.local.get=async()=>({boundTabId:8});
delete scope.__CHAT2API_PERSISTENT_WINDOW_POOL_V132__;
const beforeVisible=events.length;
await diag.execute({job_id:"1".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",
  show_tab:true});
assert.equal(opens.at(-1).active,true);
assert.equal(opens.at(-1).windowId,26);
assert.ok(focused.some(x=>x.id===26&&x.focused===true));
assert.ok(activated.some(x=>x.id===8&&x.active===true));
const visibleEvents=events.slice(beforeVisible);
assert.ok(visibleEvents.some(x=>x.type==="native.page.windows.finished"&&x.ok));
for(const stage of ["accepted","tab_opened","page_ready","assistant_scanning",
  "assistant_captured","uploading","upload_complete","tab_closed"]){
  assert.ok(visibleEvents.some(x=>x.type==="native.page.windows.progress"&&x.stage===stage),
    "Missing bounded progress stage "+stage);
}
assert.ok(visibleEvents.findIndex(x=>x.type==="native.page.windows.progress"&&x.stage==="tab_closed") <
  visibleEvents.findIndex(x=>x.type==="native.page.windows.finished"&&x.ok),
  "Close the temporary tab before finalizing the report");

// Busy API requests must never be hidden by a foreground diagnostic.
scope.__CHAT2API_CONVERSATION_ROUTING_V1__={routes:{},activeRequests:new Map([["req_active",{}]])};
const beforeBusy=events.length,openedBeforeBusy=opens.length;
await diag.execute({job_id:"2".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",
  show_tab:true});
assert.equal(opens.length,openedBeforeBusy,"Busy Worker cannot open visible diagnosis");
assert.ok(events.slice(beforeBusy).some(x=>x.type==="native.page.windows.finished"&&
  x.ok===false&&x.error==="worker_busy_visual_diagnosis"));
assert.equal(scope.__CHAT2API_NATIVE_PAGE_DIAG_TAB_IDS_V1__.size,0);
// A request that starts during capture must win over the diagnostic's
// original foreground tab restoration.
delete scope.__CHAT2API_CONVERSATION_ROUTING_V1__;
const oldExecute=scope.chrome.scripting.executeScript;
scope.chrome.scripting.executeScript=async (...args)=>{
  scope.__CHAT2API_CONVERSATION_ROUTING_V1__={
    routes:{},activeRequests:new Map([["req_arrived_mid_capture",{}]])
  };
  activeTabId=8; // A different real Worker tab has taken focus.
  return oldExecute(...args);
};
const beforeLateRestore=activated.length;
await diag.execute({job_id:"3".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",
  show_tab:true});
assert.equal(activated.length,beforeLateRestore,
  "Never steal focus from an API request that arrived during capture");
assert.ok(events.some(x=>x.type==="native.page.windows.finished"&&x.ok&&x.job_id==="3".repeat(32)));
// The independently released collector is installed only from a source and
// SHA-256 delivered through the authenticated Worker command.
const toolSource='/* Independently verified read-only assistant DOM collector. This header is long enough for the release size gate. */(()=>{const DIAGNOSTIC_TOOL_VERSION="1.0.0";return {ok:true};})()';
const toolDigest=[...new Uint8Array(await webcrypto.subtle.digest("SHA-256",
  new TextEncoder().encode(toolSource)))]
  .map(b=>b.toString(16).padStart(2,"0")).join("");
const initialEvents=events.length;
await diag.installTool({update_id:"a".repeat(32),version:"1.0.0",
  sha256:toolDigest,source:toolSource});
const installAck=events.slice(initialEvents).find(e=>e.type==="native.page.windows.tool.updated");
assert.equal(installAck?.ok,true);
assert.equal(installAck?.version,"1.0.0");
assert.equal(installAck?.sha256,toolDigest);
assert.equal(installAck?.updated,true);
assert.ok((await diag.getToolStatus()).ready);
assert.equal((await diag.getToolStatus()).version,"1.0.0");
assert.equal(localStorage.chat2apiWindowsNativePageToolV1?.sha256,toolDigest);
delete scope.__CHAT2API_CONVERSATION_ROUTING_V1__;
scope.chrome.scripting.executeScript=async()=>{throw Error("Bundled capture not expected after hot install");};
await diag.execute({job_id:"6".repeat(32),
  conversation_url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"});
assert.ok(hotCalls.some(x=>x.world==="USER_SCRIPT"&&
  x.js[0].code===toolSource&&x.target.tabId===101));
assert.ok(events.some(x=>x.type==="native.page.windows.finished"&&x.ok&&x.job_id==="6".repeat(32)));

// Bad digest must never replace a working collector.
await diag.installTool({update_id:"7".repeat(32),version:"1.0.1",
  sha256:"b".repeat(64),source:toolSource.replaceAll("1.0.0","1.0.1")});
assert.equal(events.at(-1).ok,false);
assert.equal((await diag.getToolStatus()).version,"1.0.0");

// User must explicitly enable Chrome's "Allow User Scripts" toggle.
userScriptsAvailable=false;
const permissionSource=toolSource.replaceAll("1.0.0","1.0.1");
const permissionDigest=[...new Uint8Array(await webcrypto.subtle.digest("SHA-256",
  new TextEncoder().encode(permissionSource)))]
  .map(b=>b.toString(16).padStart(2,"0")).join("");
await diag.installTool({update_id:"8".repeat(32),version:"1.0.1",
  sha256:permissionDigest,source:permissionSource});
assert.equal(events.at(-1).ok,false);
assert.match(events.at(-1).error,/user_scripts_permission_disabled/);
assert.equal((await diag.getToolStatus()).ready,false);
console.log("Windows native diagnosis: isolated windows, verified independent userScript install and execution");
