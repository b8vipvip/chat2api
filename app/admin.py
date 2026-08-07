from __future__ import annotations

from fastapi.responses import HTMLResponse


ADMIN_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>chat2api 管理面板</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#131a2c;--muted:#8290aa;--line:#24304a;--ok:#35d09a;--bad:#ff6b75;--warn:#ffcf5a;--text:#eef3ff;--accent:#6ea8ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1400px;margin:auto;padding:24px}.top{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap}.brand{font-size:24px;font-weight:800}.muted{color:var(--muted)}
.auth{display:flex;gap:8px;min-width:min(100%,520px)}input,button{border:1px solid var(--line);border-radius:9px;background:#0e1526;color:var(--text);padding:9px 11px}input{flex:1}button{cursor:pointer;background:#1b3158}button:hover{border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:12px;margin:20px 0}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}.card .n{font-size:25px;font-weight:800;margin-top:4px}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:14px}.panel h2{margin:0 0 12px;font-size:16px}.wide{grid-column:1/-1}.scroll{overflow:auto;max-height:520px}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--panel)}code{color:#c7d8ff}.pill{display:inline-block;padding:2px 7px;border-radius:999px;background:#1a2943}.err{max-width:520px;white-space:normal;color:#ff9da5}.diag{font-size:12px;white-space:normal;min-width:280px}.footer{margin-top:18px;color:var(--muted)}
@media(max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}.panels{grid-template-columns:1fr}.wide{grid-column:auto}}
</style>
</head><body><div class="wrap">
<div class="top"><div><div class="brand">chat2api 管理面板</div><div class="muted">扩展、模型、请求耗时与估算 Token 监控</div></div><div class="auth"><input id="key" type="password" placeholder="CHAT2API_API_KEY"/><button id="save">连接</button><button id="refresh">刷新</button></div></div>
<div id="message" class="muted" style="margin-top:10px">请输入 API Key。</div>
<div class="grid">
<div class="card"><div class="muted">在线扩展</div><div id="extensions" class="n">-</div></div>
<div class="card"><div class="muted">桌面客户端</div><div id="agents" class="n">-</div></div>
<div class="card"><div class="muted">保留请求</div><div id="requests" class="n">-</div></div>
<div class="card"><div class="muted">成功率</div><div id="success" class="n">-</div></div>
<div class="card"><div class="muted">平均首 Token</div><div id="firstToken" class="n">-</div></div>
<div class="card"><div class="muted">估算 Token</div><div id="tokens" class="n">-</div></div>
</div>
<div class="panels">
<div class="panel"><h2>Chrome 扩展</h2><div class="scroll"><table><thead><tr><th>ID</th><th>名称</th><th>状态</th><th>当前模型</th><th>版本</th></tr></thead><tbody id="clientsBody"></tbody></table></div></div>
<div class="panel"><h2>已发现模型</h2><div class="scroll"><table><thead><tr><th>Model ID</th><th>标签</th><th>客户端</th></tr></thead><tbody id="modelsBody"></tbody></table></div></div>
<div class="panel wide"><h2>最近请求</h2><div class="scroll"><table><thead><tr><th>时间</th><th>状态</th><th>请求模型</th><th>实际模型</th><th>首 Token</th><th>总耗时</th><th>模型选择</th><th>Token</th><th>诊断 / 错误</th></tr></thead><tbody id="historyBody"></tbody></table></div></div>
</div>
<div class="footer">Token 来自 <code>chat2api-heuristic-v1</code> 估算，不是 ChatGPT 官方 usage。面板数据接口需要同一个 API Key。</div>
</div>
<script>
const $=id=>document.getElementById(id); const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtMs=v=>v==null?'-':(Number(v)<1000?Math.round(v)+' ms':(Number(v)/1000).toFixed(1)+' s');
function key(){return sessionStorage.getItem('chat2api_admin_key')||''} $('key').value=key();
$('save').onclick=()=>{sessionStorage.setItem('chat2api_admin_key',$('key').value.trim());load()}; $('refresh').onclick=load;
async function api(path){const r=await fetch(path,{headers:{Authorization:'Bearer '+key()},cache:'no-store'}); if(!r.ok)throw new Error(r.status+' '+(await r.text())); return r.json()}
function statusPill(ok,text){return `<span class="pill ${ok?'ok':'bad'}">${esc(text)}</span>`}
async function load(){if(!key()){ $('message').textContent='请输入 API Key。';return } try{const data=await api('/api/admin/overview');$('message').textContent='已连接 · '+new Date().toLocaleTimeString(); const s=data.telemetry||{};$('extensions').textContent=data.health?.online_extensions??0;$('agents').textContent=data.health?.online_desktop_agents??0;$('requests').textContent=s.retained_requests??0;$('success').textContent=(s.success_rate??100)+'%';$('firstToken').textContent=fmtMs(s.avg_first_token_ms);$('tokens').textContent=s.estimated_total_tokens??0;
$('clientsBody').innerHTML=(data.clients||[]).map(c=>`<tr><td><code>${esc(c.client_id)}</code></td><td>${esc(c.name)}</td><td>${statusPill(c.online,c.online?(c.busy?'busy':'online'):'offline')}</td><td>${esc(c.metadata?.current_model||'-')}</td><td>${esc(c.version||'-')}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">暂无客户端</td></tr>';
$('modelsBody').innerHTML=(data.models||[]).map(m=>`<tr><td><code>${esc(m.id)}</code></td><td>${esc(m.label||'')}</td><td>${esc((m.clients||[]).join(', ')||'-')}</td></tr>`).join('')||'<tr><td colspan="3" class="muted">暂无模型目录</td></tr>';
$('historyBody').innerHTML=(data.recent_requests||[]).map(r=>{const t=r.timings||{},u=r.usage||{},d=r.diagnostics||{};const actual=d.actual_model||[d.actual_family,d.actual_reasoning].filter(Boolean).join('-')||'-';const detail=r.error?`<div class="err">${esc(r.error)}</div>`:`<div class="diag">zero_op=${esc(d.zero_op??'-')} · family_switched=${esc(d.model_switched??'-')} · reasoning_switched=${esc(d.reasoning_switched??'-')} · source=${esc(d.state_source||'-')}</div>`;return `<tr><td>${esc((r.recorded_at||'').replace('T',' ').slice(0,19))}</td><td>${statusPill(r.status==='completed',r.status||'-')}</td><td><code>${esc(r.requested_model||'-')}</code></td><td><code>${esc(actual)}</code></td><td>${fmtMs(t.first_token_ms)}</td><td>${fmtMs(t.total_ms)}</td><td>${fmtMs(t.model_selection_ms)}</td><td>${esc(u.total_tokens??0)} <span class="muted">(${esc(u.prompt_tokens??0)}+${esc(u.completion_tokens??0)})</span></td><td>${detail}</td></tr>`}).join('')||'<tr><td colspan="9" class="muted">暂无请求记录</td></tr>';
}catch(e){$('message').innerHTML='<span class="bad">连接失败：'+esc(e.message)+'</span>'}}
if(key())load(); setInterval(()=>{if(key()&&!document.hidden)load()},5000);
</script></body></html>'''


def admin_response() -> HTMLResponse:
    return HTMLResponse(ADMIN_HTML, headers={"Cache-Control": "no-store"})
