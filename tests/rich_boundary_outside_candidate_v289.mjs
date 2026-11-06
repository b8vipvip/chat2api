import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function renderedResponseBoundaryEvidence(active, candidate");
const end=source.indexOf("  // Only request-v6 consumes the evidence",start);
assert.ok(start>0 && end>start, "extract actual rich boundary scanner");
const ctx={
  visible:()=>true,
  normalize:value=>String(value||"").replace(/\s+/g," ").trim(),
  transientText:()=>false,
  Node:{DOCUMENT_POSITION_FOLLOWING:4},
};
runInNewContext(source.slice(start,end)+"\nthis.scan=renderedResponseBoundaryEvidence;",ctx);

class Node {
  constructor(tag,text="",parent=null,pos=0) {
    this.tagName=tag;
    this.innerText=text;
    this.textContent=text;
    this.parentElement=parent;
    this.pos=pos;
    this.classList={contains:()=>false};
  }
  contains(other) {
    for(let n=other;n;n=n.parentElement)if(n===this)return true;
    return false;
  }
  closest(){return null;}
  compareDocumentPosition(other){return this.pos<other.pos?4:0;}
}
function fixture(inside,outside) {
  const scope=new Node("MAIN");
  const candidate=new Node("DIV","Complete assistant answer with full tables and images",scope,1);
  const descendants=Array.from({length:inside},(_,i)=>
    new Node(i%2?"TD":"P","A full paragraph or table cell belonging to the current answer",candidate,i+2));
  const independent=Array.from({length:outside},(_,i)=>
    new Node("P","A changed independent block whose ownership must be verified",scope,inside+i+2));
  scope.querySelectorAll=()=>[...descendants,...independent];
  const active={baselineRenderedText:new Map(),promptNormalized:"A unique submitted prompt"};
  return ctx.scan(active,candidate,scope);
}

const complete=fixture(4800,0);
assert.equal(complete.scan_structural_node_count,4800);
assert.equal(complete.scan_outside_candidate_node_count,0);
assert.equal(complete.scan_overflow,false,
  "many fully owned answer descendants must not consume independent scan quota");
assert.equal(complete.complete_scope,true);
assert.equal(complete.nodes.length,1);

const partial=fixture(4800,3);
assert.equal(partial.scan_structural_node_count,4803);
assert.equal(partial.scan_outside_candidate_node_count,3);
assert.equal(partial.scan_overflow,false);
assert.equal(partial.independent_count,3);
assert.equal(partial.complete_scope,false,
  "do not silently ignore three newly changed blocks outside the candidate");
assert.equal(partial.nodes.length,4);

const unsafe=fixture(4800,1601);
assert.equal(unsafe.scan_structural_node_count,6401);
assert.equal(unsafe.scan_outside_candidate_node_count,1601);
assert.equal(unsafe.scan_overflow,true);
assert.equal(unsafe.nodes.length,0,
  "unbounded unrelated nodes must remain fail-closed, not be sampled");

console.log("rich DOM 4800-node contained vs unowned overflow cases passed");
