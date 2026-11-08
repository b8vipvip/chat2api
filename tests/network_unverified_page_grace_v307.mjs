import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function unverifiedNetworkFailureReady(active, now,");
const end=source.indexOf("  function abortedReplyStableThreshold(rendered)",start);
assert.ok(start>0 && end>start,"v307 guard must be request-v6-owned");
const scope={};
runInNewContext(source.slice(start,end)+
  "\nthis.guard=unverifiedNetworkFailureReady;",scope);
const guard=scope.guard;
const ended=5000;
const state={
  networkStreamSeen:true,networkStreamEndedWithoutTerminalAt:ended,
  networkUnverifiedStreamId:"stream-current",
  networkStreamResponseId:"stream-current",
  networkTerminalAt:0,networkObservedAt:4000,
};
assert.equal(guard(state,ended+3001,true,false,false),false,
  "real req_157d: page Thinking cannot turn an aborted 20s fetch into chat.error after 3s");
assert.equal(guard(state,ended+60000,true,false,false),false,
  "a live Thinking page is not network terminal, even after long quiet");
assert.equal(guard(state,ended+60000,false,true,false),false,
  "explicit generating control retains the request");
assert.equal(guard(state,ended+19000,false,false,false),false,
  "give the single owner a bounded DOM/toolbar recovery window");
assert.equal(guard({...state,networkObservedAt:ended+16000},ended+22000,false,false),false,
  "new same-request text within the last 12 seconds resets failure decision");
assert.equal(guard(state,ended+22000,false,false,false),true,
  "idle unverified stream still fails closed, never fabricated completed");
assert.equal(guard({...state,networkStreamResponseId:"stream-new"},ended+60000,false,false),false,
  "stale ended stream may not terminate newer stream");
assert.equal(guard({...state,networkTerminalAt:ended+5},ended+60000,false,false),false,
  "a provisional SSE end-turn is handled by existing positive proof checks");
assert.equal(guard(state,ended+60000,false,false,true),false,
  "media-producing flow retains its own strict capture gate");
assert.equal(guard({...state,networkStreamSeen:false},ended+60000,false,false),false);
assert.ok(source.includes('unverified-network-await-page-terminal'));
assert.ok(source.includes('network_unverified_page_working: upstreamStillWorking()'));
assert.ok(source.includes('network_unverified_fail_closed_after_page_idle: true'));
assert.ok(!source.includes('Date.now() - active.networkStreamEndedWithoutTerminalAt >= 3000'));
console.log("v307 request-v6 only: Thinking and newer streams prevent 3s error, idle unknown still fails closed");
