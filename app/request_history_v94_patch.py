from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.routing import APIRoute

from . import admin as admin_module
from .response_history_media_v188 import history_safe_response_text
from .playground_request_evidence_v207 import request_test_index, playground_link


PATCH_ID = "admin-request-single-owner-v94"
CONVERSATION_PATCH_ID = "admin-request-conversation-v97"
ASSET_COMPILER_REVISION = "request-history-served-asset-single-owner-v98"

# Request History has one browser-side renderer. Historical admin assets are
# compiled once on the server before delivery so they can keep unrelated
# features without retaining any request-table ownership. There is deliberately
# no browser MutationObserver, repair loop, fallback renderer, or competing
# onclick owner for this table.
_BASE_HEADER = (
    '<thead><tr><th>时间</th><th>类型</th><th>状态</th><th>Key</th><th>模型</th>'
    '<th>附件</th><th>首包</th><th>总耗时</th><th>Token</th></tr></thead>'
    '<tbody id="rqBody"></tbody>'
)
_FINAL_HEADER = (
    '<thead><tr><th>时间（北京时间）</th><th>请求ID</th><th>类型</th><th>状态</th><th>Key</th>'
    '<th>设备标识</th><th>模型</th><th>附件</th><th>首包</th><th>总耗时</th><th>Token</th>'
    '<th>对话</th><th>日志</th></tr></thead><tbody id="rqBody"></tbody>'
)

_BASE_REQUEST_VIEW_OPEN = '<section class="view" id="view-requests"><div class="split">'
_FINAL_REQUEST_VIEW_OPEN = '<section class="view" id="view-requests"><div>'
_BASE_REQUEST_DETAIL_PANEL = (
    '<div class="panel detail"><h3>请求详情</h3>'
    '<div id="rqDetail" class="muted">点击记录查看诊断。</div></div>'
)
_BASE_REQUEST_DETAIL_RE = re.compile(
    r"async function requestDetail\(id\)\{.*?\}\s*window\.requestDetail=requestDetail;",
    re.DOTALL,
)

_BASE_LOADER_RE = re.compile(
    r"async function loadRequests\(\)\{.*?\}\s*\$\('rqGo'\)\.onclick=loadRequests;",
    re.DOTALL,
)

_FINAL_LOADER = r'''function requestHistoryText(value, fallback='-'){
  if(value===null||value===undefined||value==='')return fallback;
  return String(value);
}
function requestHistoryCell(tr,value,className=''){
  const td=document.createElement('td');
  if(className)td.className=className;
  td.textContent=requestHistoryText(value);
  tr.appendChild(td);
  return td;
}
function requestHistoryStatusCell(tr,value,stage=''){
  const td=document.createElement('td'),span=document.createElement('span');
  const text=requestHistoryText(value);
  span.className='pill '+(text==='completed'?'ok':text==='error'?'bad':(text==='running'||text==='queued')?'warn':'');
  span.textContent=text==='queued'?'排队中':text;
  if(text==='queued')span.title=stage==='waiting-capacity'?'已选中 Worker，正在等待容量释放':'正在选择可用 Worker';
  td.appendChild(span);tr.appendChild(td);return td;
}
function requestHistoryButton(tr,label,title,handler,enabled=true){
  const td=document.createElement('td');
  if(enabled){
    const button=document.createElement('button');
    button.type='button';button.className='action';button.textContent=label;button.title=title;
    button.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();handler();});
    td.appendChild(button);
  }else td.textContent='-';
  tr.appendChild(td);return td;
}
async function requestHistoryDownload(path,fallbackName){
  if(!key())throw new Error('请先连接管理员 CHAT2API_API_KEY');
  const response=await fetch(path,{headers:{Authorization:`Bearer ${key()}`},cache:'no-store'});
  if(!response.ok){
    let message=`HTTP ${response.status}`;
    try{const data=await response.json();message=data.detail||data.error||message;}catch(_){}
    throw new Error(message);
  }
  const blob=await response.blob();
  const disposition=response.headers.get('content-disposition')||'';
  const matched=disposition.match(/filename="?([^";]+)"?/i);
  const url=URL.createObjectURL(blob),anchor=document.createElement('a');
  anchor.href=url;anchor.download=matched?.[1]||fallbackName;document.body.appendChild(anchor);anchor.click();anchor.remove();
  setTimeout(()=>URL.revokeObjectURL(url),3000);
}
let requestHistoryMediaUrls=[];
let requestHistoryMediaSequence=0;
function requestHistoryRevokeMedia(){
  for(const url of requestHistoryMediaUrls)URL.revokeObjectURL(url);
  requestHistoryMediaUrls=[];
}
function requestHistoryEnsureConversationModal(){
  if(document.getElementById('requestConversationModal'))return;
  const wrap=document.createElement('div');
  wrap.id='requestConversationModal';
  wrap.style.cssText='display:none;position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.58);align-items:center;justify-content:center;padding:24px';
  wrap.innerHTML=`
    <div class="card" style="width:min(1100px,95vw);max-height:90vh;display:flex;flex-direction:column;gap:12px;overflow:hidden">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><h3 style="margin:0">请求对话</h3><button id="requestConversationClose" class="secondary" type="button">关闭</button></div>
      <div id="requestConversationMeta" class="muted"></div>
      <div style="overflow:auto;display:grid;gap:12px;padding-right:2px">
        <div><div style="font-weight:700;margin-bottom:6px">提示词</div><pre id="requestConversationPrompt" style="margin:0;white-space:pre-wrap;word-break:break-word;max-height:34vh;overflow:auto;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--panel2)"></pre></div>
        <div><div style="font-weight:700;margin-bottom:6px">生成回复（文字与图片）</div><div id="requestConversationReply" style="white-space:pre-wrap;word-break:break-word;max-height:40vh;overflow:auto;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--panel2)"></div><div id="requestConversationMedia" style="display:flex;flex-wrap:wrap;gap:12px;margin-top:10px"></div></div>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  document.getElementById('requestConversationClose').onclick=()=>{requestHistoryMediaSequence++;requestHistoryRevokeMedia();wrap.style.display='none';};
  wrap.addEventListener('click',event=>{if(event.target===wrap){requestHistoryMediaSequence++;requestHistoryRevokeMedia();wrap.style.display='none';}});
}
async function requestHistoryShowConversation(requestId){
  requestHistoryEnsureConversationModal();
  const modal=document.getElementById('requestConversationModal');
  const meta=document.getElementById('requestConversationMeta');
  const prompt=document.getElementById('requestConversationPrompt');
  const reply=document.getElementById('requestConversationReply');
  const media=document.getElementById('requestConversationMedia');
  const sequence=++requestHistoryMediaSequence;
  requestHistoryRevokeMedia();
  media.replaceChildren();
  const shortId=requestId?requestId.slice(-4):'-';
  meta.textContent=`请求 …${shortId} · 正在加载…`;
  prompt.textContent='';reply.textContent='';modal.style.display='flex';
  try{
    const row=await api(`/api/admin/requests/${encodeURIComponent(requestId)}`);
    const promptText=requestHistoryText(row?.final_prompt,'');
    const replyText=requestHistoryText(row?.response_text,'');
    prompt.textContent=promptText||'该请求没有保存提示词。旧请求或关闭“保存最终提示词”的请求不会包含此内容。';
    const actualReply=replyText||'该请求没有保存文字回复。若是图片生成请求，请查看下方生成图片。';
    const rich=globalThis.__CHAT2API_PLAYGROUND_CHAT_V69__;
    if(typeof rich?.renderMarkdown==='function')reply.innerHTML=rich.renderMarkdown(actualReply);
    else reply.textContent=actualReply;
    const refs=Array.isArray(row?.response_media)?row.response_media.slice(0,4):[];
    meta.textContent=`请求 …${shortId} · 提示词 ${Number(row?.final_prompt_chars??promptText.length)} 字符 · 回复 ${Number(row?.completion_chars??replyText.length)} 字符 · 已归档图片 ${refs.length} 张`;
    for(const item of refs){
      const fileId=String(item?.file_id||'');
      if(!/^file_[A-Za-z0-9]{8,60}$/.test(fileId))continue;
      try{
        const res=await fetch(`/api/admin/requests/${encodeURIComponent(requestId)}/media/${encodeURIComponent(fileId)}`,{
          headers:{Authorization:`Bearer ${key()}`},cache:'no-store'
        });
        if(!res.ok)throw new Error(`HTTP ${res.status}`);
        const blob=await res.blob();
        if(!['image/png','image/jpeg','image/webp','image/gif'].includes(blob.type)||blob.size>20*1024*1024)
          throw new Error('不支持的图片格式或大小');
        if(sequence!==requestHistoryMediaSequence)return;
        const url=URL.createObjectURL(blob);
        requestHistoryMediaUrls.push(url);
        const link=document.createElement('a');
        const ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp','image/gif':'gif'}[blob.type]||'png';
        link.href=url;link.download=`chat2api-${requestId}.${ext}`;
        const img=document.createElement('img');
        img.src=url;img.alt='请求生成的图片';img.loading='lazy';
        img.style.cssText='display:block;max-width:min(100%,420px);max-height:420px;object-fit:contain;border:1px solid var(--line);border-radius:10px';
        link.appendChild(img);media.appendChild(link);
      }catch(error){
        if(sequence!==requestHistoryMediaSequence)return;
        const warning=document.createElement('span');warning.className='muted';
        warning.textContent=`图片归档读取失败：${String(error?.message||error)}`;media.appendChild(warning);
      }
    }
  }catch(error){
    prompt.textContent=`加载失败：${String(error?.message||error)}`;
    reply.textContent='';meta.textContent=`请求 …${shortId}`;
  }
}
window.showRequestConversationV97=requestHistoryShowConversation;
// The UI and API use the SAME unambiguous instant. When the pasted value
// carries no offset, interpret it in the request-history's Beijing timezone,
// NOT in the browser's OS zone (admins often work through remote desktops).
function requestHistoryParseExportSince(value){
  const source=String(value||'').trim();
  const m=/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?\s*(Z|[+-]\d{2}:?\d{2})?$/i.exec(source);
  if(!m)return null;
  const year=Number(m[1]),month=Number(m[2]),day=Number(m[3]);
  const hour=Number(m[4]),minute=Number(m[5]),second=Number(m[6]||0);
  const ms=Number((m[7]||'').padEnd(3,'0')||0);
  if(year<100||month<1||month>12||day<1||day>31||hour>23||minute>59||second>59)return null;
  const wall=new Date(Date.UTC(year,month-1,day,hour,minute,second,ms));
  if(wall.getUTCFullYear()!==year||wall.getUTCMonth()+1!==month||
      wall.getUTCDate()!==day||wall.getUTCHours()!==hour||
      wall.getUTCMinutes()!==minute||wall.getUTCSeconds()!==second)return null;
  const zone=m[8]||'';
  let offsetMinutes=480;
  if(zone.toUpperCase()==='Z')offsetMinutes=0;
  else if(zone){
    const sign=zone[0]==='+'?1:-1;
    const digits=zone.slice(1).replace(':','');
    const hh=Number(digits.slice(0,2)),mm=Number(digits.slice(2,4));
    if(hh>14||mm>59||(hh===14&&mm!==0))return null;
    offsetMinutes=sign*(hh*60+mm);
  }
  const instant=new Date(wall.getTime()-offsetMinutes*60000);
  if(!Number.isFinite(instant.getTime()))return null;
  return {iso:instant.toISOString(),assumedBeijing:!zone};
}
function requestHistoryEnsureControls(){
  requestHistoryEnsureConversationModal();
  const section=document.getElementById('view-requests'),toolbar=section?.querySelector('.toolbar');
  if(toolbar&&!document.getElementById('rqBatchExportLogs')){
    const group=document.createElement('div');
    group.id='rqBatchExportLogs';
    group.style.cssText='display:inline-flex;align-items:center;gap:7px;flex-wrap:wrap';
    const label=document.createElement('label');
    label.textContent='从时间（含）';label.htmlFor='rqBatchExportSince';
    const since=document.createElement('input');
    since.id='rqBatchExportSince';
    since.type='text';
    since.placeholder='2026-10-29 10:39:31';
    since.autocomplete='off';
    since.setAttribute('aria-label','请求日志导出起始时间');
    since.title='可粘贴 2026-10-29 10:39:31 或 2026-10-29T11:28:29+08:00；不带时区按北京时间 UTC+08:00';
    since.style.cssText='width:258px;max-width:calc(100vw - 48px);box-sizing:border-box;padding:7px 8px;font-variant-numeric:tabular-nums';
    const note=document.createElement('span');
    note.id='rqBatchExportTimeHint';
    note.style.cssText='font-size:12px;opacity:.85;overflow-wrap:anywhere';
    note.setAttribute('role','status');
    note.textContent='未标注时区按北京时间 UTC+08:00';
    since.addEventListener('input',()=>{
      const value=since.value.trim();
      const parsed=value?requestHistoryParseExportSince(value):null;
      note.textContent=!value?'未标注时区按北京时间 UTC+08:00'
        :parsed?'已识别：'+parsed.iso+(parsed.assumedBeijing?'（按北京时间）':'（使用输入时区）')
        :'时间格式无效，请检查日期、秒数或时区';
    });
    const button=document.createElement('button');button.type='button';
    button.className='action';button.textContent='合并打包导出请求日志';
    button.title='按输入的起始时间（包含该时刻）导出全部仍留存的请求日志，最多 500 条';
    button.addEventListener('click',async()=>{
      const parsed=requestHistoryParseExportSince(since.value);
      if(!parsed){
        status('请输入有效起始时间，例如 2026-10-29 10:39:31 或 2026-10-29T11:28:29+08:00','bad');
        since.focus();return;
      }
      const query=new URLSearchParams({since:parsed.iso});
      button.disabled=true;const oldLabel=button.textContent;button.textContent='正在打包…';
      try{
        status('正在导出指定时刻起全部留存请求的完整日志…');
        await requestHistoryDownload(
          '/api/admin/requests/logs/batch?'+query.toString(),
          'chat2api-request-logs-since.zip');
        status('合并请求日志 ZIP 已下载','ok');
      }catch(error){status(String(error?.message||error),'bad');}
      finally{button.disabled=false;button.textContent=oldLabel;}
    });
    group.append(label,since,button,note);
    toolbar.appendChild(group);
  }
  if(toolbar&&!document.getElementById('rqDownloadDiagnostics')){
    const button=document.createElement('button');button.type='button';button.className='action';button.id='rqDownloadDiagnostics';button.textContent='下载诊断日志包';
    button.title='下载最近请求、失败原因、HTTP Trace、Worker 状态和服务端日志；自动隐藏敏感内容';
    button.addEventListener('click',async()=>{try{status('正在生成诊断日志包…');await requestHistoryDownload('/api/admin/diagnostics/export?limit=200','chat2api-diagnostics.zip');status('诊断日志包已下载','ok');}catch(error){status(String(error?.message||error),'bad');}});
    toolbar.appendChild(button);
  }
  if(section&&!section.querySelector('[data-request-history-diagnostic-hint]')){
    const hint=document.createElement('div');hint.dataset.requestHistoryDiagnosticHint='1';hint.className='footer';
    hint.textContent='排障建议：单条请求点“下载日志”；需要从指定起始时间（含）导出全部仍留存的请求时点“合并打包导出请求日志”（每条均含探针及测试诊断）；“下载诊断日志包”则另外汇总服务端运行日志、Worker 状态与故障摘要。请求日志可能包含已保存的完整提示词与回复，分享前请核对并脱敏。';
    section.appendChild(hint);
  }
}
async function loadRequests(){
  const body=$('rqBody');
  if(!body)return;
  requestHistoryEnsureControls();
  // Queue is a real, persisted pre-dispatch stage and must be filterable.
  const statusFilter=$('rqStatus');
  if(statusFilter&&!Array.from(statusFilter.options).some(option=>option.value==='queued')){
    const queuedOption=document.createElement('option');
    queuedOption.value='queued';queuedOption.textContent='排队中';
    statusFilter.appendChild(queuedOption);
  }
  const search=$('rqSearch')?.value.trim()||'',st=$('rqStatus')?.value||'',m=$('rqModel')?.value.trim()||'';
  const qs=new URLSearchParams({limit:'100'});
  if(search)qs.set('q',search);if(st)qs.set('status',st);if(m)qs.set('model',m);
  body.replaceChildren();
  const loading=document.createElement('tr'),loadingCell=document.createElement('td');
  loadingCell.colSpan=13;loadingCell.className='muted';loadingCell.textContent='正在读取请求记录…';
  loading.appendChild(loadingCell);body.appendChild(loading);
  try{
    const d=await api('/api/admin/requests?'+qs);
    const rows=Array.isArray(d?.data)?d.data:[];
    const fragment=document.createDocumentFragment();
    for(const r of rows){
      const tr=document.createElement('tr');
      const requestId=requestHistoryText(r?.request_id,'');
      const historicalRepair=requestId.startsWith('resp_repair_');
      requestHistoryCell(tr,fmtTime(r?.recorded_at||r?.created_at));
      const shortRequestId=requestId?requestId.slice(-4):'-';
      const idCell=requestHistoryCell(tr,shortRequestId);
      if(r?.is_playground_test===true){
        const marker=document.createElement('span');
        marker.textContent='测';
        marker.title='测试场发起 · '+requestHistoryText(r?.playground_test_type,'测试');
        marker.style.cssText='display:inline-block;margin-left:4px;padding:1px 4px;border:1px solid currentColor;border-radius:4px;font-size:11px;line-height:1.2';
        idCell.appendChild(marker);
      }
      idCell.title=requestId+(r?.parent_request_id?(' · 来源请求 '+r.parent_request_id):'');
      // v0.22.154 internal repair rows did not persist type/key/status.
      // Label legacy absence explicitly; do not fabricate an API Key or
      // claim a browser completion meant a protocol-valid success.
      requestHistoryCell(tr,r?.request_type||r?.type||(historicalRepair?'responses-repair':'-'));
      requestHistoryStatusCell(tr,r?.status||(historicalRepair?'历史未记录':'-'),r?.diagnostics?.request_admission_stage||'');
      requestHistoryCell(tr,r?.api_key_name||r?.key_name||(historicalRepair?'历史未记录':'-'));
      const clientId=requestHistoryText(r?.worker_client_id||r?.client_id,'');
      requestHistoryCell(tr,r?.device_name||(clientId?`未绑定 · ${clientId}`:'-'));
      requestHistoryCell(tr,r?.requested_model||r?.model);
      const attachmentCount=Number.isFinite(Number(r?.attachments_count))?Number(r.attachments_count):(Array.isArray(r?.attachments)?r.attachments.length:0);
      requestHistoryCell(tr,attachmentCount);
      requestHistoryCell(tr,fmtMs(r?.timings?.first_token_ms??r?.first_token_ms));
      requestHistoryCell(tr,fmtMs(r?.timings?.total_ms??r?.total_ms));
      requestHistoryCell(tr,r?.usage?.total_tokens??r?.total_tokens??r?.token_estimate??0);
      requestHistoryButton(tr,'查看对话','查看本次请求的提示词和生成回复',()=>requestHistoryShowConversation(requestId),Boolean(requestId));
      requestHistoryButton(tr,'下载日志','下载该请求的脱敏诊断日志',async()=>{
        try{status(`正在导出 ${requestId} 的日志…`);await requestHistoryDownload(`/api/admin/requests/${encodeURIComponent(requestId)}/log`,`chat2api-request-${requestId}.json`);status('请求日志已下载','ok');}
        catch(error){status(String(error?.message||error),'bad');}
      },Boolean(requestId));
      fragment.appendChild(tr);
    }
    body.replaceChildren();
    if(rows.length)body.appendChild(fragment);
    else{
      const empty=document.createElement('tr'),cell=document.createElement('td');
      cell.colSpan=13;cell.className='muted';cell.textContent='当前筛选条件下没有请求记录。';
      empty.appendChild(cell);body.appendChild(empty);
    }
  }catch(error){
    body.replaceChildren();
    const failed=document.createElement('tr'),cell=document.createElement('td');
    cell.colSpan=13;cell.className='bad';cell.textContent=`请求记录加载失败：${String(error?.message||error)}`;
    failed.appendChild(cell);body.appendChild(failed);
  }
}
requestHistoryEnsureControls();
$('rqGo').onclick=loadRequests;'''


def _replace_once(pattern: str, replacement: str, source: str, label: str) -> str:
    compiled = re.compile(pattern, re.DOTALL)
    result, count = compiled.subn(lambda _match: replacement, source, count=1)
    if count != 1:
        raise RuntimeError(f"{PATCH_ID}: expected exactly one {label}, found {count}")
    return result


def compile_legacy_admin_asset(filename: str, source: str) -> str:
    """Compile a historical asset into a request-history-passive asset."""

    if filename == "admin_v7.js":
        result = _replace_once(
            r"\n  function simplifyRequestPage\(\) \{.*?\n  if \(\$\(\"rqGo\"\)\) \$\(\"rqGo\"\)\.onclick = loadRequests;\n",
            "\n  // Request History ownership retired by the canonical source compiler.\n",
            source,
            "v7 request-history override",
        )
        result = result.replace('if (view === "requests") simplifyRequestPage();', "")
        if "simplifyRequestPage" in result or "loadRequestsV7" in result:
            raise RuntimeError(f"{PATCH_ID}: unable to retire all v7 request-history ownership")
        return result
    if filename == "admin_v8.js":
        if "loadRequestsV8" not in source and "rqBody" not in source and "data-chat2api-log" not in source:
            return source
        return _replace_once(
            r"\n  function ensureDiagnosticControls\(\) \{.*?\n  if \(\$\(\"rqGo\"\)\) \$\(\"rqGo\"\)\.onclick = loadRequests;\n",
            "\n  // Diagnostics endpoints remain; Request History UI is rendered canonically.\n",
            source,
            "v8 request-history override",
        )
    if filename == "admin_v10.js":
        result = source.replace('"recentBody", "keysBody", "rqBody", "testHistory"', '"recentBody", "keysBody", "testHistory"')
        result = result.replace('"#recentBody,#rqBody,#testHistory"', '"#recentBody,#testHistory"')
        if result == source or "rqBody" in result:
            raise RuntimeError(f"{PATCH_ID}: unable to retire v10 request-time decorator")
        return result
    raise ValueError(f"unsupported legacy admin asset: {filename}")


def _compiled_asset_asgi(source: str):
    """Return the actual ASGI endpoint used by APIRoute after construction.

    FastAPI builds APIRoute.app when a route is created. Updating only
    route.endpoint/dependant.call later changes introspection but does not change
    the already-built ASGI callable. v0.22.65 did exactly that, so browsers still
    received admin_v7.js with its old nine-column loadRequests renderer. Replacing
    route.app makes the served asset and the declared endpoint the same owner.
    """

    async def asgi(scope, receive, send) -> None:
        response = Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})
        await response(scope, receive, send)

    return asgi


def _compile_legacy_assets(app: FastAPI) -> None:
    specs = {
        "/assets/chat2api-v7.js": "admin_v7.js",
        "/assets/chat2api-v8.js": "admin_v8.js",
        "/assets/chat2api-v10.js": "admin_v10.js",
    }
    compiled = {
        route_path: compile_legacy_admin_asset(filename, Path(__file__).with_name(filename).read_text(encoding="utf-8"))
        for route_path, filename in specs.items()
    }

    found: set[str] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path not in compiled or "GET" not in route.methods:
            continue
        route_path = route.path
        source = compiled[route_path]

        async def compiled_asset(_source: str = source) -> Response:
            return Response(_source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

        compiled_asset.__chat2api_request_history_compiled_v94__ = True
        route.dependant.call = compiled_asset
        route.endpoint = compiled_asset
        # This is the decisive v98 fix. APIRoute.app is the callable Starlette
        # actually dispatches; replacing only endpoint/dependant.call is not
        # sufficient after route construction.
        route.app = _compiled_asset_asgi(source)
        found.add(route_path)

    missing = set(specs) - found
    if missing:
        raise RuntimeError(f"{PATCH_ID}: legacy admin asset routes missing: {sorted(missing)}")
    app.state.request_history_compiled_assets = compiled
    app.state.request_history_asset_compiler_revision = ASSET_COMPILER_REVISION


def _install_conversation_capture(app: FastAPI) -> None:
    if getattr(app.state, "request_history_conversation_capture_installed", False):
        return

    telemetry = app.state.telemetry
    broker = app.state.broker

    base_publish = broker.publish

    async def publish_with_conversation_capture(request_id: str, event: dict) -> bool:
        published = await base_publish(request_id, event)
        if not published:
            return published
        event_type = str(event.get("type") or "")
        if event_type in {"chat.completed", "chat.error", "chat.cancelled"}:
            state = broker.requests.get(request_id)
            # The browser terminal can carry a full generated PNG data URI.
            # Only the API archival path may persist its final owned URL.
            response_text = history_safe_response_text(
                str(event.get("text") or (state.text if state is not None else ""))
            )
            record = {
                "request_id": request_id,
                "response_text": response_text,
                "response_text_available": bool(response_text),
            }
            # Responses HTTP middleware owns the public protocol outcome;
            # capture only correlated browser timings here. The request-v6
            # terminal handler is the sole owner of first-token timing, so
            # no UI placeholder or estimated "首包" is necessary.
            if request_id.startswith(("resp_req_", "resp_repair_")) and state is not None:
                record["client_id"] = state.client_id
                record["worker_client_id"] = state.client_id
                record["timings"] = state.timings()
            await telemetry.upsert(record)
        return published

    broker.publish = publish_with_conversation_capture

    base_query = telemetry.query

    def query_without_full_response(*args, **kwargs):
        result = base_query(*args, **kwargs)
        rows = result.get("data") if isinstance(result, dict) else None
        if isinstance(rows, list):
            tests_by_request = request_test_index(getattr(app.state, "test_runs", None))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row.update(playground_link(tests_by_request.get(str(row.get("request_id") or ""))))
                row["response_text_available"] = bool(row.get("response_text"))
                row["final_prompt_available"] = bool(row.get("final_prompt"))
                # List rows need only the availability flags; complete prompt
                # and response are loaded via authenticated request-detail API.
                # Long FDEX catalogs can be ~50 KB per row and otherwise slow
                # down the admin request list for no useful column content.
                row.pop("response_text", None)
                row.pop("final_prompt", None)
        return result

    telemetry.query = query_without_full_response
    app.state.request_history_conversation_capture_installed = True
    app.state.request_history_conversation_patch = CONVERSATION_PATCH_ID


def _normalize_admin_html(html: str) -> str:
    if html.count(_BASE_HEADER) != 1:
        raise RuntimeError(
            f"{PATCH_ID}: expected exactly one base request-history header, found {html.count(_BASE_HEADER)}"
        )
    if html.count(_BASE_REQUEST_VIEW_OPEN) != 1:
        raise RuntimeError(
            f"{PATCH_ID}: expected exactly one split request-history layout, found {html.count(_BASE_REQUEST_VIEW_OPEN)}"
        )
    if html.count(_BASE_REQUEST_DETAIL_PANEL) != 1:
        raise RuntimeError(
            f"{PATCH_ID}: expected exactly one legacy request-detail panel, found {html.count(_BASE_REQUEST_DETAIL_PANEL)}"
        )

    html = html.replace(_BASE_HEADER, _FINAL_HEADER, 1)
    html = html.replace(_BASE_REQUEST_VIEW_OPEN, _FINAL_REQUEST_VIEW_OPEN, 1)
    html = html.replace(_BASE_REQUEST_DETAIL_PANEL, "", 1)

    html, replacements = _BASE_LOADER_RE.subn(lambda _match: _FINAL_LOADER, html, count=1)
    if replacements != 1:
        raise RuntimeError(f"{PATCH_ID}: expected exactly one base loadRequests owner, found {replacements}")

    html, detail_replacements = _BASE_REQUEST_DETAIL_RE.subn("", html, count=1)
    if detail_replacements != 1:
        raise RuntimeError(
            f"{PATCH_ID}: expected exactly one legacy requestDetail owner, found {detail_replacements}"
        )

    if html.count("async function loadRequests()") != 1:
        raise RuntimeError(f"{PATCH_ID}: final admin HTML must contain exactly one loadRequests owner")
    if 'id="rqDetail"' in html or "requestDetail(" in html or "window.requestDetail" in html:
        raise RuntimeError(f"{PATCH_ID}: legacy request-detail UI/owner survived canonical normalization")
    return html


def install_request_history_v94_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "request_history_v94_installed", False):
        return app
    _compile_legacy_assets(app)
    _install_conversation_capture(app)
    admin_module.ADMIN_HTML = _normalize_admin_html(admin_module.ADMIN_HTML)
    app.state.request_history_v94_installed = True
    app.state.request_history_owner = PATCH_ID
    return app
