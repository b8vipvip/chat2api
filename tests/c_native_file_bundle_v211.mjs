import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const mainCode=readFileSync("chrome_extension/native_file_main_v210.js","utf8");
const contentCode=readFileSync("chrome_extension/content_native_file_v210.js","utf8");

async function harness(extensions=["xlsx","pptx","json"]) {
  let now=1_000_000, serial=0;
  const listeners=new Map(), origin="https://chatgpt.com";
  const window={
    addEventListener(type,fn){const set=listeners.get(type)||new Set();set.add(fn);listeners.set(type,set);},
    removeEventListener(type,fn){listeners.get(type)?.delete(fn);},
    postMessage(data){queueMicrotask(()=>{for(const fn of [...(listeners.get("message")||[])])
      fn({source:window,origin,data});});},
  };
  class FakeAnchor {
    constructor(){this.download="";this.href="";}
    getAttribute(name){return this[name]||"";}
    hasAttribute(name){return name==="download"&&Boolean(this.download);}
    click(){}
  }
  class FakeURL extends URL {}
  FakeURL.createObjectURL=()=> "blob:"+origin+"/"+(++serial);
  FakeURL.revokeObjectURL=()=>{};
  const buttons=extensions.map(ext=>{
    const filename="Project."+ext;
    const bytes=ext==="json"?new TextEncoder().encode('{"schedule":[]}'):
      ext==="md"?new TextEncoder().encode("# Verified project notes"):
      new Uint8Array([80,75,3,4,0,1,2,3]);
    return {
      disabled:false,getAttribute(name){return name==="aria-label"?"Download":"";},
      parentElement:{parentElement:null,innerText:filename,querySelectorAll:()=>[]},
      getBoundingClientRect:()=>({width:100,height:30}),
      click(){
        const uri=FakeURL.createObjectURL(new Blob([bytes]));
        const anchor=new FakeAnchor();anchor.download=filename;anchor.href=uri;anchor.click();
      },
    };
  });
  const scope={
    isConnected:true,closest:()=>({}),
    querySelectorAll(sel){return sel==="button,[role='button'],a[href],a[download]"?buttons:[];},
  };
  const clock=class extends Date {static now(){return now;}};
  const context={window,document:{addEventListener(){}},location:{origin,href:origin+"/c/demo"},
    URL:FakeURL,HTMLAnchorElement:FakeAnchor,Blob,TextDecoder,TextEncoder,Uint8Array,
    crypto:{randomUUID:()=>String(++serial).padStart(32,"0")},btoa,atob,queueMicrotask,
    Date:clock,Math,Set,Map,Array,Number,String,Object,DataView,
    setTimeout,clearTimeout,getComputedStyle:()=>({display:"block",visibility:"visible"})};
  runInNewContext(mainCode,context);
  runInNewContext(contentCode,context);
  return {capture:()=>context.__CHAT2API_NATIVE_FILE_V210__.capture(scope,"req_bundle"),
    advance(ms){now+=ms;}};
}

test("ZIP packaging is delayed, includes all three real signed files plus README",async()=>{
  const h=await harness();
  const first=await h.capture();
  assert.equal(first.count,3);
  assert.equal(first.synthesized_zip,false);
  h.advance(31000);
  const second=await h.capture();
  assert.equal(second.count,4);
  assert.equal(second.synthesized_zip,true);
  assert.ok(second.captured_types.includes("zip"));
  const match=second.text.match(/!\[📎 chat2api-artifacts\.zip\]\(data:application\/zip;base64,([A-Za-z0-9+/=]+)\)/);
  assert.ok(match,"actual ZIP data URI");
  const buffer=Buffer.from(match[1],"base64");
  assert.equal(buffer.readUInt32LE(0),0x04034b50);
  const names=[...buffer.toString("utf8").matchAll(/(?:Project\.(?:xlsx|pptx|json)|README\.txt)/g)]
    .map(m=>m[0]);
  assert.ok(["Project.xlsx","Project.pptx","Project.json","README.txt"].every(name=>names.includes(name)));
  assert.equal(buffer.readUInt32LE(buffer.length-22),0x06054b50);
});

test("incomplete originals never yield an invented ZIP",async()=>{
  const h=await harness(["xlsx","pptx"]);
  await h.capture();h.advance(31000);
  const result=await h.capture();
  assert.equal(result.count,2);
  assert.equal(result.synthesized_zip,false);
});

test("extra verified Markdown card must not block ZIP of XLSX PPTX JSON",async()=>{
  const h=await harness(["xlsx","pptx","json","md"]);
  const first=await h.capture();
  assert.equal(first.count,4);
  assert.equal(first.synthesized_zip,false);
  h.advance(31000);
  const final=await h.capture();
  assert.equal(final.count,5);
  assert.equal(final.synthesized_zip,true);
  assert.ok(final.captured_types.includes("md"));
  assert.ok(final.captured_types.includes("zip"));
  const match=final.text.match(/!\[📎 chat2api-artifacts\.zip\]\(data:application\/zip;base64,([A-Za-z0-9+/=]+)\)/);
  assert.ok(match);
  const originalFiles=Buffer.from(match[1],"base64").toString("utf8");
  assert.ok(originalFiles.includes("Project.xlsx"));
  assert.ok(originalFiles.includes("Project.pptx"));
  assert.ok(originalFiles.includes("Project.json"));
  assert.ok(!originalFiles.includes("Project.md"));
});
