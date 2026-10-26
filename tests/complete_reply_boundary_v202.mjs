import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync(new URL("../chrome_extension/content_request_v6.js",import.meta.url),"utf8");
const start=source.indexOf("  function verifiedRenderedAbortCandidate(active, current) {");
const end=source.indexOf("  function renderedNetworkCorrelatedTurn(active) {",start);
assert.ok(start>0&&end>start);

function attempt({completionIndex=1,blocks=2}={}){
  const root={contains:x=>x===action||content.includes(x),matches:()=>false,querySelector:()=>null};
  const content=Array.from({length:blocks},(_,i)=>({pos:i, id:"answer-block-"+i, parentElement:root}));
  const action={id:"finished-answer-toolbar",parentElement:root,getAttribute:()=>""};
  const active={
    networkStreamSeen:true,submitStartedAt:100,networkObservedAt:110,
    networkTerminalTransportErrorAt:150,networkStreamEndedWithoutTerminalAt:151,
    baselineRenderedText:new Map(),baselineFinishActions:new Set(),
    promptNormalized:"Test user prompt uniquely identifying this request",
  };
  const current={isNew:true,latest:content[0],reason:"verified-rendered-dom-baseline-v191",
    text:"This long complete reply has valid narrative, structured tables and code.".repeat(8)};
  const fn=runInNewContext(source.slice(start,end)+"\nverifiedRenderedAbortCandidate",{
    responseFinishActions:()=>[action],
    turnFollows:(left,right)=>right===action && left.pos < completionIndex,
    internalToolPayload:()=>false,promptStillPresent:()=>false,
    renderedResponseBoundaryEvidence:()=>({nodes:content,independent_count:blocks-1,
      candidate_chars:current.text.length,complete_scope:blocks===1}),
  });
  return fn(active,current);
}
test("A rich response: toolbar after code but before final checklist cannot complete",()=>{
  assert.equal(attempt({completionIndex:1,blocks:3}),null);
});
test("Toolbar following all 3 blocks permits candidate, preserving body order",()=>{
  const candidate=attempt({completionIndex:3,blocks:3});
  assert.ok(candidate);
  assert.deepEqual([...candidate.coverage.nodes].map(x=>x.id),
    ["answer-block-0","answer-block-1","answer-block-2"]);
});
test("Simple one-block response still qualifies",()=>{
  assert.ok(attempt({completionIndex:1,blocks:1}));
});
test("Request-v6 terminal must revalidate all parts just before commit",()=>{
  assert.match(source,/parts\.every\(node => turnFollows\(node, rendered\.finishAction\)\)/);
  assert.match(source,/response_integrity_scope: "same-answer-container-before-toolbar-v205"/);
  assert.match(source,/response_recovery_finish_after_all_blocks/);
  assert.ok(!/type:\s*["']chat\.completed["']/.test(
    readFileSync(new URL("../chrome_extension/content_probe_supervisor_v1.js",import.meta.url),"utf8")));
});
