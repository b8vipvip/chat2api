import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
const source=readFileSync("chrome_extension/content_probe_supervisor_v1.js","utf8");
const a=source.indexOf("    const freshAssistantHeadings=[...scope.querySelectorAll(\"h4\")].filter(node =>");
const b=source.indexOf("    const stream = String(active?.networkObservedText || \"\");",a);
assert.ok(a>0&&b>a);
const fragment=source.slice(a,b);
function fixture(){
 const root={};
 const replyScope={parentElement:root};
 const otherScope={parentElement:root};
 function node(pos,outer=otherScope,label="") {
  return {pos,isConnected:true,parentElement:outer,innerText:label,
   compareDocumentPosition(x){return x.pos>pos?4:0;}};
 }
 const heading=node(10,otherScope,"ChatGPT said:");
 const current=node(12,replyScope);
 const inside=node(13,replyScope);
 const after=node(25);
 const prior=node(3);
 const toolbar=node(20,replyScope);
 root.querySelectorAll=(selector)=>selector==="h4"?[heading]:[];
 const scope=root;
 const active={baselineRolelessHeadings:new Set()};
 const uncovered=[prior,inside,after];
 const context={scope,active,uncovered,current,freshActions:[toolbar],
  safeVisible:()=>true,text:n=>n.innerText||"",Node:{DOCUMENT_POSITION_FOLLOWING:4}};
 vm.runInNewContext(fragment+"\nthis.proof={headings:freshAssistantHeadings.length,afterHeading:afterHeading.length,beforeAction:beforeLastAction.length,between:activeUncovered.filter(node=>follows(latestHeading,node)&&follows(node,lastFreshAction)).length,afterAction:activeUncovered.filter(node=>follows(lastFreshAction,node)).length,shared:sharedCandidate.length};",context);
 return context.proof;
}
test("read-only topology identifies blocks before and after assistant toolbar",()=>{
 const evidence=fixture();
 assert.equal(evidence.headings,1);
 assert.equal(evidence.afterHeading,2);
 assert.equal(evidence.beforeAction,2);
 assert.equal(evidence.between,1);
 assert.equal(evidence.afterAction,1);
 assert.equal(evidence.shared,1);
});
test("probe includes only bounded counters, not assistant HTML or private reply content",()=>{
 assert.match(source,/scene_uncovered_between_heading_toolbar_count/);
 assert.match(source,/scene_uncovered_shared_bounded_ancestor_count/);
 assert.ok(!fragment.includes("outerHTML"));
 assert.ok(!fragment.includes("innerHTML"));
 assert.ok(!fragment.includes("postMessage"));
 assert.ok(!fragment.includes("chat.completed"));
});
