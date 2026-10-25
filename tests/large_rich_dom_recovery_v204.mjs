import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync(new URL("../chrome_extension/content_request_v6.js",import.meta.url),"utf8");
const scanStart=source.indexOf("  function renderedResponseBoundaryEvidence(active, candidate) {");
const guardEnd=source.indexOf("  function renderedNetworkCorrelatedTurn(active) {",scanStart);
assert.ok(scanStart>0 && guardEnd>scanStart);
function scene({count=610,toolbarAt=count+1,oldCount=0}={}){
  const main={querySelectorAll:()=>blocks};
  const blocks=Array.from({length:count},(_,i)=>({
    id:"block-"+i,pos:i,tagName:i%23===0?"H2":i%11===0?"TABLE":i%7===0?"PRE":"P",
    innerText:"经营计划和路线模型第"+i+"项，包含成本、人工工时和复修目标。".repeat(3),
    textContent:"经营计划和路线模型第"+i+"项，包含成本、人工工时和复修目标。".repeat(3),
    classList:{contains:()=>false},
    closest(selector){return selector.includes("main")?main:null;},
    contains(other){return this===other;},
    compareDocumentPosition(other){return this.pos<other.pos?4:2;},
  }));
  const chosen=blocks[Math.floor(count/2)];
  const action={pos:toolbarAt,id:"final-answer-copy"};
  const promptNormalized="Unique request identifying the latest leather-repair business proposal";
  const active={networkStreamSeen:true,submitStartedAt:100,networkObservedAt:101,
    networkTerminalTransportErrorAt:150,networkStreamEndedWithoutTerminalAt:151,
    baselineRenderedText:new Map(blocks.slice(0,oldCount).map(n=>[n,n.innerText])),
    baselineFinishActions:new Set(),promptNormalized};
  const current={isNew:true,latest:chosen,reason:"verified-rendered-dom-baseline-v191",
    text:chosen.innerText};
  const code=source.slice(scanStart,guardEnd);
  const context={
    document:{},visible:()=>true,
    normalize:s=>String(s).replace(/\\s+/g," ").trim(),
    transientText:()=>false,
    Node:{DOCUMENT_POSITION_FOLLOWING:4},
    responseFinishActions:()=>[action],
    turnFollows:(left,right)=>Boolean(left.pos<right.pos),
    internalToolPayload:()=>false,promptStillPresent:()=>false,
  };
  const recover=runInNewContext(code+"\nverifiedRenderedAbortCandidate",context);
  return {result:recover(active,current),active,blocks,chosen};
}
test("TX03 609 uncovered blocks: all bounded new reply blocks remain mergeable",()=>{
  const {result,blocks}=scene();
  assert.ok(result,"a full bounded rich document must not be rejected by the old 155-block cap");
  assert.equal(result.coverage.scan_structural_node_count,610);
  assert.equal(result.coverage.nodes.length,610);
  assert.equal(result.coverage.nodes[0].id,blocks[0].id);
  assert.equal(result.coverage.nodes.at(-1).id,blocks.at(-1).id);
  assert.equal(result.finishActionCount,1);
});
test("An answer toolbar before the last section is never completion proof",()=>{
  assert.equal(scene({toolbarAt:600}).result,null);
});
test("Historical pre-submit blocks cannot leak into the new response",()=>{
  const {result}=scene({oldCount:17});
  assert.ok(result);
  assert.equal(result.coverage.nodes.length,593);
  assert.equal(result.coverage.nodes[0].id,"block-17");
});
test("The 1600 structural scan limit remains a refusal, not a truncated success",()=>{
  assert.equal(scene({count:1601}).result,null);
});
test("The sole terminal owner still revalidates full rich text and 8s stability",()=>{
  assert.match(source,/response_integrity_full_block_count: rendered\\.coverage\\.nodes\\.length/);
  assert.match(source,/Date\\.now\\(\\) - abortedRenderedCandidateSince >= 8000/);
  assert.match(source,/rich\\.captureFinalMarkdown\\(wrapper\\)/);
  assert.match(source,/if \\(coverage\\.scan_overflow \\|\\| !coverage\\.nodes\\?\\.length\\) return null/);
});
