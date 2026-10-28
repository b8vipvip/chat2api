import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const bg=fs.readFileSync("chrome_extension/background.js","utf8");
const start=bg.indexOf("const pendingFileChunkAcks = new Map();");
const end=bg.indexOf("async function sendSocket(",start);
assert.ok(start>0&&end>start);
const sent=[];
const webSocket={readyState:1,send(message){sent.push(JSON.parse(message));}};
const ctx={socket:webSocket,WebSocket:{OPEN:1},Map,Date,Promise,
  setTimeout,clearTimeout,Number,JSON,Error,String};
vm.runInNewContext(bg.slice(start,end)+
  "\nthis.waitAck=awaitFileChunkAck;this.pending=pendingFileChunkAcks;"+
  "this.key=fileChunkKey;this.reject=rejectPendingFileChunks;",ctx);
const packet={type:"chat.file.chunk",request_id:"req_origin",
  upload_id:"f".repeat(32),sequence:0,base64_chunk:"UEsDBA=="};
const p=ctx.waitAck(packet);
assert.equal(sent.length,1);
assert.equal(ctx.pending.size,1);
let done=false;
p.then(()=>done=true);
await Promise.resolve();
assert.equal(done,false,"WebSocket.send is not server storage confirmation");
const row=ctx.pending.get(ctx.key(packet));
assert.ok(row);
row.resolve({ok:true});
clearTimeout(row.timer);
ctx.pending.delete(ctx.key(packet));
assert.equal((await p).ok,true);
const pending=ctx.waitAck({...packet,sequence:1});
assert.equal(ctx.pending.size,1);
ctx.reject("socket-closed-before-server-ack");
assert.equal((await pending).ok,false);
assert.equal(ctx.pending.size,0);
assert.match(bg,/message\?\.type === "chat\.file\.chunk\.ack"/);
assert.match(bg,/if \(message\.type === "chat2api\.file\.chunk"\)/);
const main=fs.readFileSync("app/main.py","utf8");
assert.match(main,/if message_type == "chat\.file\.chunk":/);
assert.match(main,/"type": "chat\.file\.chunk\.ack"/);
assert.match(main,/generated_staging\.ingest/);
assert.match(main,/await websocket\.send_json\(\{/);
console.log("v238 Worker chunk awaits server persisted ack, errors fail closed");
