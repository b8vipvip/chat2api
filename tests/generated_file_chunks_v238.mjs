import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {webcrypto} from "node:crypto";

const source=fs.readFileSync("chrome_extension/content_native_file_v210.js","utf8");
const begin=source.indexOf("  async function stageLargeFile(");
const end=source.indexOf("  async function capture(",begin);
assert.ok(begin>0&&end>begin);
const frames=[];
const context={
  atob,btoa,Uint8Array,crypto:{
    randomUUID:()=>"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    subtle:webcrypto.subtle},
  MIMES:{xlsx:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
  MAX_INLINE_BYTES:6*1024*1024,MAX_BYTES:12*1024*1024,
  UPLOAD_CHUNK_BYTES:96*1024,
  chrome:{runtime:{sendMessage:async m=>{frames.push(m.event);return{ok:true}}}}
};
vm.runInNewContext(source.slice(begin,end)+
  "\nthis.stageLargeFile=stageLargeFile;",context);
const length=6*1024*1024+140;
const binary="PK\x03\x04"+"a".repeat(length-4);
const file={filename:"budget.xlsx",bytes:length,
  data_url:"data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,"+
    btoa(binary)};
const uploaded=await context.stageLargeFile(file,"req_verified_chunk");
assert.equal(uploaded.stage_marker,"chat2api-staged:"+"a".repeat(32));
assert.ok(frames.length>60);
assert.ok(frames.every(m=>m.type==="chat.file.chunk"));
assert.ok(frames.every(m=>m.request_id==="req_verified_chunk"));
assert.equal(frames.at(-1).final,true);
assert.equal(frames.map(m=>atob(m.base64_chunk)).join(""),binary);
assert.equal(frames.at(-1).expected_sha256,frames[0].expected_sha256);
const oldFrames=frames.length;
context.chrome.runtime.sendMessage=async ()=>({ok:false});
const failure=await context.stageLargeFile(file,"req_chunk_rejected");
assert.equal(failure,null);
assert.equal(frames.length,oldFrames);
const controller=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");
assert.ok(controller.includes("chat2api-staged:"));
console.log("v238 6MiB+ artifact arrives as ordered bounded chunks, not a terminal Base64 body");
