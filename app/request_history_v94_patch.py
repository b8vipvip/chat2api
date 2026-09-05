from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.routing import APIRoute

from . import admin as admin_module


PATCH_ID = "admin-request-single-owner-v94"

# Request history is normalized once on the server before browser delivery.
# Historical admin assets are also compiled once at startup so old versions may
# keep unrelated features without retaining any request-table ownership in the
# browser. No runtime observer/guard chain participates in this decision.
_BASE_HEADER = (
    '<thead><tr><th>时间</th><th>类型</th><th>状态</th><th>Key</th><th>模型</th>'
    '<th>附件</th><th>首包</th><th>总耗时</th><th>Token</th></tr></thead>'
    '<tbody id="rqBody"></tbody>'
)
_FINAL_HEADER = (
    '<thead><tr><th>时间（北京时间）</th><th>请求ID</th><th>类型</th><th>状态</th><th>Key</th>'
    '<th>设备标识</th><th>模型</th><th>附件</th><th>首包</th><th>总耗时</th><th>Token</th>'
    '<th>提示词</th><th>日志</th></tr></thead><tbody id="rqBody"></tbody>'
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
function requestHistoryStatusCell(tr,value){
  const td=document.createElement('td'),span=document.createElement('span');
  const text=requestHistoryText(value);
  span.className='pill '+(text==='completed'?'ok':text==='error'?'bad':text==='running'?'warn':'');
  span.textContent=text;
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
function requestHistoryEnsureControls(){
  const section=document.getElementById('view-requests'),toolbar=section?.querySelector('.toolbar');
  if(toolbar&&!document.getElementById('rqDownloadDiagnostics')){
    const button=document.createElement('button');button.type='button';button.className='action';button.id='rqDownloadDiagnostics';button.textContent='下载诊断日志包';
    button.title='下载最近请求、失败原因、HTTP Trace、Worker 状态和服务端日志；自动隐藏敏感内容';
    button.addEventListener('click',async()=>{try{status('正在生成诊断日志包…');await requestHistoryDownload('/api/admin/diagnostics/export?limit=200','chat2api-diagnostics.zip');status('诊断日志包已下载','ok');}catch(error){status(String(error?.message||error),'bad');}});
    toolbar.appendChild(button);
  }
  if(section&&!section.querySelector('[data-request-history-diagnostic-hint]')){
    const hint=document.createElement('div');hint.dataset.requestHistoryDiagnosticHint='1';hint.className='footer';
    hint.textContent='排障建议：单条失败点“下载日志”；多个外部调用一起失败时点“下载诊断日志包”。日志会自动隐藏 API Key、Authorization、设备码、提示词正文和 base64 文件内容。';
    section.appendChild(hint);
  }
}
async function loadRequests(){
  const body=$('rqBody');
  if(!body)return;
  requestHistoryEnsureControls();
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
      const tr=document.createElement('tr');tr.className='clickable';
      const requestId=requestHistoryText(r?.request_id,'');
      tr.addEventListener('click',()=>{if(requestId)requestDetail(requestId);});
      requestHistoryCell(tr,fmtTime(r?.recorded_at||r?.created_at));
      const idCell=requestHistoryCell(tr,requestId||'-');idCell.title=requestId;
      requestHistoryCell(tr,r?.request_type||r?.type);
      requestHistoryStatusCell(tr,r?.status);
      requestHistoryCell(tr,r?.api_key_name||r?.key_name);
      const clientId=requestHistoryText(r?.worker_client_id||r?.client_id,'');
      requestHistoryCell(tr,r?.device_name||(clientId?`未绑定 · ${clientId}`:'-'));
      requestHistoryCell(tr,r?.requested_model||r?.model);
      const attachmentCount=Number.isFinite(Number(r?.attachments_count))?Number(r.attachments_count):(Array.isArray(r?.attachments)?r.attachments.length:0);
      requestHistoryCell(tr,attachmentCount);
      requestHistoryCell(tr,fmtMs(r?.timings?.first_token_ms??r?.first_token_ms));
      requestHistoryCell(tr,fmtMs(r?.timings?.total_ms??r?.total_ms));
      requestHistoryCell(tr,r?.usage?.total_tokens??r?.total_tokens??r?.token_estimate??0);
      requestHistoryButton(
        tr,'查看','查看最终完整提示词',
        ()=>{if(typeof window.showRequestPromptV72==='function')window.showRequestPromptV72(requestId);else requestDetail(requestId);},
        Boolean(requestId&&r?.final_prompt_available!==false)
      );
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
    """Remove historical request-table owners while preserving unrelated features."""

    if filename == "admin_v7.js":
        return _replace_once(
            r"\n  function simplifyRequestPage\(\) \{.*?\n  if \(\$\(\"rqGo\"\)\) \$\(\"rqGo\"\)\.onclick = loadRequests;\n",
            "\n  // Request History ownership retired by v94 source compiler.\n",
            source,
            "v7 request-history override",
        )
    if filename == "admin_v8.js":
        return _replace_once(
            r"\n  function ensureDiagnosticControls\(\) \{.*?\n  if \(\$\(\"rqGo\"\)\) \$\(\"rqGo\"\)\.onclick = loadRequests;\n",
            "\n  // Diagnostics endpoints remain; Request History UI is rendered by v94.\n",
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
        found.add(route_path)

    missing = set(specs) - found
    if missing:
        raise RuntimeError(f"{PATCH_ID}: legacy admin asset routes missing: {sorted(missing)}")
    app.state.request_history_compiled_assets = compiled


def _normalize_admin_html(html: str) -> str:
    if html.count(_BASE_HEADER) != 1:
        raise RuntimeError(
            f"{PATCH_ID}: expected exactly one base request-history header, found {html.count(_BASE_HEADER)}"
        )
    html = html.replace(_BASE_HEADER, _FINAL_HEADER, 1)
    html, replacements = _BASE_LOADER_RE.subn(lambda _match: _FINAL_LOADER, html, count=1)
    if replacements != 1:
        raise RuntimeError(f"{PATCH_ID}: expected exactly one base loadRequests owner, found {replacements}")
    if html.count("async function loadRequests()") != 1:
        raise RuntimeError(f"{PATCH_ID}: final admin HTML must contain exactly one loadRequests owner")
    return html


def install_request_history_v94_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "request_history_v94_installed", False):
        return app
    _compile_legacy_assets(app)
    admin_module.ADMIN_HTML = _normalize_admin_html(admin_module.ADMIN_HTML)
    app.state.request_history_v94_installed = True
    app.state.request_history_owner = PATCH_ID
    return app
