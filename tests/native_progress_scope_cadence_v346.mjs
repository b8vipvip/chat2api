import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
const worker=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=worker.indexOf("  function shouldCaptureNativeProgress(");
const end=worker.indexOf("  async function monitor(active) {",start);
assert.ok(start>=0&&end>start);
let calls=[];
const fn=runInNewContext("("+worker.slice(start,end).trim()+")",{
  native:{capture:async(_active,final)=>calls.push(final)},
});
const candidate={isConnected:true};
const scope={isConnected:true,contains(node){return node===candidate;}};
const started=1000000;
const active={
  requestId:"req_testprogresstelemetryv346",submitStartedAt:started,
  networkStreamSeen:true,nativeVerifiedReplyScope:scope,
  nativeProgressCaptureAt:started,
};
const at=elapsed=>started+elapsed;
assert.equal(fn(active,{isNew:true,latest:candidate},at(14999)),false);
assert.equal(fn(active,{isNew:true,latest:candidate},at(15000)),true);
assert.equal(fn(active,{isNew:true,latest:candidate},at(25000)),true,
  "caller sets nativeProgressCaptureAt atomically before scheduling upload");
active.nativeProgressCaptureAt=at(15000);
assert.equal(fn(active,{isNew:true,latest:candidate},at(29999)),false);
assert.equal(fn(active,{isNew:true,latest:candidate},at(30000)),true);
assert.equal(fn({...active,networkStreamSeen:false},{isNew:true,latest:candidate},at(30000)),false);
assert.equal(fn({...active,completed:true},{isNew:true,latest:candidate},at(30000)),false);
assert.equal(fn({...active,cancelled:true},{isNew:true,latest:candidate},at(30000)),false);
assert.equal(fn({...active,nativeTransferInFlight:true},{isNew:true,latest:candidate},at(30000)),false);
assert.equal(fn({...active,nativeVerifiedReplyScope:{isConnected:false}},{isNew:true,latest:candidate},at(30000)),false);
assert.equal(fn({...active,nativeVerifiedReplyScope:{isConnected:true,contains:()=>false}},{isNew:true,latest:candidate},at(30000)),false,
  "unrelated user, historical or sibling assistant DOM must not be captured");
assert.equal(fn(active,{isNew:false,latest:candidate},at(30000)),false);
assert.equal(fn(active,{isNew:true,latest:{isConnected:false}},at(30000)),false);
assert.equal(fn({...active,requestId:""}, {isNew:true,latest:candidate},at(30000)),false);
assert.equal(fn({...active,submitStartedAt:0}, {isNew:true,latest:candidate},at(30000)),false);
const monitor=worker.slice(end,worker.indexOf("  function ",end+20)>0
  ?worker.indexOf("  function ",end+20):undefined);
assert.ok(worker.includes("if(shouldCaptureNativeProgress(active,current,nativeProgressNow))"));
assert.ok(worker.includes("const pending=native.capture(active,false);"),
  "one and only one controller starts an optional partial native snapshot");
assert.ok(!worker.includes("await native.capture(active,false)"),
  "progress snapshot may not hold up the page monitor or sole request terminal");
assert.ok(worker.includes("await native.capture(active, true);"),
  "the final authoritative snapshot must still wait for its own committed ACK");
console.log("v346: bound request-owned partial DOM snapshots without a second terminal");
