import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import {webcrypto} from "node:crypto";

const source=readFileSync("chrome_extension/background_native_page_diagnostic_v1.js","utf8");
const events=[],opens=[],closed=[];
const answer={schema:"windows-native-page-evidence-v1",version:"windows-dom-1.0.0",
  boundary_verified:true,request_bound:false,complete:true,
  message_id:"reply-abc",counts:{nodes:12,text_chars:50,tables:1,images:2},
  probe:{ready_state:"complete",assistant_role_nodes:1},
  root:{tag:"div",children:[{tag:"p",children:[{tag:"#text",text:"safe assistant answer"}]}]},
  limitations:["No full-page screenshot","No source image bytes","No API request identity validation"]};
const scope={
  setTimeout:fn=>{Promise.resolve().then(fn);return 1;},
  chrome:{
    storage:{local:{get:async()=>({boundTabId:8})}},
    tabs:{
      query:async()=>[
        {id:7,windowId:25,url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"},
        {id:8,windowId:26,url:"https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"}
      ],
      create:async options=>{opens.push(options);return {id:101};},
      get:async()=>({id:101,status:"complete"}),
      remove:async id=>{closed.push(id);}
    },
    scripting:{executeScript:async()=>[{result:{ok:true,answer}}]}
  },
  TextEncoder,crypto:webcrypto,btoa,
  URL,Date,
  trySendSocket:async event=>{events.push(event);return true;},
  handleServerMessage:async message=>{events.push({type:"old.handler",message});}
};
runInNewContext(source,scope);
const diag=scope.__CHAT2API_WINDOWS_NATIVE_PAGE_DIAG_V1__;
assert.ok(diag);
assert.equal(diag.version,"windows-dom-1.0.0");
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
assert.equal(opens[0].windowId,26,"Reuse the explicitly bound Worker session, not an unrelated ChatGPT window");
assert.deepEqual(closed,[101],"Owned diagnostic tab removed after capture");
assert.equal(scope.__CHAT2API_NATIVE_PAGE_DIAG_TAB_IDS_V1__?.size,0,
  "Remove the diagnostic tab lease even after completion");
assert.equal(events.filter(x=>x.type==="native.page.windows.finished"&&x.ok).length,1);
assert.ok(events.some(x=>x.type==="native.page.windows.chunk"));
assert.equal(events.some(x=>x.type==="chat.completed"||x.type==="chat.request"),false,
  "Diagnostics never own the request terminal or issue a model request");
assert.ok(events.every(x=>!JSON.stringify(x).includes("token=1")));
console.log("Windows native diagnosis: isolated tab, URL allowlist, bounded authenticated chunks, read-only completion");
