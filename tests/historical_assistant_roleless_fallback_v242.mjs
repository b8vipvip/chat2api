import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const begin=source.indexOf("  function currentAssistantState(active) {");
const end=source.indexOf("\n  async function writePrompt(",begin);
assert.ok(begin>0 && end>begin,"real request-v6 currentAssistantState exists");
const historical={id:"old",parentElement:null,text:"Previous answer"};
const replacement={isNew:true,latest:{id:"new"},text:"Fresh answer",reason:"verified-rendered-dom-baseline-v191"};
let fallback=true,invocations=0;
const ctx={
 assistantNodes:()=>[historical],
 currentUserTurn:()=>null,
 turnOf:node=>node,
 nodeIdentity:node=>node.id,
 plainNodeText:node=>node.text,
 isGenerating:()=>false,
 turnFollows:()=>false,
 rolelessShortReplyCandidate:()=>null,
 renderedNetworkCorrelatedTurn:()=>null,
 renderedCurrentRequestCandidate:()=>{invocations++;return fallback?replacement:null;},
};
vm.runInNewContext(source.slice(begin,end)+"\nthis.current=currentAssistantState;",ctx);
const active={
 baselineIds:new Set(["old"]),baselineNodes:new Set([historical]),
 baselineLatestNode:historical,baselineLatestTurn:historical,
 baselineIdentity:"old",baselineLatestText:"Previous answer",
};
const recovered=ctx.current(active);
assert.equal(recovered.reason,"verified-rendered-dom-baseline-v191");
assert.equal(recovered.candidate_fallback_revision,242);
assert.equal(recovered.legacy_assistant_nodes_present,1);
assert.equal(invocations,1);
fallback=false;
assert.equal(ctx.current(active).reason,"historical-turn");
assert.equal(ctx.current(active).isNew,false);
ctx.nodeIdentity=()=> "fresh";
const newlyIdentified=ctx.current(active);
assert.equal(newlyIdentified.reason,"new-turn-identity");
assert.equal(newlyIdentified.isNew,true);
console.log("v242 historical assistant nodes cannot mask a fresh roleless reply");
