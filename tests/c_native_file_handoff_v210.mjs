import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const mainCode=readFileSync("chrome_extension/native_file_main_v210.js","utf8");
const contentCode=readFileSync("chrome_extension/content_native_file_v210.js","utf8");
const request=readFileSync("chrome_extension/content_request_v6.js","utf8");
const manifest=JSON.parse(readFileSync("chrome_extension/manifest.json","utf8"));

async function harness({bytes=new Uint8Array([80,75,3,4,0,1])}={}) {
  let clicked=0, next=0;
  const listeners=new Map();
  const pageOrigin="https://chatgpt.com";
  const window={
    addEventListener(type,fn){const set=listeners.get(type)||new Set();set.add(fn);listeners.set(type,set);},
    removeEventListener(type,fn){listeners.get(type)?.delete(fn);},
    postMessage(data){
      queueMicrotask(()=>{
        for(const fn of [...(listeners.get("message")||[])])
          fn({source:window,origin:pageOrigin,data});
      });
    },
  };
  class FakeAnchor {
    constructor(){this.download="";this.href="";}
    getAttribute(name){return name==="href"?this.href:this[name]||"";}
    hasAttribute(name){return name==="download"&&Boolean(this.download);}
    click(){}
  }
  class FakeURL extends URL {}
  FakeURL.createObjectURL=()=> "blob:"+pageOrigin+"/"+(++next);
  FakeURL.revokeObjectURL=()=>{};
  const button={
    disabled:false,getAttribute(name){return name==="aria-label"?"Download":"";},
    parentElement:{parentElement:null,innerText:"Bookkeeping.xlsx",querySelectorAll:()=>[]},
    getBoundingClientRect:()=>({width:140,height:40}),
    click(){
      clicked++;
      const uri=FakeURL.createObjectURL(new Blob([bytes]));
      const anchor=new FakeAnchor();
      anchor.download="Bookkeeping.xlsx";anchor.href=uri;anchor.click();
    },
  };
  const scope={
    isConnected:true,
    closest:()=>({}),
    querySelectorAll(selector){return selector==="button,[role='button']"?[button]:[];},
  };
  const context={
    window,document:{addEventListener(){}},location:{origin:pageOrigin,href:pageOrigin+"/c/demo"},
    URL:FakeURL,HTMLAnchorElement:FakeAnchor,Blob,TextDecoder,TextEncoder,Uint8Array,
    crypto:{randomUUID:()=>"01234567-89ab-cdef-0123-456789abcdef"},
    btoa,atob,queueMicrotask,Date,Math,Set,Map,Array,Number,String,Object,
    setTimeout,clearTimeout,getComputedStyle:()=>({display:"block",visibility:"visible"}),
  };
  runInNewContext(mainCode,context);
  runInNewContext(contentCode,context);
  return {content:context.__CHAT2API_NATIVE_FILE_V210__,scope,clicked:()=>clicked};
}

test("current reply native click returns signed XLSX bytes and no stale replay",async()=>{
  const h=await harness();
  const one=await h.content.capture(h.scope,"req_cfile1");
  assert.equal(one.count,1);
  assert.equal(one.bytes,6);
  assert.match(one.text,/!\[📎 Bookkeeping\.xlsx\]\(data:application\/vnd/);
  const two=await h.content.capture(h.scope,"req_cfile1");
  assert.equal(two.count,1);
  assert.equal(h.clicked(),1);
  assert.equal(h.content.stats("req_another").count,0);
});

test("unverified scope never initiates a browser download",async()=>{
  const h=await harness();
  const result=await h.content.capture({...h.scope,closest:()=>null},"req_cfile2");
  assert.equal(result.count,0);
  assert.equal(h.clicked(),0);
});

test("file signature guard does not accept HTML relabeled XLSX",async()=>{
  const h=await harness({bytes:new TextEncoder().encode("<html>hello</html>")});
  const result=await h.content.capture(h.scope,"req_cfile3");
  assert.equal(result.count,0);
  assert.equal(result.error,"native-download-not-observed");
  assert.equal(h.clicked(),1);
});

test("native hook remains observational; v6 alone owns terminal and required files",()=>{
  assert.ok(!mainCode.includes("chat.completed")&&!contentCode.includes("chat.completed"));
  assert.match(request,/fileReplyBoundary\.replyScope, active\.requestId/);
  assert.match(request,/allRequiredFileTypesDelivered/);
  assert.match(request,/response_native_file_handoff/);
  assert.ok(manifest.content_scripts.some(item=>item.world==="MAIN"&&item.js.includes("native_file_main_v210.js")));
  assert.ok(manifest.content_scripts.some(item=>item.world!=="MAIN"&&item.js.includes("content_native_file_v210.js")));
});
