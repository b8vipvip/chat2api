import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

// Real background native-ACK code, not a mocked parser. The completed
// 35ce1dc deployment rejected ordinary snapshots because revision lived
// under snapshot.revision while the ACK bridge required event.revision.
const source=readFileSync("chrome_extension/background.js","utf8");
const start=source.indexOf("const pendingNativeAcks = new Map();");
const end=source.indexOf("function socketReady()",start);
assert.ok(start>0 && end>start);
const writes=[];
const socket={readyState:1,send:encoded=>writes.push(JSON.parse(encoded))};
const context={socket,WebSocket:{OPEN:1},setTimeout,clearTimeout};
runInNewContext(`
function socketReady(){return socket.readyState === WebSocket.OPEN;}
${source.slice(start,end)}
globalThis.bridge={
  key:nativeAckKey,
  send:awaitNativeAck,
  settle(event){
    const key=nativeAckKey(event);
    const pending=pendingNativeAcks.get(key);
    if(!pending || pending.socket !== socket)return false;
    clearTimeout(pending.timer);
    pendingNativeAcks.delete(key);
    pending.resolve({ok:event.ok===true,committed:event.committed===true,error:String(event.error||"")});
    return true;
  }
};`,context);
const req="req_v640snapshotackvalid";
const small={type:"chat.native.snapshot",request_id:req,
  snapshot:{version:1,revision:6,root:{tag:"p",text:"safe"}}};
const ack={type:"chat.native.ack",kind:"snapshot",request_id:req,revision:6,
  index:-1,ok:true,committed:true};
assert.equal(context.bridge.key(small),context.bridge.key(ack));
const pending=context.bridge.send(small);
assert.equal(writes.length,1,"Nested-revision snapshot must actually reach WebSocket");
assert.equal(writes[0].snapshot.revision,6);
assert.equal(context.bridge.settle(ack),true);
assert.equal((await pending).committed,true);
assert.equal(context.bridge.settle(ack),false,"duplicate ACK cannot settle a second time");

// Reject invalid shape before socket writes, without altering other requests.
const malformed=await context.bridge.send({...small,snapshot:{version:1,root:{tag:"div"}}});
assert.equal(malformed.ok,false);
assert.equal(writes.length,1);

// Chunk protocol still uses top-level revision and requires server completion
// on the final chunk, not an enqueue-only success.
const chunk={type:"chat.native.chunk",request_id:req,revision:7,
  index:0,count:1,byte_length:4,sha256:"a".repeat(64),data:"dGVzdA=="};
const chunkAck={type:"chat.native.ack",kind:"chunk",request_id:req,
  revision:7,index:0,ok:true,committed:true};
assert.equal(context.bridge.key(chunk),context.bridge.key(chunkAck));
const chunkPending=context.bridge.send(chunk);
assert.equal(writes.length,2);
assert.equal(context.bridge.settle(chunkAck),true);
assert.equal((await chunkPending).ok,true);

// Different revision cannot satisfy the pending revision.
const next=context.bridge.send({...small,snapshot:{...small.snapshot,revision:8}});
assert.equal(context.bridge.settle({...ack,revision:7}),false);
assert.equal(context.bridge.settle({...ack,revision:8}),true);
assert.equal((await next).committed,true);
console.log("Native ACK regression: nested snapshot revision, same-socket ACK, chunk revision isolation");
