import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync(new URL("../chrome_extension/content_request_v6.js",import.meta.url),"utf8");
const start=source.indexOf("  function renderedResponseBoundaryEvidence(active, candidate");
const end=source.indexOf("  // Only request-v6 consumes the evidence below.",start);
assert.ok(start>0&&end>start,"single request controller contains structural scan");
const fn=runInNewContext(source.slice(start,end)+"\nrenderedResponseBoundaryEvidence",{
  visible:()=>true,
  normalize:value=>String(value).replace(/\s+/g," ").trim(),
  transientText:()=>false,
  Node:{DOCUMENT_POSITION_FOLLOWING:4},
});

function fixture({overflow=false}={}){
  const main={querySelectorAll:()=>overflow
    ? Array.from({length:1601},(_,i)=>make("overflow"+i,"row".repeat(50),i,"SECTION"))
    : [heading,intro,formula,table,code,tail]};
  function make(id,text,pos,tagName) {
    return {id,pos,tagName,innerText:text,textContent:text,
      classList:{contains:()=>false},
      closest:(s)=>s.includes("main")?main:null,
      contains(other){return this===other;},
      compareDocumentPosition(other){return pos<other.pos?4:2;},
    };
  }
  const heading=make("heading","城市植物诊所",1,"H2");
  const intro=make("intro","目标客群为家庭和社区植物爱好者。",2,"P");
  const formula=make("math","\\frac{收入-成本}{收入}",3,"MATH");
  const table=make("table","渠道|预算|负责人\n".repeat(14),4,"TABLE");
  const code=make("code","for month in months:\n print(month)\n".repeat(12),5,"PRE");
  const tail=make("tail","执行任务表",6,"H2");
  return {main,heading,intro,formula,table,code,tail,
    active:{promptNormalized:"请设计城市植物诊所的完整商业模型。",
      baselineRenderedText:new Map()}};
}
test("A scene: short heading, prose, LaTex and final heading are not silently discarded",()=>{
  const {active,code}=fixture();
  const result=fn(active,code);
  assert.deepEqual([...result.nodes].map(x=>x.id),
    ["heading","intro","math","table","code","tail"]);
  assert.equal(result.independent_count,5);
  assert.equal(result.scan_overflow,false);
});
test("Historical nodes in the pre-submit baseline are excluded",()=>{
  const {active,code,heading,intro}=fixture();
  const normalize=t=>t.replace(/\s+/g," ").trim();
  active.baselineRenderedText.set(heading,normalize(heading.innerText));
  active.baselineRenderedText.set(intro,normalize(intro.innerText));
  const result=fn(active,code);
  assert.deepEqual([...result.nodes].map(x=>x.id),
    ["math","table","code","tail"]);
});
test("A scene: more than structural scan limit is never silently truncated into success",()=>{
  const {active,code}=fixture({overflow:true});
  const result=fn(active,code);
  assert.equal(result.scan_overflow,true);
  assert.equal(result.scan_structural_node_count,1601);
  assert.equal(result.nodes.length,0);
});
test("Failure diagnostics and sole terminal owner retain scan overflow evidence",()=>{
  assert.match(source,/if \(coverage\.scan_overflow \|\| !coverage\.nodes\?\.length/);
  assert.match(source,/response_recovery_scan_overflow/);
  assert.match(source,/response_integrity_structural_scan_nodes/);
  assert.match(source,/rich\.captureFinalMarkdown\(wrapper\)/);
});
