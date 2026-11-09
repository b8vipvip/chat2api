import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  function unverifiedNetworkFailureReady(");
const end=source.indexOf("  function abortedReplyStableThreshold(",start);
assert.ok(start>0&&end>start,"request-v6 must own ONE unverified terminal gate");
const script=source.slice(start,end)+
  "\nthis.ready=unverifiedNetworkFailureReady;";
const ctx={confirmedTransportTerminal:active=>
    Number(active.networkTerminalTransportDoneAt||0)>0
    && active.networkTerminalTransportStreamId===active.networkStreamResponseId
    && !Number(active.networkTerminalTransportErrorAt||0)};
runInNewContext(script,ctx);
const NOW=100000;
const ACT={
  requestId:"req_same_request",networkStreamSeen:true,
  networkStreamResponseId:"stream-1",
  networkUnverifiedStreamId:"stream-1",
  networkStreamEndedWithoutTerminalAt:NOW-40000,
  networkObservedAt:NOW-40000,
  networkTerminalAt:NOW-41000,
  networkTerminalText:"short provisional first 34 chars",
  networkTerminalTransportErrorAt:NOW-39999,
  networkTerminalTransportDoneAt:0,
  networkTerminalTransportStreamId:"stream-1"
};
const test=(patch={},opts={})=>ctx.ready({...ACT,...patch},
  opts.now??NOW,opts.pageWorking??false,opts.generating??false,
  opts.captureMedia??false);
assert.equal(test(),true,
  "actual request: provisional assistant marker + aborted same SSE + quiet page must fail closed");
assert.equal(test({networkTerminalAt:0}),true,
  "ordinary missing-terminal reader abort still fails once idle");
assert.equal(test({networkTerminalTransportErrorAt:0}),false,
  "provisional hint with ongoing stream cannot be interrupted by the error gate");
assert.equal(test({
  networkTerminalTransportDoneAt:NOW-31000,
  networkTerminalTransportErrorAt:0
}),false,"verified clean EOF remains owned by the normal success reconciliation path");
assert.equal(test({networkStreamEndedWithoutTerminalAt:NOW-10000}),false,
  "never turn a short 10s network pause into a failure");
assert.equal(test({networkObservedAt:NOW-9000}),false,
  "a fresh same-request SSE / network update preserves generation");
assert.equal(test({lastCaptureAt:NOW-9000}),false,
  "a progressing same-request DOM after aborted network may still become the full reply");
assert.equal(test({lastCaptureAt:NOW-14000}),true,
  "a stale DOM checkpoint cannot hold a page that is otherwise idle forever");
assert.equal(test({networkUnverifiedStreamId:"stream-old"}),false,
  "a newer live stream supersedes any old stream error");
assert.equal(test({}, {pageWorking:true}),false,
  "ChatGPT still visibly working must never be aborted by provisional end marker");
assert.equal(test({}, {generating:true}),false,
  "newly submitted assistant generation remains alive while spinner/control works");
assert.equal(test({}, {captureMedia:true}),false,
  "image/file media transfer retains its independent, stricter finality gate");
assert.equal(test({networkStreamSeen:false}),false,
  "no observed SSE cannot be promoted to any network terminal");
assert.equal(test({networkStreamEndedWithoutTerminalAt:0}),false,
  "missing raw abort/EOF evidence cannot be converted into fail-closed terminal");
assert.equal(test({networkTerminalAt:0,networkTerminalTransportErrorAt:0}),true,
  "the original v307 missing-terminal without any provisional hint still applies");
assert.equal(source.includes("network_unverified_provisional_terminal_seen:"),true);
assert.equal(source.includes('response_integrity_guard: "verified-network-terminal-v314"'),true);
console.log("v314: aborted provisional assistant end-turn cannot hold browser lease for 915s; working/clean/new streams remain safe");
