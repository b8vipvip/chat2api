import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
const request=readFileSync(new URL("../chrome_extension/content_request_v6.js",import.meta.url),"utf8");
const native=readFileSync(new URL("../chrome_extension/content_generation_liveness_v49.js",import.meta.url),"utf8");
const admin=readFileSync(new URL("../app/admin_playground_chat_v69.js",import.meta.url),"utf8");
const requestStart=request.indexOf("  function upstreamStillWorking() {");
const requestEnd=request.indexOf("  function conversationTurns()",requestStart);
assert.ok(requestStart > 0 && requestEnd > requestStart);
const guardSource=request.slice(requestStart,requestEnd);
function workGuard(kind, stop=false){
  const ctx={isGenerating:()=>stop,globalThis:{
    __CHAT2API_GENERATION_LIVENESS_V49__:{nativeStatus:()=>kind ? {kind} : null}}};
  return runInNewContext(guardSource + "\nupstreamStillWorking",ctx)();
}
test("ongoing reasoning without a visible Stop button forbids DOM terminal",()=>{
  assert.equal(workGuard("working"),true);
  assert.equal(workGuard("reasoning"),false);
  assert.equal(workGuard(null),false);
  assert.equal(workGuard(null,true),true);
});
test("DOM-only completed path rechecks live working status before emit",()=>{
  assert.match(request,/!generating && !upstreamStillWorking\(\)/);
  assert.match(request,/parts\.every\(node => turnFollows\(node, rendered\.finishAction\)\)\s*&& !upstreamStillWorking\(\)/);
  assert.match(request,/response_terminal_live_working_guard: true/);
  assert.match(request,/response_recovery_native_working: upstreamStillWorking\(\)/);
  const liveStart=native.indexOf("  function nativeStatus() {");
  const liveEnd=native.indexOf("  async function report(",liveStart);
  const body=native.slice(liveStart,liveEnd);
  assert.ok(body.indexOf("const statusPattern") < body.indexOf("if (Number(active?.submitStartedAt"),
    "live status must precede older reasoning summaries");
  assert.match(body,/思考\|分析\|推理\|生成\|处理/);
});
const mathStart=admin.indexOf("  function mathMarkup(tex, display = false) {");
const mathEnd=admin.indexOf("  function inlineMarkdown(value)",mathStart);
assert.ok(mathStart > 0 && mathEnd > mathStart);
const math=runInNewContext(admin.slice(mathStart,mathEnd)+"\nmathMarkup",{
  escHtml:text=>String(text).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
});
test("Chinese renewal-rate LaTeX becomes native fraction",()=>{
  const actual=math("续约率=\\frac{到期后成功续约的合约数}{同期到期合约数}",true);
  assert.match(actual,/<math[^>]*display="block"/);
  assert.match(actual,/<mfrac>/);
  assert.match(actual,/<mtext>到期后成功续约的合约数<\/mtext>/);
  assert.match(actual,/<mtext>同期到期合约数<\/mtext>/);
});
test("Math renderer supports inline scripts and fails closed for unsupported TeX",()=>{
  assert.match(math("N_t=N_{t-1}\\times r+A_t"),/<msub>/);
  assert.match(math("N_t=N_{t-1}\\times r+A_t"),/×/);
  assert.equal(math("\\html{<img onerror=evil>}"),null);
});
test("Request conversation and playground share the same rendered math",()=>{
  assert.match(admin,/function mathMarkup\(/);
  assert.match(admin,/source\.replace\(\/\\\\\\\(/);
  assert.match(admin,/renderMarkdown, mathMarkup/);
});
