(() => {
"use strict";
const host=document.getElementById("playgroundChatPanel");
if(!host||document.getElementById("pgABCD"))return;
// Task content lives in the persistent server-side one-shot pool, never in the JS bundle.
const cases={A:{title:"旧住宅节能改造",prompts:[]},B:{title:"模块化收纳盒产品摄影",prompts:[]},C:{title:"社区屋顶菜园资料包",prompts:[]},D:{title:"图书馆离线预约网页",prompts:[]}};
const byId=id=>document.getElementById(id),note=s=>{
  const panel=byId("pgABCDNote");
  if(panel)panel.textContent=s;
};
// UI widgets can be unmounted by admin navigation while an SSE connection
// remains active. A progress display error is not a transport cancellation.
function safeProgress(update,chars,frames,status){
  try { update(chars,frames,status); } catch (_) { /* detached view */ }
}
const el=document.createElement("section");el.className="panel";el.id="pgABCD";el.style.margin="12px 0";
el.innerHTML='<h3>ABCD 真实场景验收</h3><p class="muted">通过已选业务Key和文本模型实际发起API流式请求。A检验全文结构、B检验3张图片、C检验4份文件、D检验可运行HTML与业务逻辑。每次运行将当前题目立即从持久化队列移除，自动换成全新主题；API SSE 完成不代表上游 ChatGPT 终态已验证。</p><div style="display:flex;flex-wrap:wrap;gap:8px;margin:10px 0"><select id="pgABCDSelect"></select><button class="action good" id="pgABCDRun">运行场景</button></div><p id="pgABCDNote" role="status" style="white-space:pre-wrap;line-height:1.6">尚未执行</p>';
host.querySelector(".pgChatMode")?.after(el);
byId("pgABCDSelect").innerHTML=Object.entries(cases).map(([id,s])=>'<option value="'+id+'">'+id+' · '+s.title+'</option>').join("");
let working=false,report=null;
async function refreshPool(){
  try {
    const selected=byId('pgABCDSelect').value;
    const data=await api('/api/admin/playground/abcd/preview');
    for(const [id,item] of Object.entries(data.scenes||{}))if(cases[id]) cases[id].title=item.title;
    byId('pgABCDSelect').innerHTML=Object.entries(cases).map(([id,item])=>'<option value="'+id+'">'+id+' · '+item.title+'</option>').join('');
    byId('pgABCDSelect').value=selected||'A';
  }catch(error){note('提示词池暂不可用：'+String(error.message||error));}
}
refreshPool();
async function key(){
const raw=byId("pgChatKeyInput").value.trim();
if(raw)return {token:raw,id:null,label:"手动粘贴"};
const id=byId("pgChatKey").value;if(!id)throw Error("请选择业务API Key");
const k=await api("/api/admin/keys/"+encodeURIComponent(id)+"/secret");
if(!k.token)throw Error("业务API Key无法恢复");
return {token:k.token,id,label:byId("pgChatKey").selectedOptions?.[0]?.dataset?.keyName||id};
}
// The browser/proxy can close this SSE HTTP connection 3-6 seconds after
// admission while the *same* Worker request remains in the Broker for minutes.
// Restore by authenticated request ID; never issue another model request or
// equate an HTTP disconnect with a Worker terminal.
async function recoverDisconnectedStream(requestId, update) {
  if (!/^req_[a-z0-9_-]{12,110}$/i.test(String(requestId||""))) return null;
  let observed=false, last=null, missed=0;
  for(let i=0;i<230;i++){
    try{
      last=await api("/api/admin/playground/requests/"+
        encodeURIComponent(requestId)+"/outcome?full_text=1");
      if(last?.status){
        observed=true;missed=0;
        const status=String(last.status);
        if(status==="completed"){
          const text=String(last.response_text||"");
          // A bounded diagnostic preview is NOT a full assistant reply.
          if(last.response_text_truncated===true || !text.trim())
            throw Error("服务端回复无法完整恢复，不能按已交付处理");
          return {text,frames:0,recovered:true};
        }
        if(!["running","queued","pending"].includes(status))
          throw Error("请求实际终态："+status+"；"+String(last.error||""));
        if(i===0 || i%8===0) {
          // Standalone recovery also runs in isolation in the regression
          // harness. A detached UI callback cannot alter Broker status.
          try { update(0,0,
            "SSE 连接已断开，服务端仍在运行；按相同请求 ID 恢复中"); }
          catch (_) { /* display-only failure */ }
        }
      }else if(++missed>=4 && !observed)return null;
    }catch(error){
      // An observed error/failure terminal must propagate, not become an
      // indefinite polling loop or an invented completed response.
      if(observed && last && !["running","queued","pending"].includes(last.status))
        throw error;
      if(!observed && ++missed>=4)return null;
    }
    if(i<229) await new Promise(resolve=>setTimeout(resolve,4000));
  }
  throw Error("恢复等待结束，但服务端尚无已验证终态；请按请求 ID 查看请求记录");
}

async function stream(model,auth,requestId,messages,update,sceneId){
  // v294: save early client-side transport evidence independently of the
  // final ABCD report or multi-minute same-ID Broker recovery.
  const persistClientTruth=(facts,phase="progress")=>{
    if(!sceneId)return Promise.resolve();
    return api("/api/admin/playground/abcd/transport-truth",{
      method:"POST",body:{
        request_id:requestId,scene:sceneId,client_sse_truth:facts,
        failed:phase!=="complete",phase
      }
    }).catch(()=>{});
  };
const clientStarted=performance.now();
const safeErrorKind=error=>{
  const kind=String(error?.name||"Error");
  return ["AbortError","TypeError","SyntaxError","TimeoutError","NetworkError","RangeError","Error"].includes(kind)
    ?kind:"OtherError";
};
let r;
try{
  r=await fetch("/v1/chat/completions",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+auth.token,"X-Chat2API-Request-ID":requestId,"X-Chat2API-Playground-Transport":"abcd-real-sse-v293"},body:JSON.stringify({model,messages,prompt_mode:"full",stream:true})});
}catch(error){
  // Fetch can reject before even returning SSE response headers. Keep this
  // distinct from a reader abort after headers, without storing exception text.
  const failure=error&&typeof error==="object"?error:Error("SSE fetch failed");
  failure.clientSseTruth={
    response_headers_received:false,fetch_error_name:safeErrorKind(error),
    fetch_failed_after_ms:Math.max(0,Math.round(performance.now()-clientStarted)),
    reader_cancel_requested:false,protocol_done_seen:false,native_reader_eof_seen:false
  };
  throw failure;
}
if(!r.ok){
  const error=Error("API HTTP "+r.status+": "+(await r.text()).slice(0,300));
  error.clientSseTruth={
    response_headers_received:true,
    response_headers_ms:Math.max(0,Math.round(performance.now()-clientStarted)),
    http_status:r.status,fetch_error_name:"HTTPStatusError",
    reader_cancel_requested:false,
    protocol_stop_seen:false,protocol_done_seen:false,native_reader_eof_seen:false,
  };
  throw error;
}
if(!r.body)throw Error("缺少SSE响应体");
const responseHeadersAt=performance.now();
let buffer="",answer="",stopped=false,done=false,frames=0,lastStatus="",streamError=null;
let nativeEof=false,firstChunkAt=null,lastChunkAt=null,readerCancelled=false,readerReleased=false;
let readerErrorName=null,lastCheckpointAt=null,bodyChunks=0;
const relative=at=>at===null?null:Math.max(0,Math.round(at-clientStarted));
const reader=r.body.getReader(),decoder=new TextDecoder();
try{
while(!done){
const chunk=await reader.read();if(chunk.done){nativeEof=true;break;}
const readAt=performance.now();
bodyChunks++;
if(firstChunkAt===null)firstChunkAt=readAt;
lastChunkAt=readAt;
buffer+=decoder.decode(chunk.value,{stream:true}).replace(/\r\n/g,"\n");
if(buffer.length>8000000)throw Error("SSE帧超过8MB");
let pos;
while((pos=buffer.indexOf("\n\n"))>=0){
const raw=buffer.slice(0,pos);buffer=buffer.slice(pos+2);
const data=raw.split("\n").filter(x=>x.startsWith("data:")).map(x=>x.slice(5).trimStart()).join("\n");
if(!data)continue;
if(data==="[DONE]"){done=true;break;}
const event=JSON.parse(data);
if(event.error)throw Error(String(event.error.message||"上游返回错误"));
frames++;
for(const c of event.choices||[]){
if(typeof c.delta?.content==="string")answer+=c.delta.content;
if(typeof c.delta?.chat2api_status?.text==="string")lastStatus=c.delta.chat2api_status.text.slice(0,160);
if(c.finish_reason==="stop")stopped=true;
}
safeProgress(update,answer.length,frames,lastStatus);
}
// Send a bounded, content-free progress checkpoint when the browser
// actually receives chunks. A tab crash or proxy close can prevent the
// later final report or recovery callback from ever being sent.
if(lastCheckpointAt===null||readAt-lastCheckpointAt>=8000){
  lastCheckpointAt=readAt;
  void persistClientTruth({
    response_headers_received:true,
    response_headers_ms:relative(responseHeadersAt),
    first_chunk_ms:relative(firstChunkAt),
    last_chunk_ms:relative(lastChunkAt),
    read_elapsed_ms:relative(readAt),
    sse_body_chunks:Math.min(bodyChunks,1000000),
    parsed_frames:Math.min(frames,1000000),
    protocol_stop_seen:stopped,
    protocol_done_seen:done,
    reader_cancel_requested:false,
    native_reader_eof_seen:false,
  },"progress");
}
}
}catch(error){
  streamError=error;
  // Error messages can include server data; record the exception *class* only.
  readerErrorName=safeErrorKind(error);
}
finally{
  try{
    // Only the browser HTTP client records this action. The Broker has no
    // authority to infer client receipt or the cause of a server disconnect.
    if(!streamError&&stopped&&done){
      if(typeof reader.releaseLock==="function"){reader.releaseLock();readerReleased=true;}
    }else{
      readerCancelled=true;
      await reader.cancel();
    }
  }catch(e){/* observer cleanup never owns Broker terminal */}
}
const clientSseTruth={
  response_headers_received:true,
  response_headers_ms:relative(responseHeadersAt),
  protocol_stop_seen:stopped,
  protocol_done_seen:done,
  native_reader_eof_seen:nativeEof,
  reader_cancel_requested:readerCancelled,
  reader_lock_released:readerReleased,
  reader_error_name:readerErrorName,
  first_chunk_ms:relative(firstChunkAt),
  last_chunk_ms:relative(lastChunkAt),
  read_elapsed_ms:relative(performance.now()),
  parsed_frames:Math.min(frames,1000000),
  sse_body_chunks:Math.min(bodyChunks,1000000),
  reader_cancel_reason:readerCancelled
    ?(streamError?"StreamError":"MissingTerminal"):"None",
};
// Always attach this bounded, content-free client observation to the SAME
// request's ABCD report, including the detached-Broker recovery path.
if(!stopped||!done){
  // This must be written BEFORE same-request recovery, which may continue
  // for minutes. Previously the browser facts were lost if the user left
  // during the recovery loop.
  await persistClientTruth(clientSseTruth,"incomplete");
  try{
    const recovered=await recoverDisconnectedStream(requestId,update);
    if(recovered)return {...recovered,client_sse_truth:clientSseTruth};
  }catch(error){
    if(error&&typeof error==="object")error.clientSseTruth=clientSseTruth;
    throw error;
  }
  const err=streamError||Error("SSE缺少stop或[DONE]，不能判为完成");
  if(err&&typeof err==="object")err.clientSseTruth=clientSseTruth;
  throw err;
}
if(!answer.trim()){
  const err=Error("权威正文为空");
  err.clientSseTruth=clientSseTruth;
  throw err;
}
return {text:answer,frames,client_sse_truth:clientSseTruth};
}
const images=text=>[...text.matchAll(/!\[[^\]\n]*\]\((https:\/\/[^)\s]+)\)/g)].map(x=>({name:"picture",url:x[1]}));
const files=text=>[...text.matchAll(/\[📎\s*([^\]\n]+)\]\((https:\/\/[^)\s]+)\)/g)].map(x=>({name:x[1],url:x[2]}));
const unique=items=>[...new Map(items.map(x=>[x.url,x])).values()];
async function verifyAsset(asset,image){
const u=new URL(asset.url);
if(u.protocol!=="https:"||u.origin!==location.origin||!(image?u.pathname.startsWith("/api/media/"):u.pathname.startsWith("/api/generated-files/")))throw Error("不是当前站点的受控产物："+asset.name);
// A ready status carries the SHA256 of the original staged bytes.
// Download and hash independently: file headers alone cannot prove identity.
const pending=/^\/api\/generated-files\/pending\/pgen_[a-f0-9]{32}\/[A-Za-z0-9_-]{16,150}$/.test(u.pathname);
let expected=null;
if(pending){
  if(u.search||u.hash)throw Error("产物链接包含意外参数："+asset.name);
  const stat=await fetch(u.origin+u.pathname+"/status",{cache:"no-store",credentials:"omit"});
  if(!stat.ok)throw Error("无法读取归档文件校验信息："+asset.name);
  expected=await stat.json();
  if(expected?.status!=="ready"||!Number.isSafeInteger(expected?.bytes)
    ||expected.bytes<1||!(/^[a-f0-9]{64}$/i).test(String(expected.sha256||""))
    ||expected.filename!==asset.name)
    throw Error("文件归档状态或身份未验证："+asset.name);
}
const r=await fetch(u.href,{cache:"no-store",credentials:"omit"});
if(!r.ok)throw Error("文件无法下载："+asset.name+" HTTP "+r.status);
const buf=await r.arrayBuffer(),b=new Uint8Array(buf),ext=asset.name.toLowerCase().split(".").pop();
if(b.length<16||b.length>48*1024*1024)throw Error("文件大小异常："+asset.name);
const zip=b[0]===80&&b[1]===75;
const pic=(b[0]===137&&b[1]===80&&b[2]===78&&b[3]===71)||(b[0]===255&&b[1]===216)||(b[0]===71&&b[1]===73&&b[2]===70)||(b[0]===82&&b[1]===73&&b[2]===70&&b[3]===70);
if(image&&!pic)throw Error("文件头不是图像："+asset.name);
if(!image&&["xlsx","pptx","zip"].includes(ext)&&!zip)throw Error("文件头不是ZIP/Office："+asset.name);
if(!image&&ext==="json")JSON.parse(new TextDecoder().decode(b));
let sha256Verified=false;
if(expected){
  if(b.length!==expected.bytes)throw Error("文件下载大小与归档记录不一致："+asset.name);
  if(!globalThis.crypto?.subtle?.digest)throw Error("浏览器无法完成 SHA256 校验："+asset.name);
  const digest=await globalThis.crypto.subtle.digest("SHA-256",buf);
  const actual=[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,"0")).join("");
  if(actual!==String(expected.sha256).toLowerCase())
    throw Error("文件实际下载 SHA256 与归档不一致："+asset.name);
  sha256Verified=true;
}
return {name:asset.name,url:u.href,bytes:b.length,sha256_verified:sha256Verified};
}
async function inspect(id,turns,task){
const checks=[],assets=[],warnings=[],text=turns.map(x=>x.text).join("\n");
const check=(ok,label)=>checks.push({passed:Boolean(ok),label});
if(id==="A"){
check(text.length>=2500,"全文至少2500字符");
check((text.match(/^\|[^\n]+\|$/gm)||[]).length>=5,"包含Markdown表格");
const fenced=text.includes(String.fromCharCode(96).repeat(3));
const codePresent=fenced||/(?:^|\n)[ \t]*[`]?(?:import\s+\w+|from\s+\w+\s+import|def\s+\w+\s*\(|function\s+\w+\s*\(|const\s+\w+\s*=)/m.test(text);
check(codePresent,"实际包含代码内容");
if(codePresent&&!fenced) warnings.push("代码内容已出现，但 Markdown 代码围栏丢失；展示保真仍需修复");
const formula=["\\boxed","\\frac","\\dfrac","\\sqrt","\\sum","\\begin","\\(","\\["].some(mark=>text.includes(mark))||/\$[^$\n]{3,}\$/.test(text);
check(formula,"包含可识别的数学公式内容");
check(text.lastIndexOf(task.end_anchor||"执行任务表")>text.length*.5,"末段含指定执行清单");
}
if(id==="B"){
const all=unique(images(text));check(all.length===3,"三张独立图片");
for(const a of all)assets.push(await verifyAsset(a,true));
check(assets.length===3,"三个图片均可下载");
}
if(id==="C"){
const all=unique(files(text));
for(const ext of ["xlsx","pptx","json","zip"]){
const f=all.find(a=>a.name.toLowerCase().endsWith("."+ext));
check(Boolean(f),"有"+ext.toUpperCase()+"文件");
if(f)assets.push(await verifyAsset(f,false));
}
check(assets.length===4,"全部四种文件真实下载");
check(assets.length===4&&assets.every(x=>x.sha256_verified===true),
  "四种文件下载字节 SHA256 均与服务端归档记录一致");
}
if(id==="D" && task.kind==="web_app"){
check(turns.length===1,"完成完整的离线网页开发请求");
check(turns[0]?.text?.length>=2000,"具有足够完整的实现说明和代码");
const html=files(turns[0]?.text||"").find(x=>x.name.toLowerCase().endsWith(".html"));
check(Boolean(html),"交付可下载的HTML文件");
if(html)assets.push(await verifyAsset(html,false));
check(/localStorage|本地存储/.test(text),"包含本地数据持久化机制");
check(/库存|预约/.test(text),"包含库存和预约业务规则");
}
if(id==="D" && task.kind!=="web_app"){
check(turns.length===2,"完成两轮请求");
check(turns.every(x=>x.text.length>=450),"两轮都有完整内容");
check((task.keywords||[]).some(word=>(turns[1]?.text||"").includes(word)),"第二轮延续当前题目的关键设定");
const md=files(turns[0]?.text||"").find(x=>x.name.toLowerCase().endsWith(".md"));
check(Boolean(md),"第一轮有Markdown档案");
if(md)assets.push(await verifyAsset(md,false));
}
return {checks,assets,warnings,passed:checks.every(x=>x.passed)&&checks.length>0};
}
async function run(){
if(working)return;
working=true;byId("pgABCDRun").disabled=true;
const sceneId=byId("pgABCDSelect").value,model=byId("pgChatModel").value;
let scene=null;
report={scene:sceneId,model,started_at:new Date().toISOString(),turns:[],checks:[],assets:[],warnings:[],status:"running",pool_version:"abcd-new-real-scenes-v5"};
let auth=null;
try{
if(!model)throw Error("请选择在线Worker支持的模型");
auth=await key();
// Consume once before dispatch. Database BEGIN IMMEDIATE prevents reuse across
// browsers/workers; retrying with same claim_id is idempotent.
const claim_id="abcd_"+crypto.randomUUID().replace(/-/g,"");
scene=await api("/api/admin/playground/abcd/claim",{method:"POST",body:{scene:sceneId,claim_id}});
if(!scene||!Array.isArray(scene.prompts)||!scene.prompts.length)throw Error("无法领取持久化新提示词");
Object.assign(report,{prompt_claim_id:claim_id,scene_title:scene.title,scene_serial:scene.serial,prompts_consumed:true,next_title:scene.next?.title||null});
await refreshPool();
const history=[];
for(let i=0;i<scene.prompts.length;i++){
const prompt=scene.prompts[i],request_id="req_"+crypto.randomUUID().replace(/-/g,""),begin=performance.now();
history.push({role:"user",content:prompt});
note(sceneId+"第"+(i+1)+"轮正在提交："+request_id);
await api("/api/admin/playground/chat-runs",{method:"POST",body:{request_id,model,prompt,api_key_id:auth.id,api_key_name:auth.label,attachments_count:0,transport_contract:"abcd-real-sse-v293",abcd_scene:sceneId}});
try{
const out=await stream(model,auth,request_id,history,(chars,frames,status)=>note(sceneId+" 第"+(i+1)+"轮 "+request_id+"\n正文"+chars+"字符，SSE帧"+frames+"\n"+status),sceneId);
history.push({role:"assistant",content:out.text});
report.turns.push({request_id,prompt,text:out.text,frames:out.frames,
  client_sse_truth:out.client_sse_truth||null,
  recovered_after_disconnect:out.recovered===true,duration_ms:Math.round(performance.now()-begin)});
try {
  await api("/api/admin/playground/abcd/transport-truth",{method:"POST",body:{
    request_id,scene:sceneId,client_sse_truth:out.client_sse_truth||null,failed:false
  }});
} catch (_) { /* evidence sink failure cannot change genuine protocol terminal */ }
}catch(e){
report.turns.push({request_id,prompt,text:"",frames:0,error:String(e),
  client_sse_truth:e?.clientSseTruth||null,
  duration_ms:Math.round(performance.now()-begin)});
try {
  // Persist bounded client-side receive facts before a later ABCD summary,
  // page navigation or transport error prevents the full report from saving.
  await api("/api/admin/playground/abcd/transport-truth",{method:"POST",body:{
    request_id,scene:sceneId,client_sse_truth:e?.clientSseTruth||null,failed:true
  }});
} catch (_) { /* never hide original stream failure */ }
throw e;
}
}
Object.assign(report,await inspect(sceneId,report.turns,scene));
if(report.turns.some(turn=>turn.recovered_after_disconnect===true))
  report.warnings.push("至少一条请求在 SSE 提前断开后按相同请求 ID 从服务端真实终态恢复；不把原始 SSE 交付误报为成功");
for(const turn of report.turns){
  // API-level SSE stop+[DONE] proves that our endpoint finished, not that
  // ChatGPT conversation-fetch completed normally. Read immutable request
  // evidence without giving this observer authority to change request state.
  try{
    const detail=await api("/api/admin/requests/"+encodeURIComponent(turn.request_id));
    const diag=detail.diagnostics||{};
    const expectedModel=String(detail.routed_model||detail.requested_model||model||"").trim();
    const servedModel=String(detail.served_model||diag.served_model||"").trim();
    const modelMismatch=detail.model_conflict===true||diag.model_conflict===true
      ||Boolean(servedModel&&expectedModel&&servedModel!==expectedModel);
    turn.upstream_evidence={
      api_status:detail.status,
      network_terminal_proof:diag.network_stream_terminal_proof===true,
      network_stream_phase:diag.network_stream_phase||null,
      network_stream_error:diag.network_stream_error||null,
      response_terminal_source:diag.response_terminal_source||null,
      requested_model:expectedModel||null,
      served_model:servedModel||null,
      model_conflict:modelMismatch,
      model_verified:detail.model_verified===true
    };
    if(modelMismatch)report.warnings.push("请求 "+turn.request_id+
      " 的实际服务模型 "+(servedModel||"未确认")+" 与指定模型 "+(expectedModel||"未确认")+
      " 不一致；不得作为指定模型验收通过");
    if(detail.status!=="completed"||diag.network_stream_terminal_proof!==true)
      report.warnings.push("请求 "+turn.request_id+" 的底层ChatGPT流缺乏正常终态证据，请人工复核；API completed 不能替代上游终态");
  }catch(e){report.warnings.push("请求 "+turn.request_id+" 无法读取上游终态诊断："+String(e.message||e));}
}
report.status=report.passed?(report.warnings.length?"warning":"passed"):"failed";
}catch(e){report.status="failed";report.error=String(e?.message||e);}
finally{
report.finished_at=new Date().toISOString();
for(const turn of report.turns){
try{await api("/api/admin/playground/chat-records",{method:"POST",body:{request_id:turn.request_id,model,prompt:turn.prompt,api_key_id:auth?.id||null,api_key_name:auth?.label||"未解析",status:report.status,duration_ms:turn.duration_ms,response_chars:turn.text.length,attachments_count:0,error:turn.error||report.error||(report.status==="passed"?null:(report.warnings.join("；")||"产物验证失败"))}});}
catch(e){console.warn("ABCD record persistence failed",e);}
}
if(report.turns.length){
try{
  await api("/api/admin/playground/abcd/report",{method:"POST",body:{scene:sceneId,report}});
}catch(error){
  report.warnings.push("ABCD 诊断报告写入请求日志失败："+String(error?.message||error));
}
}
note(sceneId+" · "+(report.status==="passed"?"协议和上游终态已证实":report.status==="warning"?"内容验收有警告／上游终态未证实":"未通过")+"\n"+(report.error||report.checks.map(c=>(c.passed?"✓ ":"✗ ")+c.label).join("\n"))+"\n"+(report.warnings||[]).join("\n")+"\n此题已从待用池移除，下次自动使用新主题。图像质量、PPT页数及业务准确性仍需人工复核。");
working=false;byId("pgABCDRun").disabled=false;
if(typeof loadTests==="function")Promise.resolve(loadTests()).catch(()=>{});
}
}
byId("pgABCDRun").addEventListener("click",run);
})();