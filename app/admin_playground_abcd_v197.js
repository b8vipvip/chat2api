(() => {
"use strict";
const host=document.getElementById("playgroundChatPanel");
if(!host||document.getElementById("pgABCD"))return;
const cases={
A:{title:"晨雾咖啡商业运营",prompts:["我计划在成都经营精品咖啡品牌「晨雾咖啡 Morning Mist」。请提供完整、可执行的开店运营方案，涵盖定位、市场竞品、选址、菜单、定价及毛利、供应链、团队排班、获客、90天运营规划和风险。至少提供两张Markdown表格、一个可运行的代码示例和明确的数学公式。不要只给提纲，最后以「落地执行清单」收尾，列出十项具体行动及负责人。"]},
B:{title:"云端图书馆三张插画",prompts:["请创作完整三幕奇幻绘本《云朵尽头的图书馆》，并分别实际生成三张独立可下载的3:4竖版高清插画：《清晨的云端图书馆》《星光书籍的秘密》《月光灯塔的重逢》。主人公露米的斗篷、书包、项链在三张图片中保持一致。请不要交付拼接图、提示词、绘图代码或路径，实际交付三张不同的图片。"]},
C:{title:"海滨音乐节XLSX/PPTX/JSON/ZIP",prompts:["为「BlueWave 海滨音乐节」编写完整策划，并实际交付四个独立可下载文件：含预算公式的XLSX、至少8页的招商PPTX、合法日程和场地资源JSON、包含前述资料和说明书的ZIP。正文另外解释预算、活动风险、赞助收益和执行步骤。不能使用代码块、虚构下载地址或本地文件路径代替真实文件。"]},
D:{title:"午夜钟楼连续两轮",prompts:["为1928年虚构的海滨城市雾港设计复古悬疑游戏《午夜钟楼》。1876年钟楼每天午夜敲十三声，第十三声会令一人遗忘最重要的秘密。完整设计世界观、三名人物、五条线索、第一章调查对话和三条分支选择；实际交付可下载的Markdown人物档案文件，第一章结尾留下悬念。","沿用上一轮《午夜钟楼》的雾港背景、人物档案、十三声钟声规则和三条分支，请续写第二章，承接第一章的选择后果，设计五段可交互对话、两个反转、关联线索，并明确引用前一轮人物档案中的角色和秘密，不要重新开始故事。"]}
};
const byId=id=>document.getElementById(id),note=s=>{byId("pgABCDNote").textContent=s};
const el=document.createElement("section");el.className="panel";el.id="pgABCD";el.style.margin="12px 0";
el.innerHTML='<h3>ABCD 真实场景验收</h3><p class="muted">通过已选业务Key和文本模型实际发起API流式请求。A检验全文结构、B检验3张图片、C检验4份文件、D连续两轮上下文。必须收到权威SSE完成事件和真实产物才可通过。</p><div style="display:flex;flex-wrap:wrap;gap:8px;margin:10px 0"><select id="pgABCDSelect"></select><button class="action good" id="pgABCDRun">运行场景</button><button class="action" id="pgABCDExport" disabled>导出诊断报告</button></div><p id="pgABCDNote" role="status" style="white-space:pre-wrap;line-height:1.6">尚未执行</p>';
host.querySelector(".pgChatMode")?.after(el);
byId("pgABCDSelect").innerHTML=Object.entries(cases).map(([id,s])=>'<option value="'+id+'">'+id+' · '+s.title+'</option>').join("");
let working=false,report=null;
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
async function inspect(id,turns){
const checks=[],assets=[],text=turns.map(x=>x.text).join("\n");
const check=(ok,label)=>checks.push({passed:Boolean(ok),label});
if(id==="A"){
check(text.length>=2500,"全文至少2500字符");
check((text.match(/^\|[^\n]+\|$/gm)||[]).length>=5,"包含Markdown表格");
check(text.includes(String.fromCharCode(96).repeat(3)),"包含代码块");
check(/\\\(|\\\[|\$[^$\n]{3,}\$/.test(text),"包含数学公式");
check(text.lastIndexOf("落地执行清单")>text.length*.5,"末段含落地执行清单");
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
check(/雾港|钟楼|钟声/.test(turns[1]?.text||""),"第二轮延续故事");
const md=files(turns[0]?.text||"").find(x=>x.name.toLowerCase().endsWith(".md"));
check(Boolean(md),"第一轮有Markdown档案");
if(md)assets.push(await verifyAsset(md,false));
}
return {checks,assets,passed:checks.every(x=>x.passed)&&checks.length>0};
}
async function run(){
if(working)return;
working=true;byId("pgABCDRun").disabled=true;byId("pgABCDExport").disabled=true;
const sceneId=byId("pgABCDSelect").value,scene=cases[sceneId],model=byId("pgChatModel").value;
report={scene:sceneId,model,started_at:new Date().toISOString(),turns:[],checks:[],assets:[],status:"running"};
let auth=null;
try{
if(!model)throw Error("请选择在线Worker支持的模型");
auth=await key();
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
Object.assign(report,await inspect(sceneId,report.turns));
report.status=report.passed?"passed":"failed";
}catch(e){report.status="failed";report.error=String(e?.message||e);}
finally{
report.finished_at=new Date().toISOString();
for(const turn of report.turns){
try{await api("/api/admin/playground/chat-records",{method:"POST",body:{request_id:turn.request_id,model,prompt:turn.prompt,api_key_id:auth?.id||null,api_key_name:auth?.label||"未解析",status:report.status==="passed"?"passed":"failed",duration_ms:turn.duration_ms,response_chars:turn.text.length,attachments_count:0,error:turn.error||report.error||(report.status==="passed"?null:"产物验证失败")}});}
catch(e){console.warn("ABCD record persistence failed",e);}
}
note(sceneId+" · "+(report.status==="passed"?"接口和产物结构验收通过":"未通过")+"\n"+(report.error||report.checks.map(c=>(c.passed?"✓ ":"✗ ")+c.label).join("\n"))+"\n图像质量、PPT真实页数及业务准确性仍需人工核查。");
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