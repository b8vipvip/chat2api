from __future__ import annotations

import re

from fastapi import FastAPI

from . import admin as admin_module


PATCH_ID = "admin-request-single-owner-v94"

# Request history is deliberately normalized once, on the server, before the
# admin HTML is served. Browser-side feature modules are not allowed to add,
# repair, observe, or re-render #rqBody. This gives the table one structural and
# data owner instead of another generation of guards around competing owners.
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

# The base admin owns loadRequests. Replace that one source definition rather
# than wrapping it. The expression is intentionally strict and must match once;
# a future base-page change fails startup/tests instead of silently creating a
# second request-history authority.
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
async function loadRequests(){
  const body=$('rqBody');
  if(!body)return;
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
      requestHistoryButton(tr,'详情','查看该请求的完整诊断记录',()=>requestDetail(requestId),Boolean(requestId));
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
$('rqGo').onclick=loadRequests;'''


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
    admin_module.ADMIN_HTML = _normalize_admin_html(admin_module.ADMIN_HTML)
    app.state.request_history_v94_installed = True
    app.state.request_history_owner = PATCH_ID
    return app
