import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const request=readFileSync(new URL("../chrome_extension/content_request_v6.js",import.meta.url),"utf8");
const rich=readFileSync(new URL("../chrome_extension/content_rich_response_v69.js",import.meta.url),"utf8");
const shortStart=request.indexOf("  function verifiedShortAbortCandidate(active) {");
const shortEnd=request.indexOf("  function renderedNetworkCorrelatedTurn(active) {",shortStart);
assert.ok(shortStart>0 && shortEnd>shortStart);
const shortSource=request.slice(shortStart,shortEnd);

function shortCandidate({fresh=true,working=false,changed=true,source="目前没有一天的套餐",body="目前没有一天的套餐"}={}){
  const node={innerText:body,textContent:body,closest:()=>null};
  const toolbar={};
  const active={
    networkStreamSeen:true,networkTerminalTransportErrorAt:1,
    networkStreamEndedWithoutTerminalAt:1,
    networkObservedText:source,baselineRenderedText:new Map(
      changed?[]:[[node,body]]),
    baselineFinishActions:new Set(fresh?[]:[toolbar]),
    promptNormalized:"这次需要一天的套餐么",
  };
  const context={
    normalize:x=>String(x||"").trim(),
    document:{querySelectorAll:()=>[node]},
    responseFinishActions:()=>[toolbar],
    visible:()=>true,transientText:()=>false,
    turnFollows:()=>true,
    replyScopeForFinishAction:()=>({}),
    renderedResponseBoundaryEvidence:()=>({
      scan_overflow:false,independent_count:0,scan_structural_node_count:3,
    }),
    plainNodeText:x=>x.innerText,
    promptStillPresent:()=>false,
    internalToolPayload:()=>false,
    upstreamStillWorking:()=>working,
  };
  const fn=runInNewContext(shortSource+"\nverifiedShortAbortCandidate",context);
  return fn(active);
}
test("short answer requires current SSE, live scoped toolbar and unchanged working state",()=>{
  assert.equal(shortCandidate()?.shortTextVerified,true);
  assert.equal(shortCandidate({fresh:false}),null);
  assert.equal(shortCandidate({changed:false}),null);
  assert.equal(shortCandidate({working:true}),null);
  assert.equal(shortCandidate({source:"别的内容"}),null);
  assert.equal(shortCandidate({source:"好的"}),null);
});
test("short DOM terminal stays under the sole request-v6 8-second guard",()=>{
  assert.match(request,/verifiedShortAbortCandidate\(active\)/);
  assert.match(request,/Date\.now\(\) - abortedRenderedCandidateSince >= 8000/);
  assert.match(request,/&& !upstreamStillWorking\(\)/);
  assert.match(request,/verified-short-sse-rendered-answer-v207/);
});
const magicStart=rich.indexOf("  function validGeneratedFileBytes(filename, bytes) {");
const magicEnd=rich.indexOf("  function hasCandidateFile(root) {",magicStart);
assert.ok(magicStart>0 && magicEnd>magicStart);
const magic=runInNewContext(rich.slice(magicStart,magicEnd)+"\nvalidGeneratedFileBytes",
  {TextDecoder});
test("reject HTML preview falsely labeled as Office ZIP or PDF",()=>{
  const html=new TextEncoder().encode("<html><title>Preview</title></html>");
  assert.equal(magic("预算.xlsx",html),false);
  assert.equal(magic("演示.pptx",html),false);
  assert.equal(magic("交付.zip",html),false);
  assert.equal(magic("报告.pdf",html),false);
  assert.equal(magic("预算.xlsx",Uint8Array.from([80,75,3,4,9,9])),true);
  assert.equal(magic("数据.json",new TextEncoder().encode('{"valid":true}')),true);
  assert.equal(magic("数据.json",html),false);
});
test("real binary download data attributes take precedence over preview href",()=>{
  const start=rich.indexOf("  function recognizedFile(anchor) {");
  const end=rich.indexOf("  function validGeneratedFileBytes",start);
  const section=rich.slice(start,end);
  assert.ok(section.indexOf('getAttribute?.("data-download-url")') < section.indexOf('getAttribute?.("href")'));
  assert.match(rich,/file-signature-mismatch/);
});
