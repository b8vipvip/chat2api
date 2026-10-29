import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const request=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");
const native=fs.readFileSync("chrome_extension/content_native_file_v210.js","utf8");
const first=request.indexOf("  function verifiedFileReplyBoundary(active, current) {");
const last=request.indexOf("\n  // A short real answer",first);
assert.ok(first>0 && last>first);
assert.match(request.slice(first,last),/fresh-roleless-rich-answer-v246/);
assert.match(request.slice(first,last),/verifiedRolelessRichAbortCandidate\(active, current\)/);
assert.match(request.slice(first,last),/replyScope:current.latest/);
assert.doesNotMatch(request.slice(first,last),/replyScope:current.rolelessRoot/);

{
 const scope={id:"replyDiv",isConnected:true,closest:()=>({})};
 const ctx={
   verifiedRolelessRichAbortCandidate:()=>({finishAction:{id:"copy-current"}}),
 };
 vm.runInNewContext(request.slice(first,last)+
   "\nthis.boundary=verifiedFileReplyBoundary;",ctx);
 const active={};
 const cur={reason:"fresh-roleless-rich-answer-v246",latest:scope};
 const bound=ctx.boundary(active,cur);
 assert.equal(bound.replyScope,scope);
 assert.equal(bound.boundaryEvidence,"v247-same-request-roleless-rich-DIV-with-fresh-toolbar");
 ctx.verifiedRolelessRichAbortCandidate=()=>null;
 assert.equal(ctx.boundary(active,cur),null,"without fresh verified finish toolbar, no file scope");
}

const begin=native.indexOf("  function candidateButtons(scope) {");
const end=native.indexOf("  function signedBytes(",begin);
assert.ok(begin>0&&end>begin);
const nativeFn=native.slice(begin,end);
function link(href,label,tag="A"){
 return {
   tagName:tag,disabled:false,textContent:label,
   getAttribute(k){return ({href,"aria-label":label,title:label})[k]||null;},
   hasAttribute:()=>false,
   closest:()=>null,
 };
}
const safe=link("/download/file.xlsx","演示文件.xlsx");
const blob=link("blob:https://chatgpt.com/12345","演示文件.xlsx");
const unsafe=link("https://outside.example/file.xlsx","演示文件.xlsx");
const script=link("javascript:alert(1)","演示文件.xlsx");
const plain=link("/settings","账户设置");
const ctx={
  MIMES:{xlsx:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
  location:{origin:"https://chatgpt.com",href:"https://chatgpt.com/c/123"},
  URL,visible:()=>true,filenames:n=>n.textContent.includes(".xlsx")?[n.textContent]:[],
  matchButtonFilename:n=>n.textContent.includes(".xlsx")?n.textContent:"",
};
vm.runInNewContext(nativeFn+"\nthis.candidates=candidateButtons;",ctx);
let nodes=[safe,blob,unsafe,script,plain];
const scope={querySelectorAll:()=>nodes};
const hits=ctx.candidates(scope);
assert.ok(hits.some(item=>item.button===safe),"same-origin file card available");
assert.ok(hits.some(item=>item.button===blob),"same-origin blob can be captured and signed");
assert.ok(!hits.some(item=>item.button===unsafe),"foreign domain cannot be clicked");
assert.ok(!hits.some(item=>item.button===script),"script URL cannot be clicked");
assert.ok(!hits.some(item=>item.button===plain),"non-file links cannot be clicked");
console.log("v247: file cards and whole current rich DIV eligible, cross-origin/stale excluded");
