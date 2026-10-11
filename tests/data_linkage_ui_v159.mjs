import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const presentation = fs.readFileSync(new URL("../app/admin_worker_presentation_v66.js",import.meta.url),"utf8");
const context = {document:{documentElement:{dataset:{}}}};
vm.runInNewContext(presentation,context);
const truth=context.__CHAT2API_WORKER_PRESENTATION_V66__.liveWindowTruth;
const make=(raw,opts={})=>({
  client_id:"ext_a",standby_window_count:raw,live_verified:true,
  chatgpt_routing_ready:true,online:true,truth_status:"verified",...opts
});
assert.equal(truth({truth_revision:89,workers:[make(null)]}).byClient.get("ext_a").standby,null);
assert.equal(truth({truth_revision:89,workers:[make(null)]}).byClient.get("ext_a").liveVerified,false);
assert.equal(truth({truth_revision:89,workers:[make(undefined)]}).byClient.get("ext_a").standby,null);
assert.equal(truth({truth_revision:89,workers:[make(0)]}).byClient.get("ext_a").standby,0);
assert.equal(truth({truth_revision:89,workers:[make(3)]}).byClient.get("ext_a").standby,3);
assert.equal(truth({truth_revision:89,workers:[make(-1)]}).byClient.get("ext_a").standby,null);
assert.equal(truth({truth_revision:89,workers:[make(3,{chatgpt_routing_ready:false})]}).byClient.get("ext_a").standby,null);
assert.equal(truth({truth_revision:89,workers:[make(3,{live_verified:false})]}).byClient.get("ext_a").standby,null);
assert.equal(truth({truth_revision:89,workers:[make(3,{online:false})]}).byClient.get("ext_a").standby,null);
assert.equal(truth({truth_revision:88,workers:[make(3)]}).byClient.get("ext_a").standby,null);

const linux = fs.readFileSync(new URL("../app/admin_linux_device_authority_v124.js",import.meta.url),"utf8");
const start=linux.indexOf("  function chatgpt(worker,extension){");
const end=linux.indexOf("  const proxyView =",start);
assert.ok(start>=0&&end>start);
const chatgpt=new Function("bridge",linux.slice(start,end)+"\nreturn chatgpt;")(worker=>worker.metadata?.bridge||{});
const worker={worker_id:"wrk_a",enabled:true,chatgpt_status:"ready",
  metadata:{bridge:{login_state:"ready",composer_ready:true}}};
const extension={online:true,connection_enabled:true,metadata:{
  linux_worker_id:"wrk_a",chatgpt_login_state:"ready",chatgpt_login_composer_ready:true}};
assert.equal(chatgpt(worker,extension),"已登录");
assert.equal(chatgpt(worker,{...extension,metadata:{...extension.metadata,chatgpt_login_state:"login_required"}}),"未登录",
  "fresh Bridge login-required must override cached ready");
assert.equal(chatgpt(worker,{...extension,metadata:{...extension.metadata,chatgpt_login_composer_ready:false}}),"输入区未确认");
assert.equal(chatgpt(worker,{...extension,metadata:{...extension.metadata,chatgpt_login_state:"checking"}}),"检测中");
assert.equal(chatgpt({worker_id:"wrk_b",enabled:true,metadata:{}},null),"未确认");
assert.equal(chatgpt({...worker,enabled:false},extension),"已禁用");
assert.equal(chatgpt({...worker,revoked_at:"2026-01-01"},extension),"已禁用");

const unified=fs.readFileSync(new URL("../app/admin_unified_workers_v153.js",import.meta.url),"utf8");
assert.match(unified,/!meta\.linux_worker_id && !meta\.controller_worker_id/);
assert.doesNotMatch(unified,/platform!=="linux"/,
  "standalone Linux Chrome without an Agent binding must not disappear from both lists");
assert.match(unified,/c\.connection_enabled!==false/);
console.log("data linkage v159: unknown vs zero, auth precedence and platform guards passed");
