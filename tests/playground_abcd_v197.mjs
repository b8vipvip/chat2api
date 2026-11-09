import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { test } from "node:test";
import { webcrypto } from "node:crypto";

const source=readFileSync(new URL("../app/admin_playground_abcd_v197.js",import.meta.url),"utf8");
const FULL = "## 开店\n" + "咖啡运营".repeat(900) + 
  "\n| 类别 | 数值 |\n|---|---|\n| A | 1 |\n| 类别 | 数值 |\n|---|---|\n| B | 2 |" +
  "\n\\(100+200=300\\)\n" + String.fromCharCode(96).repeat(3) +
  "python\nimport math\n\ndef revenue(amount):\n    value = float(amount)\n    return math.floor(value)\n" + String.fromCharCode(96).repeat(3) +
  "\n执行任务表：指定负责人与期限。";
const IMG_LINK=i=>"![插画](https://api.test/api/media/med_1234567890/abc"+i+")";
const FILE_LINK=(ext,i)=>"[📎 资料"+i+"."+ext+"](https://api.test/api/generated-files/pending/pgen_"+(i+1).toString(16).padStart(32,"0")+"/token"+String(i).padStart(16,"0")+")";
const frames=(text,includeDone=true)=>
"data: "+JSON.stringify({choices:[{index:0,delta:{role:"assistant"},finish_reason:null}]})+"\n\n"+
"data: "+JSON.stringify({choices:[{index:0,delta:{chat2api_status:{text:"正在分析"}},finish_reason:null}]})+"\n\n"+
"data: "+JSON.stringify({choices:[{index:0,delta:{content:text},finish_reason:null}]})+"\n\n"+
"data: "+JSON.stringify({choices:[{index:0,delta:{},finish_reason:"stop"}]})+"\n\n"+
(includeDone?"data: [DONE]\n\n":"");

function scenario(id, outputs, {corruptZip=false,modelMismatch=false,readerFailure=false,recoveredText=null,fetchFailure=false,httpFailureStatus=0,simulatePagehide=false}={}) {
  const elements=new Map(), requests=[], records=[],downloads=[],clientEvidence=[];
  let submittedReport=null;
  let cancels=0, releases=0;
  const pageEvents=new Map(),docEvents=new Map();
  const element=key=>{
    if(!elements.has(key))elements.set(key,{
      value:"",disabled:false,textContent:"",style:{},handlers:{},
      addEventListener(name,callback){this.handlers[name]=callback},
      get selectedOptions(){return [{dataset:{keyName:"test"}}]}
    });
    return elements.get(key);
  };
  element("pgChatModel").value="default";
  element("pgChatKeyInput").value="sk-private-test";
  element("pgABCDSelect").value=id;
  const host={querySelector(){return {after(){}}}};
  let uuid=0, postIndex=0;
  const document={
    visibilityState:"visible",
    addEventListener(name,cb){docEvents.set(name,cb)},
    removeEventListener(name,cb){if(docEvents.get(name)===cb)docEvents.delete(name)},
    getElementById:key=>key==="playgroundChatPanel"?host:(key==="pgABCD"&&!elements.has(key)?null:element(key)),
    createElement(){return {style:{},innerHTML:"",querySelector(){return {after(){}}}}}
  };
  const fetch=async (url, options={})=>{
    if(url==="/v1/chat/completions"){
      if(fetchFailure){const error=Error("should never appear in client proof");error.name="AbortError";throw error;}
      if(httpFailureStatus)return {ok:false,status:httpFailureStatus,text:async()=>"Internal Server Error"};
      const body=JSON.parse(options.body);
      requests.push({url,body,headers:options.headers});
      const text=outputs[Math.min(postIndex++,outputs.length-1)];
      const data=typeof text==="string"?frames(text):frames(text.text,text.done!==false);
      return {ok:true,body:{getReader(){
        let read=false;
        return {read:async()=>{
          if(readerFailure){const error=Error("synthetic read error");error.name="AbortError";throw error;}
          if(simulatePagehide&&!read) {
            document.visibilityState="hidden";
            docEvents.get("visibilitychange")?.();
            pageEvents.get("pagehide")?.({persisted:false});
          }
          return read?{done:true}:(read=true,{done:false,value:new TextEncoder().encode(data)});
        },cancel:async()=>{cancels++;},releaseLock(){releases++;}};
      }}};
    }
    const link=String(url).replace(/\/status$/,"");
    const pending=link.match(/\/pending\/pgen_([a-f0-9]{32})\/token[0-9]+$/);
    const index=pending?parseInt(pending[1],16)-1:-1;
    const ext=index>=0?(id==="D"?"md":["xlsx","pptx","json","zip"][index%4]):"image";
    let bytes;
    if(ext==="image")bytes=Uint8Array.from([137,80,78,71,13,10,26,10,...Array(25).fill(1)]);
    else if(ext==="json")bytes=new TextEncoder().encode(JSON.stringify({schedule:"full",events:[1,2,3]}));
    else if(ext==="md")bytes=new TextEncoder().encode("# Characters\nDetails of the heroes.");
    else bytes=Uint8Array.from([80,75,3,4,...Array(25).fill(1)]);
    if(String(url).endsWith("/status")){
      const sha256=[...new Uint8Array(await webcrypto.subtle.digest("SHA-256",bytes))]
        .map(x=>x.toString(16).padStart(2,"0")).join("");
      return {ok:true,json:async()=>({status:"ready",filename:"资料"+index+"."+ext,
        bytes:bytes.length,sha256})};
    }
    downloads.push(String(url));
    if(corruptZip&&ext==="zip"){bytes=bytes.slice();bytes[10]^=1;}
    return {ok:true,arrayBuffer:async()=>bytes.buffer};
  };
  let claimSerial=0;
  const api=async (endpoint,options)=>{
    if(endpoint.endsWith("/abcd/preview"))return {scenes:Object.fromEntries("ABCD".split("").map(k=>[k,{scene:k,title:"新主题"+k+claimSerial,serial:claimSerial}]))};
    if(endpoint.endsWith("/abcd/claim")) {
      const scene=options.body.scene;
      const title="新主题"+scene+claimSerial++;
      const prompts=scene==="D"?["第一章 "+title,"续写第二章 "+title]:["写出完整"+scene+"方案 "+title];
      return {scene,title,serial:claimSerial-1,claim_id:options.body.claim_id,
        prompts,end_anchor:"执行任务表",keywords:[title],next:{title:"新主题"+scene+claimSerial}};
    }
    if(endpoint.includes("/api/admin/playground/requests/"))return {
      status:"completed",response_text:recoveredText??"",response_text_truncated:false,
      diagnostics:{network_stream_terminal_proof:true,network_stream_phase:"complete"}
    };
    if(endpoint.includes("/api/admin/requests/"))return {
      status:"completed",requested_model:"gpt-5.6-sol",
      served_model:modelMismatch?"gpt-6":"gpt-5.6-sol",
      model_verified:!modelMismatch,model_conflict:modelMismatch,
      diagnostics:{network_stream_terminal_proof:true,network_stream_phase:"complete"}
    };
    if(endpoint.endsWith("/abcd/transport-truth")){clientEvidence.push({
      ...options.body,keepalive:options.keepalive===true
    });return {};}
    if(endpoint.endsWith("/abcd/report")){submittedReport=options.body.report;return {};}
    if(endpoint.endsWith("/chat-runs"))records.push({kind:"start",body:options.body});
    if(endpoint.endsWith("/chat-records"))records.push({kind:"terminal",body:options.body});
    return {};
  };
  const context={
    document,fetch,api,crypto:{randomUUID:()=>("00000000-0000-0000-0000-"+String(++uuid).padStart(12,"0")),subtle:webcrypto.subtle},
    TextDecoder,TextEncoder,performance:{now:()=>1234},location:{origin:"https://api.test"},
    console,setTimeout:()=>0,Blob,URL,
    addEventListener(name,cb){pageEvents.set(name,cb)},
    removeEventListener(name,cb){if(pageEvents.get(name)===cb)pageEvents.delete(name)},
  };
  vm.runInNewContext(source,context);
  return {run:async()=>{
    await element("pgABCDRun").handlers.click();
    return {status:element("pgABCDNote").textContent,requests,records,downloads,cancels,releases,report:submittedReport,clientEvidence};
  }, element};
}

test("v312: pagehide persists authenticated keepalive facts without cancelling healthy SSE",async()=>{
  const r=await scenario("A",[FULL],{simulatePagehide:true}).run();
  assert.equal(r.requests.length,1,"a page lifecycle signal must never replay the model prompt");
  assert.equal(r.cancels,0,"pagehide telemetry must not cancel the active reader");
  assert.equal(r.report.turns[0].client_sse_truth.protocol_done_seen,true);
  assert.equal(r.report.turns[0].client_sse_truth.client_pagehide_seen,true);
  assert.equal(r.report.turns[0].client_sse_truth.client_visibility_hidden_seen,true);
  const pagehide=r.clientEvidence.find(e=>e.keepalive===true
    && e.client_sse_truth?.client_pagehide_seen===true);
  assert.ok(pagehide,"pagehide after response headers needs its own bounded, keepalive POST");
  assert.equal(pagehide.failed,false,"progress is not a failed terminal");
  assert.equal(pagehide.phase,"progress");
  assert.equal(pagehide.client_sse_truth.reader_cancel_requested,false);
  assert.equal(pagehide.client_sse_truth.response_headers_received,true);
  assert.equal(pagehide.client_sse_truth.client_pagehide_persisted,false);
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"passed");
});

test("A: real SSE terminal and full-length report is accepted",async()=>{
  const r=await scenario("A",[FULL]).run();
  assert.match(r.status,/协议和上游终态已证实/);
  assert.equal(r.requests.length,1);
  assert.equal(r.requests[0].body.stream,true);
  assert.equal(r.cancels,0,"completed SSE reader must not cancel its fetch");
  assert.equal(r.releases,1,"release reader lock to permit final HTTP body boundary");
  assert.equal(r.report.turns[0].client_sse_truth.protocol_done_seen,true);
  assert.equal(r.report.turns[0].client_sse_truth.protocol_stop_seen,true);
  assert.equal(r.report.turns[0].client_sse_truth.reader_cancel_requested,false);
  assert.equal(r.report.turns[0].client_sse_truth.reader_lock_released,true);
  assert.equal(r.report.turns[0].client_sse_truth.native_reader_eof_seen,false,
    "reading [DONE] stops before the underlying ASGI EOF; do not invent EOF proof");
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"passed");
  assert.match(r.requests[0].body.messages[0].content,/新主题A/);
});
test("Incomplete SSE without [DONE] must fail even with full content",async()=>{
  const r=await scenario("A",[{text:FULL,done:false}]).run();
  assert.match(r.status,/未通过/);
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"failed");
  assert.equal(r.cancels,1,"incomplete SSE must cancel the abandoned read");
  assert.equal(r.releases,0);
  assert.equal(r.report.turns[0].client_sse_truth.reader_cancel_requested,true);
  assert.equal(r.report.turns[0].client_sse_truth.native_reader_eof_seen,true);
  assert.equal(r.report.turns[0].client_sse_truth.protocol_stop_seen,true);
  assert.equal(r.report.turns[0].client_sse_truth.protocol_done_seen,false);
});
test("A: thrown browser reader error records class only, never fake EOF",async()=>{
  const r=await scenario("A",[FULL],{readerFailure:true}).run();
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"failed");
  const truth=r.report.turns[0].client_sse_truth;
  assert.equal(truth.reader_error_name,"AbortError");
  assert.equal(truth.reader_cancel_requested,true);
  assert.equal(truth.native_reader_eof_seen,false);
  assert.equal(truth.protocol_done_seen,false);
  assert.doesNotMatch(JSON.stringify(truth),/synthetic read error/);
});
test("A: failed SSE persists client reader/cancel evidence BEFORE recovery result",async()=>{
  const r=await scenario("A",[{text:FULL,done:false}]).run();
  const early=r.clientEvidence.find(x=>x.phase==="incomplete");
  assert.ok(early,"pre-recovery client truth must be recorded even without terminal ABCD report");
  assert.equal(early.client_sse_truth.protocol_done_seen,false);
  assert.equal(early.client_sse_truth.reader_cancel_requested,true);
  assert.equal(early.client_sse_truth.reader_cancel_reason,"MissingTerminal");
});
test("A: HTTP 500 response is reported as response-layer error with exact status",async()=>{
  const r=await scenario("A",[FULL],{httpFailureStatus:500}).run();
  const terminal=r.records.find(x=>x.kind==="terminal");
  assert.equal(terminal.body.status,"failed");
  assert.equal(r.report.turns[0].client_sse_truth.http_status,500);
  assert.equal(r.report.turns[0].client_sse_truth.fetch_error_name,"HTTPStatusError");
  assert.ok(r.clientEvidence.some(x=>x.client_sse_truth?.http_status===500));
});
test("A: premature SSE EOF recovers SAME request while retaining client cancel proof",async()=>{
  const r=await scenario("A",[{text:FULL,done:false}],{recoveredText:FULL}).run();
  assert.equal(r.requests.length,1,"recovery cannot issue a second model request");
  assert.equal(r.report.turns[0].recovered_after_disconnect,true);
  assert.equal(r.report.turns[0].client_sse_truth.protocol_done_seen,false);
  assert.equal(r.report.turns[0].client_sse_truth.protocol_stop_seen,true);
  assert.equal(r.report.turns[0].client_sse_truth.native_reader_eof_seen,true);
  assert.equal(r.report.turns[0].client_sse_truth.reader_cancel_requested,true);
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"warning");
});
test("A: fetch aborted before response headers keeps distinct proof",async()=>{
  const r=await scenario("A",[FULL],{fetchFailure:true}).run();
  const truth=r.report.turns[0].client_sse_truth;
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"failed");
  assert.equal(truth.response_headers_received,false);
  assert.equal(truth.fetch_error_name,"AbortError");
  assert.equal(truth.reader_cancel_requested,false);
  assert.equal(truth.native_reader_eof_seen,false);
  assert.doesNotMatch(JSON.stringify(truth),/should never appear/);
});
test("B: three distinct image files are fetched",async()=>{
  const r=await scenario("B",[IMG_LINK(1)+"\n"+IMG_LINK(2)+"\n"+IMG_LINK(3)]).run();
  assert.match(r.status,/协议和上游终态已证实/);
  assert.equal(r.downloads.length,3);
});
test("C: XLSX PPTX JSON ZIP links require real file downloads",async()=>{
  const r=await scenario("C",[["xlsx","pptx","json","zip"].map(FILE_LINK).join("\n")]).run();
  assert.match(r.status,/协议和上游终态已证实/);
  assert.equal(r.downloads.length,4);
});
test("D: second turn keeps prior messages",async()=>{
  const r=await scenario("D",[FILE_LINK("md",0)+"\n"+"新主题D0".repeat(200),"新主题D0的剧情延续".repeat(120)]).run();
  assert.match(r.status,/协议和上游终态已证实/);
  assert.equal(r.requests.length,2);
  assert.equal(r.requests[1].body.messages.length,3);
  assert.equal(r.records.filter(x=>x.kind==="terminal").length,2);
});

test("A: missing Python source cannot be promoted by unrelated Plain text fence",async()=>{
  const lost=FULL.replace(
    /python\nimport math\n\ndef revenue\(amount\):\n    value = float\(amount\)\n    return math\.floor\(value\)/,
    "text\nExpected revenue: 1245"
  );
  const r=await scenario("A",[lost]).run();
  assert.match(r.status,/未通过/);
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"failed");
  const check=r.report.checks.find(x=>x.label.includes("Python"));
  assert.equal(check.passed,false);
});

test("A: raw inline Python code without a proper fenced executable block is not 1:1",async()=>{
  const lost=FULL.replace(
    /`{3}python\nimport math\n\ndef revenue\(amount\):\n    value = float\(amount\)\n    return math\.floor\(value\)\n`{3}/,
    "`import math\ndef revenue(amount):\n return amount`"
  );
  const r=await scenario("A",[lost]).run();
  assert.match(r.status,/未通过/);
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"failed");
});

test("C: corrupted ZIP with valid magic and size must fail independent SHA256 verification",async()=>{
  const links=["xlsx","pptx","json","zip"].map(FILE_LINK).join("\n");
  const r=await scenario("C",[links],{corruptZip:true}).run();
  assert.match(r.status,/未通过/);
  assert.match(r.status,/SHA256/);
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"failed");
});

test("A: actual served-model mismatch cannot pass silently",async()=>{
  const r=await scenario("A",[FULL],{modelMismatch:true}).run();
  assert.match(r.status,/内容验收有警告/);
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"warning");
  assert.equal(r.report.turns[0].upstream_evidence.model_conflict,true);
  assert.equal(r.report.turns[0].upstream_evidence.requested_model,"gpt-5.6-sol");
  assert.equal(r.report.turns[0].upstream_evidence.served_model,"gpt-6");
  assert.ok(r.report.warnings.some(x=>x.includes("实际服务模型")));
});
