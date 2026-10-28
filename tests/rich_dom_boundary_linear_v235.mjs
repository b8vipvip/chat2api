import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("chrome_extension/content_request_v6.js", "utf8");
const start = source.indexOf("  function renderedResponseBoundaryEvidence(active, candidate");
const end = source.indexOf("  // Only request-v6 consumes the evidence below.", start);
assert.ok(start > 0 && end > start);

let containmentChecks = 0;
const root = {querySelectorAll:()=>nodes};
const nodes = [];
let position = 0;
function element(text, tagName, parentElement) {
  const node = {
    tagName, innerText:text, textContent:text, parentElement,
    contains(other) {
      containmentChecks += 1;
      for (let cursor=other;cursor;cursor=cursor.parentElement)
        if(cursor===this) return true;
      return false;
    },
    closest(selector){return selector.includes("main")?root:null},
    compareDocumentPosition(other){return positionOf.get(this)<positionOf.get(other)?4:2},
  };
  positionOf.set(node,position++);
  return node;
}
const positionOf=new Map();
// 700 nested pairs = 1,400 distinct structural nodes, below the 1,600
// fail-closed scan boundary, matching a large mathematical/table reply.
for(let i=0;i<700;i++){
  const wrapper=element("section-"+i+" "+("report ".repeat(40)), "SECTION", root);
  const child=element("paragraph "+i+" "+("data ".repeat(10)), "P",wrapper);
  nodes.push(wrapper, child);
}
const candidate=element("current-request assistant paragraph", "P", root);
const active={baselineRenderedText:new Map(),promptNormalized:"unique user prompt"};
const normalize=value=>String(value||"").replace(/\s+/g," ").trim();
const scope=vm.runInNewContext(
  source.slice(start,end)+"\nthis.readBoundary=renderedResponseBoundaryEvidence;",
  {normalize,visible:()=>true,transientText:()=>false,Node:{DOCUMENT_POSITION_FOLLOWING:4}});
const actual=scope.readBoundary(active,candidate);
assert.equal(actual.scan_overflow,false);
assert.equal(actual.independent_count,700);
assert.equal(actual.nodes.length,701);
assert.equal(actual.nodes[actual.nodes.length-1],candidate);
assert.equal(actual.nodes.some(node=>node.tagName==="P"&&node!==candidate),false);
assert.ok(containmentChecks<3500,
  "candidate containment checks should be linear, not quadratic: "+containmentChecks);
// A pre-submit wrapper is excluded but a changed child remains detectable.
active.baselineRenderedText.set(nodes[0],normalize(nodes[0].innerText));
const after=scope.readBoundary(active,candidate);
assert.equal(after.independent_count,700);
assert.equal(after.nodes[0],nodes[1]);
assert.ok(source.includes("const coverageByScope = new Map()"));
assert.ok(source.includes("coverageByScope.set(scope, coverage)"));
assert.ok(!source.includes("const independent = changed.filter(node => !changed.some"));
console.log("v235 large rich reply boundary linear ancestry and complete-block preservation passed");
