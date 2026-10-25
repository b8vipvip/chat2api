import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync(new URL("../chrome_extension/content_request_v6.js", import.meta.url),"utf8");
const start=source.indexOf("  function verifiedRenderedAbortCandidate(active, current) {");
const end=source.indexOf("  function renderedNetworkCorrelatedTurn(active) {",start);
assert.ok(start>0 && end>start,"recovery guard must be in sole request-v6 controller");

function candidate({hasToolbar=true, after=true, baseline=false, aborted=true,
                    newDOM=true, size=1000, promptEcho=false, networkSeen=true}={}){
  const node={id:"new-response"};
  const action={id:"copy-reply"};
  const active={
    networkStreamSeen:networkSeen, submitStartedAt:100, networkObservedAt:101,
    networkTerminalTransportErrorAt:aborted?150:0,
    networkStreamEndedWithoutTerminalAt:aborted?151:0,
    baselineRenderedText:new Map(),baselineFinishActions:new Set(baseline?[action]:[]),
    promptNormalized:"A unique user prompt cannot be copied into an assistant answer.",
  };
  const current={isNew:newDOM, latest:node,
    reason:"verified-rendered-dom-baseline-v191",
    text:promptEcho?active.promptNormalized+" ".repeat(300):"商业执行方案".repeat(Math.ceil(size/6))};
  const ctx={
    responseFinishActions:()=>hasToolbar?[action]:[],
    turnFollows:(n,a)=>n===node&&a===action&&after,
    internalToolPayload:()=>false,
    promptStillPresent:()=>false,
  };
  const guard=runInNewContext(source.slice(start,end)+"\nverifiedRenderedAbortCandidate",ctx);
  return guard(active,current);
}
test("TX03 aborted 733KB stream: new 5006-char answer with fresh finished toolbar is recoverable",()=>{
  const result=candidate();
  assert.ok(result,"DOM may recover only with positive UI completion proof");
  assert.equal(result.finishActionCount,1);
});
test("No SSE abort, no new DOM, stale toolbar, or user-copy toolbar cannot finalize",()=>{
  for(const opts of [{aborted:false},{newDOM:false},{hasToolbar:false},
    {baseline:true},{after:false},{size:20},{promptEcho:true},{networkSeen:false}]){
    assert.equal(candidate(opts),null,JSON.stringify(opts));
  }
});
test("Controller never treats paused DOM or generic network bytes as a terminal",()=>{
  assert.match(source,/Date\.now\(\) - abortedRenderedCandidateSince >= 8000/);
  assert.match(source,/Date\.now\(\) - active\.networkObservedAt >= 10000/);
  assert.match(source,/response_terminal_source: "verified-aborted-stream-rendered-dom-v200"/);
  assert.match(source,/!confirmedTransportTerminal\(active\)/);
  assert.match(source,/response_recovery_finish_actions/);
  assert.match(source,/!isGenerating\(\)/);
  assert.ok(!/type:\\s*["\x27]chat\\.completed["\x27]/.test(
    readFileSync(new URL("../chrome_extension/content_network_stream_recovery_v55.js",import.meta.url),"utf8")
  ),"network observer cannot claim completion");
});
