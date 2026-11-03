import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import test from "node:test";

const source=readFileSync("app/admin_worker_tools_v1.js","utf8");
class Element {
  constructor(tag="div",text="") {
    this.tagName=tag.toUpperCase();this.children=[];this.ownText=text;
    this.dataset={};this.style={};this.listeners={};this.disabled=false;
    this.classList={contains:()=>false};
  }
  append(...nodes) { for(const node of nodes){node.parent=this;this.children.push(node);} }
  replaceChildren(...nodes) { for(const node of this.children)node.parent=null;this.children=[];this.ownText="";this.append(...nodes); }
  set textContent(value) {this.replaceChildren();this.ownText=String(value);}
  get textContent() {return this.ownText+this.children.map(x=>x.textContent).join("");}
  get isConnected() {return this.tagName==="BODY"||Boolean(this.parent?.isConnected);}
  addEventListener(type,callback) {this.listeners[type]=callback;}
  click() {if(!this.disabled)return this.onclick?.();}
  querySelectorAll(selector) {
    const nodes=[];
    for(const child of this.children){
      if(selector==="button[data-native-export]"&&child.tagName==="BUTTON"&&child.dataset.nativeExport)nodes.push(child);
      nodes.push(...child.querySelectorAll(selector));
    }
    return nodes;
  }
}
function archive(size=1024) {const bytes=new Uint8Array(size);bytes.set([80,75,3,4]);return bytes;}
function job(index=1,size=1024) {return {job_id:String(index).padStart(32,"0"),status:"completed",percent:100,
  request_id:"",target_type:"conversation_url",target_label:"history",worker_id:"wrk_test0001",tool_version:"1.3.0",size};}
function deferred() {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function harness(rows=[job()],download=()=>new Response(archive(),{headers:{"Content-Length":"1024"}})) {
  const root=new Element();const body=new Element("body");body.append(root);
  const ids=new Map([["view-worker-tools",root]]);
  for(const id of ["workerToolsScope","workerToolsEnableAbcd","workerToolsSave","workerToolsReload","workerToolsRefreshRuns",
    "workerToolsInspect","workerToolsDownload","nativePageStatus","nativePageDownloads","nativePageRecords","nativePageRequestId",
    "nativePageStart","nativePageVersion","nativePageUpdate","nativePageReload"]){const e=new Element();ids.set(id,e);root.append(e);}
  const timers=new Map(),objects=new Map(),revoked=[],saves=[],requests=[],events={};let sequence=0;
  const document={body,getElementById:id=>ids.get(id)||null,querySelector:()=>null,
    createElement:tag=>{const e=new Element(tag);if(tag==="a")e.click=()=>{assert.ok(e.isConnected,"save anchor must be attached");assert.ok(objects.has(e.href),"save URL must be alive");saves.push({url:e.href,name:e.download});return e.onclick?.();};return e;},
    createTextNode:text=>new Element("#text",text)};
  const context={document,location:{hash:""},sessionStorage:{getItem:()=>"test-admin-key"},key:()=>"test-admin-key",
    window:{addEventListener:(type,fn)=>{events[type]=fn;}},Blob,Uint8Array,AbortController,
    URL:{createObjectURL:blob=>{const url="blob:local/test-"+(++sequence);objects.set(url,blob);return url;},
      revokeObjectURL:url=>{revoked.push(url);objects.delete(url);}},
    setTimeout:(fn,ms)=>{const id=++sequence;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
    fetch:async(path,options)=>{requests.push({path,options});assert.equal(options.headers.Authorization,"Bearer test-admin-key");
      assert.ok(!path.includes("test-admin-key"));
      return path.includes("?limit=40")?new Response(JSON.stringify({data:rows})):download(path,options);},
  };
  runInNewContext(source,context);
  return {ids,objects,revoked,saves,requests,events,
    reload:()=>ids.get("nativePageReload").listeners.click(),
    button:(index=1,format="zip")=>root.querySelectorAll("button[data-native-export]").find(e=>e.dataset.nativeExport===String(index).padStart(32,"0")+":"+format),
    timeout:ms=>{for(const [id,timer] of [...timers])if(timer.ms===ms){timers.delete(id);timer.fn();}},
    downloads:()=>requests.filter(x=>/\/(download|log)$/.test(x.path)),
  };
}

test("large download shows progress, survives polling, deduplicates and retains a user-click save link",async()=>{
  const bytes=archive(10*1024*1024),gate=deferred();let stream;
  const h=harness([job(1,bytes.length)],()=>gate.promise);
  await h.reload();const pending=h.button().click();
  assert.equal(h.button().disabled,true);assert.match(h.ids.get("nativePageDownloads").textContent,/正在获取/);
  await h.reload();assert.equal(h.button().disabled,true,"polling must preserve in-flight state");
  h.button().click();assert.equal(h.downloads().length,1,"duplicate click must not download again");
  gate.resolve(new Response(new ReadableStream({start:c=>{stream=c;}}),{headers:{"Content-Length":String(bytes.length)}}));
  await turn();stream.enqueue(bytes.subarray(0,512*1024));await turn();
  assert.match(h.ids.get("nativePageDownloads").textContent,/0\.5 MiB \/ 10\.0 MiB/);
  stream.enqueue(bytes.subarray(512*1024));stream.close();await pending;
  assert.equal(h.objects.size,1);assert.equal(h.saves.length,1);assert.equal(h.button().disabled,false);
  assert.match(h.button().textContent,/再次保存/);assert.match(h.ids.get("nativePageStatus").textContent,/没有出现下载/);
  h.timeout(3000);assert.equal(h.revoked.length,0,"old three-second cleanup must not revoke the fallback");
  const link=h.ids.get("nativePageDownloads").children[0].children.find(e=>e.tagName==="A");link.click();
  await h.button().click();assert.equal(h.saves.length,3);assert.equal(h.downloads().length,1,"saving again reuses received bytes");
  assert.equal(h.saves[0].name,"native-page-history-url-00000000-v1.3.0.zip");
  h.timeout(600000);assert.equal(h.objects.size,0);assert.equal(h.ids.get("nativePageDownloads").children.length,0);
});

test("HTTP error is visible and retry succeeds with the same row",async()=>{
  let attempts=0;const h=harness([job()],()=>++attempts===1
    ?new Response(JSON.stringify({detail:"Administrator authentication required"}),{status:401})
    :new Response(archive(),{headers:{"Content-Length":"1024"}}));
  await h.reload();await h.button().click();
  assert.equal(h.objects.size,0);assert.match(h.ids.get("nativePageDownloads").textContent,/authentication required/);
  assert.equal(h.button().textContent,"重试下载");assert.equal(h.button().disabled,false);
  await h.button().click();assert.equal(h.saves.length,1);assert.equal(attempts,2);
});

test("HTTP 200 with a truncated body cannot produce a fake successful ZIP",async()=>{
  const h=harness([job(1,2048)],()=>new Response(archive(1024),{headers:{"Content-Length":"2048"}}));
  await h.reload();await h.button().click();
  assert.equal(h.saves.length,0);assert.equal(h.objects.size,0);
  assert.match(h.ids.get("nativePageStatus").textContent,/未接收完整/);assert.equal(h.button().textContent,"重试下载");
});

test("HTTP 200 login HTML and an empty body are rejected",async()=>{
  for(const body of ["<html>login required</html>",""]){
    const h=harness([job(1,0)],()=>new Response(body));
    await h.reload();await h.button().click();
    assert.equal(h.saves.length,0);assert.equal(h.objects.size,0);assert.equal(h.button().textContent,"重试下载");
  }
});

test("a stalled fetch times out visibly instead of leaving the button inert",async()=>{
  const h=harness([job()],(_path,options)=>new Promise((_resolve,reject)=>{
    options.signal.addEventListener("abort",()=>reject(Object.assign(new Error("aborted"),{name:"AbortError"})),{once:true});
  }));
  await h.reload();const pending=h.button().click();await turn();h.timeout(120000);await pending;
  assert.match(h.ids.get("nativePageStatus").textContent,/下载超时/);assert.equal(h.button().disabled,false);
});

test("explicit cancel restores the retry action without saving partial bytes",async()=>{
  const h=harness([job()],(_path,options)=>new Promise((_resolve,reject)=>{
    options.signal.addEventListener("abort",()=>reject(Object.assign(new Error("aborted"),{name:"AbortError"})),{once:true});
  }));
  await h.reload();const pending=h.button().click();await turn();
  h.ids.get("nativePageDownloads").children[0].children.find(e=>e.tagName==="BUTTON").click();await pending;
  assert.match(h.ids.get("nativePageStatus").textContent,/下载已取消/);assert.equal(h.saves.length,0);
});

test("JSON log export uses the same progress and persistent save flow",async()=>{
  const raw=JSON.stringify({record:{status:"completed"}});
  const h=harness([job()],()=>new Response(raw,{headers:{"Content-Type":"application/json"}}));
  await h.reload();await h.button(1,"log").click();
  assert.equal(h.saves.length,1);assert.match(h.saves[0].name,/-diagnostic\.json$/);
  const blob=[...h.objects.values()][0];assert.deepEqual(JSON.parse(await blob.text()),JSON.parse(raw));
  assert.match(h.ids.get("nativePageDownloads").textContent,/保存日志/);
});

test("retained files are bounded and cleared on page exit",async()=>{
  const h=harness([job(1),job(2),job(3),job(4)]);
  await h.reload();for(let i=1;i<=4;i++)await h.button(i).click();
  assert.equal(h.objects.size,3);assert.equal(h.revoked.length,1);assert.equal(h.button(1).textContent,"导出诊断 ZIP");
  h.events.pagehide();assert.equal(h.objects.size,0);assert.equal(h.ids.get("nativePageDownloads").children.length,0);
});

test("oversized data is rejected before it creates a retained file",async()=>{
  const h=harness([job(1,19*1024*1024)]);
  await h.reload();await h.button().click();
  assert.equal(h.objects.size,0);assert.match(h.ids.get("nativePageStatus").textContent,/超出诊断包大小上限/);
});
