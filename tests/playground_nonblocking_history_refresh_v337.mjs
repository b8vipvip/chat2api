// A presentation-only Test History reload may never settle. The real model
// POST must start once the durable chat-run row is acknowledged, not later.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const code=readFileSync("app/admin_playground_chat_records_v3.js","utf8");
const RID="req_playgroundnonblockv337";
const prompt={model:"gpt-5.6-sol",messages:[{role:"user",content:"Sample"}],stream:true};
const options={
  method:"POST",
  headers:{"X-Chat2API-Request-ID":RID,"X-Chat2API-Playground-Transport":"abcd-real-sse-v293"},
  body:JSON.stringify(prompt),
};
const setup=({rowHttpStatus=200,onRefresh=null}={})=>{
  const events=[];
  const document={
    getElementById(name) {
      if(name==="pgChatKey") return {
        value:"key-a",selectedOptions:[{dataset:{keyName:"test-key"},textContent:"test-key"}]};
      if(name==="pgChatKeyInput")return {value:""};
      return null;
    },
  };
  const ctx={
    URL,Request,Headers,Promise,document,
    location:{href:"https://chat2api.example/admin",origin:"https://chat2api.example"},
    fetch:async(input,init)=>{
      const url=String(input);
      if(url==="/api/admin/playground/chat-runs"){
        events.push("durable-test-create");
        const body=JSON.parse(init.body);
        assert.equal(body.request_id,RID);
        assert.equal(body.model,"gpt-5.6-sol");
        assert.equal(body.transport_contract,"abcd-real-sse-v293");
        return {ok:rowHttpStatus===200,status:rowHttpStatus,
          text:async()=> "row failure",
          json:async()=>({run:{run_id:"chatrun_abc"}})};
      }
      if(url==="/v1/chat/completions"){
        events.push("model-request-dispatched");
        return {ok:true,marker:"production-model-response"};
      }
      throw Error("Unexpected request "+url);
    },
    loadTests:()=>{
      events.push("presentation-reload");
      return typeof onRefresh==="function"?onRefresh():Promise.resolve();
    }
  };
  ctx.globalThis=ctx;
  runInNewContext(code,ctx);
  return {ctx,events};
};

const hanging=setup({onRefresh:()=>new Promise(()=>{})});
const response=await Promise.race([
  hanging.ctx.fetch("/v1/chat/completions",options),
  new Promise((_,reject)=>setTimeout(()=>reject(Error(
    "real model fetch blocked on optional list-refresh API")),400)),
]);
assert.equal(response.marker,"production-model-response");
assert.deepEqual(hanging.events.slice(0,2),[
  "durable-test-create","presentation-reload"
].includes(hanging.events[1])?[
  "durable-test-create",hanging.events[1]
]:["durable-test-create","model-request-dispatched"]);
assert.equal(hanging.events.filter(x=>x==="durable-test-create").length,1);
assert.equal(hanging.events.filter(x=>x==="model-request-dispatched").length,1);
assert.ok(hanging.events.includes("presentation-reload"));
assert.ok(hanging.events.indexOf("durable-test-create") <
  hanging.events.indexOf("model-request-dispatched"));

const failingRefresh=setup({onRefresh:()=>Promise.reject(Error("unmounted view"))});
const result=await failingRefresh.ctx.fetch("/v1/chat/completions",options);
assert.equal(result.marker,"production-model-response");
assert.equal(failingRefresh.events.filter(x=>x==="model-request-dispatched").length,1);
// Let the presentation-only promise reject; no unhandled rejection is allowed.
await new Promise(resolve=>setImmediate(resolve));

const failedRow=setup({rowHttpStatus:500});
await assert.rejects(()=>failedRow.ctx.fetch("/v1/chat/completions",options),/测试记录创建失败/);
assert.deepEqual(failedRow.events,["durable-test-create"],
  "never fire a real model request if its test-run row was not persisted");
console.log("v337 durable chat-run creation remains mandatory, test-history refresh never blocks model dispatch");
