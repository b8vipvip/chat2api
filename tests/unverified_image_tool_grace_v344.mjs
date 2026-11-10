import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

// Execute the EXACT production error gate rather than a rewritten mock.
// It is deliberately pure regarding the error verdict: no synthetic
// network finish, HTML toolbar, media bytes or Broker terminal is created.
const s=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=s.indexOf("  function unverifiedNetworkFailureReady(");
const stop=s.indexOf("  function abortedReplyStableThreshold(",start);
assert.ok(start>0&&stop>start);
const fn=runInNewContext("("+s.slice(start,stop).trim()+")",{
  confirmedTransportTerminal:()=>false,
});
const ended=1000000;
const baseline=()=>({
  networkStreamSeen:true,
  networkStreamEndedWithoutTerminalAt:ended,
  networkTerminalAt:0,
  networkTerminalTransportErrorAt:0,
  networkStreamResponseId:"tool-part-1",
  networkUnverifiedStreamId:"tool-part-1",
  networkObservedAt:ended-10000,
  lastCaptureAt:0,
});
const decision=(ms,{image=false,media=false,working=false,generating=false,record=baseline()}={})=>
  fn(record,ended+ms,working,generating,media,image);

assert.equal(decision(19999),false);
assert.equal(decision(20000),true,
  "text responses still fail closed after existing 20s quiet grace");
assert.equal(decision(20000,{image:true}),false,
  "three-image task must not fail before async image tool has started");
assert.equal(decision(46000,{image:true}),false,
  "reproduce 2026-11-10 B: a 3.6KB early stream and quiet page at 46s");
assert.equal(decision(169000,{image:true}),false,
  "2026-11-10 C files actually took 169s; image tool must get analogous grace");
assert.equal(decision(179999,{image:true}),false);
assert.equal(decision(180000,{image:true}),true,
  "silent image task must still fail closed after a finite three-minute grace");
assert.equal(decision(240000,{image:true,working:true}),false);
assert.equal(decision(240000,{image:true,generating:true}),false);
assert.equal(decision(240000,{image:true,media:true}),false,
  "verified file/image media handoff retains its existing guarded path");
assert.equal(decision(240000,{image:true,record:{
  ...baseline(),networkStreamResponseId:"tool-part-2",
}}),false,"new same-request stream must prevent the old one declaring failure");
assert.equal(decision(240000,{image:true,record:{
  ...baseline(),networkObservedAt:ended+239000,
}}),false,"late same-request network progress keeps the request alive");
assert.equal(decision(240000,{image:true,record:{
  ...baseline(),lastCaptureAt:ended+239000,
}}),false,"late same-request DOM capture keeps the request alive");
assert.ok(s.includes("generating, captureMedia, imageCreationRequested)"),
  "production caller must pass image creation intent into the one error gate");
assert.ok(s.includes("network_unverified_idle_failure_grace_ms"),
  "request forensic evidence must distinguish an image grace from a text grace");
console.log("v344: multi-stage image creation waits at most 180s for same-request tool evidence; no false terminal");
