(() => {
"use strict";
const host=document.getElementById("playgroundChatPanel");
if(!host||document.getElementById("pgABCD"))return;
// Task content lives in the persistent server-side one-shot pool, never in the JS bundle.
const cases={A:{title:"长篇图文与公式",prompts:[]},B:{title:"三幅独立插画",prompts:[]},C:{title:"四份实际交付文件",prompts:[]},D:{title:"连续两轮互动剧情",prompts:[]}};
const byId=id=>document.getElementById(id),note=s=>{byId("pgABCDNote").textContent=s};
const el=document.createElement("section");el.className="panel";el.id="pgABCD";el.style.margin="12px 0";
el.innerHTML='<h3>ABCD 真实场景验收</h3><p class="muted">通过已选业务Key和文本模型实际发起API流式请求。A检验全文结构、B检验3张图片、C检验4份文件、D连续两轮上下文。每次运行将当前题目立即从持久化队列移除，自动换成全新主题；API SSE 完成不代表上游 ChatGPT 终态已验证。</p><div style="display:flex;flex-wrap:wrap;gap:8px;margin:10px 0"><select id="pgABCDSelect"></select><button class="action good" id="pgABCDRun">运行场景</button><button class="action" id="pgABCDExport" disabled>导出诊断报告</button></div><p id="pgABCDNote" role="status" style="white-space:pre-wrap;line-height:1.6">尚未执行</p>';
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
async function stream(model,auth,requestId,messages,update){
const r=await fetch("/v1/chat/completions",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+auth.token,"X-Chat2API-Request-ID":requestId},body:JSON.stringify({model,messages,prompt_mode:"full",stream:true})});
if(!r.ok)throw Error("API HTTP "+r.status+": "+(await r.text()).slice(0,300));
if(!r.body)throw Error("缺少SSE响应体");
let buffer="",answer="",stopped=false,done=false,frames=0,lastStatus="";
const reader=r.body.getReader(),decoder=new TextDecoder();
try{
while(!done){
const chunk=await reader.read();if(chunk.done)break;
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
update(answer.length,frames,lastStatus);
}
}
}finally{try{await reader.cancel()}catch(e){}}
if(!stopped||!done)throw Error("SSE缺少stop或[DONE]，不能判为完成");
if(!answer.trim())throw Error("权威正文为空");
return {text:answer,frames};
}
const images=text=>[...text.matchAll(/!\[[^\]\n]*\]\((https:\/\/[^)\s]+)\)/g)].map(x=>({name:"picture",url:x[1]}));
const files=text=>[...text.matchAll(/\[📎\s*([^\]\n]+)\]\((https:\/\/[^)\s]+)\)/g)].map(x=>({name:x[1],url:x[2]}));
const unique=items=>[...new Map(items.map(x=>[x.url,x])).values()];
async function verifyAsset(asset,image){
const u=new URL(asset.url);
if(u.protocol!=="https:"||u.origin!==location.origin||!(image?u.pathname.startsWith("/api/media/"):u.pathname.startsWith("/api/generated-files/")))throw Error("不是当前站点的受控产物："+asset.name);
const r=await fetch(u.href,{cache:"no-store",credentials:"omit"});
if(!r.ok)throw Error("文件无法下载："+asset.name+" HTTP "+r.status);
const buf=await r.arrayBuffer(),b=new Uint8Array(buf),ext=asset.name.toLowerCase().split(".").pop();
if(b.length<16||b.length>48*1024*1024)throw Error("文件大小异常："+asset.name);
const zip=b[0]===80&&b[1]===75;
const pic=(b[0]===137&&b[1]===80&&b[2]===78&&b[3]===71)||(b[0]===255&&b[1]===216)||(b[0]===71&&b[1]===73&&b[2]===70)||(b[0]===82&&b[1]===73&&b[2]===70&&b[3]===70);
if(image&&!pic)throw Error("文件头不是图像："+asset.name);
if(!image&&["xlsx","pptx","zip"].includes(ext)&&!zip)throw Error("文件头不是ZIP/Office："+asset.name);
if(!image&&ext==="json")JSON.parse(new TextDecoder().decode(b));
return {name:asset.name,url:u.href,bytes:b.length};
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
}
if(id==="D"){
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
working=true;byId("pgABCDRun").disabled=true;byId("pgABCDExport").disabled=true;
const sceneId=byId("pgABCDSelect").value,model=byId("pgChatModel").value;
let scene=null;
report={scene:sceneId,model,started_at:new Date().toISOString(),turns:[],checks:[],assets:[],warnings:[],status:"running",pool_version:"abcd-one-shot-v198"};
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
await api("/api/admin/playground/chat-runs",{method:"POST",body:{request_id,model,prompt,api_key_id:auth.id,api_key_name:auth.label,attachments_count:0}});
try{
const out=await stream(model,auth,request_id,history,(chars,frames,status)=>note(sceneId+" 第"+(i+1)+"轮 "+request_id+"\n正文"+chars+"字符，SSE帧"+frames+"\n"+status));
history.push({role:"assistant",content:out.text});
report.turns.push({request_id,prompt,text:out.text,frames:out.frames,duration_ms:Math.round(performance.now()-begin)});
}catch(e){
report.turns.push({request_id,prompt,text:"",frames:0,error:String(e),duration_ms:Math.round(performance.now()-begin)});
throw e;
}
}
Object.assign(report,await inspect(sceneId,report.turns,scene));
for(const turn of report.turns){
  // API-level SSE stop+[DONE] proves that our endpoint finished, not that
  // ChatGPT conversation-fetch completed normally. Read immutable request
  // evidence without giving this observer authority to change request state.
  try{
    const detail=await api("/api/admin/requests/"+encodeURIComponent(turn.request_id));
    const diag=detail.diagnostics||{};
    turn.upstream_evidence={
      api_status:detail.status,
      network_terminal_proof:diag.network_stream_terminal_proof===true,
      network_stream_phase:diag.network_stream_phase||null,
      network_stream_error:diag.network_stream_error||null,
      response_terminal_source:diag.response_terminal_source||null
    };
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
note(sceneId+" · "+(report.status==="passed"?"协议和上游终态已证实":report.status==="warning"?"内容验收有警告／上游终态未证实":"未通过")+"\n"+(report.error||report.checks.map(c=>(c.passed?"✓ ":"✗ ")+c.label).join("\n"))+"\n"+(report.warnings||[]).join("\n")+"\n此题已从待用池移除，下次自动使用新主题。图像质量、PPT页数及业务准确性仍需人工复核。");
working=false;byId("pgABCDRun").disabled=false;byId("pgABCDExport").disabled=false;
if(typeof loadTests==="function")Promise.resolve(loadTests()).catch(()=>{});
}
}
byId("pgABCDRun").addEventListener("click",run);
byId("pgABCDExport").addEventListener("click",()=>{
if(!report)return;
const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:"application/json"}));
const link=document.createElement("a");link.href=url;link.download="chat2api-ABCD-"+report.scene+"-"+Date.now()+".json";link.click();
setTimeout(()=>URL.revokeObjectURL(url),2000);
});
})();