import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync(new URL("../chrome_extension/content_request_v6.js",import.meta.url),"utf8");
const begin=source.indexOf("  function renderedResponseBoundaryEvidence(active, candidate) {");
const end=source.indexOf("  // Only request-v6 consumes the evidence below.",begin);
assert.ok(begin>0&&end>begin);
function fixture() {
  const main={querySelectorAll:()=>[story,table,code]};
  function make(id,value,pos){
    return {
      id, pos, innerText:value, textContent:value,
      closest:()=>main, contains(other){return this===other;},
      compareDocumentPosition(other){return pos<other.pos?4:2;},
    };
  }
  const story=make("story","创业项目商业分析与运营计划".repeat(35),1);
  const table=make("table","季度营收 | 客流 | 成本\n".repeat(20),2);
  const code=make("code","from dataclasses import dataclass\n".repeat(45),3);
  const active={
    baselineRenderedText:new Map(),
    promptNormalized:"这是本轮用户输入而不属于AI回复",
  };
  const func=runInNewContext(
    source.slice(begin,end)+"\nrenderedResponseBoundaryEvidence",
    {visible:()=>true,normalize:s=>String(s).replace(/\s+/g," ").trim(),transientText:()=>false,
     Node:{DOCUMENT_POSITION_FOLLOWING:4}});
  return {func,active,story,table,code};
}
test("TX03 A: stable Python child must expose substantial uncovered report blocks",()=>{
  const {func,active,story,table,code}=fixture();
  const proof=func(active,code);
  assert.equal(proof.independent_count,2);
  assert.equal(proof.nodes.length,3);
  assert.equal(proof.nodes[0].id,"story");
  assert.equal(proof.nodes[1].id,"table");
  assert.equal(proof.nodes[2].id,"code");
  assert.ok(proof.independent_chars>1000);
  assert.equal(proof.complete_scope,false);
});
test("Pre-submit blocks cannot be mistaken for new answer components",()=>{
  const {func,active,story,table,code}=fixture();
  active.baselineRenderedText.set(story,story.innerText);
  active.baselineRenderedText.set(table,table.innerText);
  const proof=func(active,code);
  assert.equal(proof.independent_count,0);
  assert.equal(proof.nodes.length,1);
});
test("Abort recovery serializes all changed pieces as one Markdown response",()=>{
  assert.match(source,/for \(const part of parts\) wrapper\.appendChild\(part\.cloneNode\(true\)\)/);
  assert.match(source,/rich\.captureFinalMarkdown\(wrapper\)/);
  assert.match(source,/response_integrity_merged_blocks: parts\.length/);
  assert.match(source,/response_recovery_uncovered_chars/);
  assert.match(source,/Date\.now\(\) - abortedRenderedCandidateSince >= 8000/);
  assert.match(source,/!isGenerating\(\)/);
});
