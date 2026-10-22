(() => {
  "use strict";
  const KEY="__CHAT2API_TEXT_MODEL_TASK_UI_V147__";
  if(globalThis[KEY])return;
  globalThis[KEY]={revision:147};
  const el=id=>document.getElementById(id);
  const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const get=async path=>api(path);
  let busy=false;
  function build(){
    const view=el("view-text-models");
    if(!view)return;
    if(el("modelTasksV147"))return;
    view.innerHTML=`
      <div class="panel" id="modelTasksV147">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
          <div><h2>模型与多模态能力管理</h2><div class="muted">模型发现仅手动执行；页面模型可选不代表图片能力已实机验证。图像生成只在浏览器传输声明中展示，不能当作模型/配额成功证据。</div></div>
          <div class="toolbar"><button class="action good" id="modelDiscoverAllV147">发现模型</button>
          <button class="action" id="modelReloadV147">刷新状态</button></div>
        </div>
        <div class="muted" id="modelOverviewV147">正在读取模型信息…</div>
        <div class="scroll"><table><thead><tr><th>模型</th><th>状态</th><th>模型来源</th><th>多模态能力证据</th><th>可尝试调用 Worker</th><th>管理</th></tr></thead>
          <tbody id="modelTableV147"></tbody></table></div>
      </div>
      <div class="panel" style="margin-top:14px">
        <h3>当前模型任务进度</h3><div class="muted">仅展示管理员点击发起的任务；Worker 错误、步骤日志按任务保留并可导出。</div>
        <div class="scroll"><table><thead><tr><th>任务 ID / 目标</th><th>Worker</th><th>进度</th><th>结果 / 错误</th><th>日志</th></tr></thead>
        <tbody id="modelTaskProgressV147"></tbody></table></div>
      </div>
      <div class="panel" style="margin-top:14px">
        <h3>任务记录</h3><div class="muted">每条代表一轮发现模型或验证模型任务；支持导出该轮全部 Worker 的任务执行日志。</div>
        <div class="scroll"><table><thead><tr><th>任务时间 (UTC)</th><th>任务类型</th><th>目标模型</th><th>Worker 结果</th><th>操作</th></tr></thead>
        <tbody id="modelTaskHistoryV147"></tbody></table></div>
      </div>
    `;
    el("modelDiscoverAllV147").onclick=async()=>act("/api/admin/text-model-tasks/discover");
    el("modelReloadV147").onclick=()=>refresh(true);
    view.addEventListener("click",async event=>{
      const b=event.target.closest("button[data-model-default],button[data-model-verify],button[data-model-toggle],button[data-task-log]");
      if(!b)return;
      if(b.dataset.taskLog){
        await logs(b.dataset.taskLog,b.dataset.logWorker||"");return;
      }
      if(b.dataset.modelDefault){
        b.disabled=true;
        try{
          await api("/api/admin/text-model-minimum",{method:"PATCH",body:{model_id:b.dataset.modelDefault}});
          await refresh(true);
        }catch(error){alert("设置默认模型失败："+String(error.message||error))}
        finally{b.disabled=false}
        return;
      }
      if(b.dataset.modelVerify){
        await act("/api/admin/text-models/"+encodeURIComponent(b.dataset.modelVerify)+"/validate");
        return;
      }
      if(b.dataset.modelToggle){
        b.disabled=true;
        try{
          await api("/api/admin/text-models/"+encodeURIComponent(b.dataset.modelToggle),{
            method:"PATCH",body:{enabled:b.dataset.modelEnable==="true"},
          });
          await refresh(true);
        }catch(error){alert("模型更新失败："+String(error.message||error))}
        finally{b.disabled=false}
      }
    });
  }
  async function logs(task,worker){
    try{
      const url="/api/admin/text-model-tasks/"+encodeURIComponent(task)+"/logs"+
        (worker?"?worker="+encodeURIComponent(worker):"");
      const h=typeof headers==="function"?headers(key()):{};
      const response=await fetch(url,{headers:h,cache:"no-store"});
      if(!response.ok)throw new Error(response.status+" "+await response.text());
      const blob=await response.blob();
      const temp=URL.createObjectURL(blob),link=document.createElement("a");
      link.href=temp;link.download=task+(worker?"-"+worker:"")+"-worker-model.log";
      document.body.appendChild(link);link.click();link.remove();
      setTimeout(()=>URL.revokeObjectURL(temp),3000);
    }catch(error){alert("导出 Worker 任务日志失败："+String(error.message||error))}
  }
  async function act(path){
    if(busy)return;
    busy=true;
    const button=el("modelDiscoverAllV147");
    if(button)button.disabled=true;
    try{
      const task=await api(path,{method:"POST"});
      if(el("modelOverviewV147"))el("modelOverviewV147").textContent=
        "任务已下发 "+task.id+"；页面将显示每个 Worker 的返回进度";
      await refresh(true);
    }catch(error){alert("任务下发失败："+String(error.message||error))}
    finally{busy=false;if(button)button.disabled=false}
  }
  const button=(title,attr,value,other="")=>`<button class="action" ${attr}="${esc(value)}" ${other}>${esc(title)}</button>`;
  function render(models,tasks){
    const workers=models.workers||[];
    const workerById=new Map(workers.map(w=>[w.client_id,w]));
    const live=new Set(workers.filter(w=>w.online&&w.routing_ready).map(w=>w.client_id));
    const rows=models.data||[];
    const defaultModel=models.legacy_minimum_model||"";
    const evidenceByModel=tasks.capability_evidence||{};
    const operation=(details,name)=>{
      const e=details?.operations?.[name]||{};
      if(e.state==="picker_validated")return "选择已验证";
      if(e.state==="model_reported")return "模型已上报";
      if(e.state==="transport_advertised")return "仅浏览器声明";
      return "未验证";
    };
    const modelBox=el("modelTableV147");
    if(modelBox)modelBox.innerHTML=rows.map(row=>{
      const sources=(row.observed_by||[]).map(id=>{
        const w=workerById.get(id);
        const verified=(row.verified_by||[]).includes(id);
        const selectable=row.source_details?.[id]?.selectable!==false;
        return `<div title="${esc(id)} · ${esc(w?.updated_at||"")}"><code>${esc(id)}</code>
          <small class="muted"> · ${esc(w?.name||"")} · ${verified?"已验证":selectable?"已发现":"页面选项不可操作"}</small></div>`;
      }).join("")||"—";
      const callable=row.enabled?(row.observed_by||[]).filter(id=>live.has(id) && row.source_details?.[id]?.selectable!==false):[];
      const label=!row.enabled?"已停用":row.verified_by?.length?"存在已验证来源":"已发现 · 未验证";
      const isDefault=row.id===defaultModel;
      const evidence=evidenceByModel[row.id];
      const capabilities=evidence
        ? `<div>文本：${esc(operation(evidence,"text_input"))}</div>
           <div>图片理解：${esc(operation(evidence,"image_input"))}</div>
           <div>图片生成：${esc(operation(evidence,"image_generation"))}</div>
           <div class="muted" title="仅说明 Extension 当前声明可执行图片任务，并不证明选中模型或账户配额已完成图片生成">图片编辑：${esc(operation(evidence,"image_edit"))}</div>`
        : '<span class="muted">未取得能力证据</span>';
      return `<tr><td><code>${esc(row.id)}</code>${isDefault?' <span class="muted" style="font-weight:700">（默认）</span>':''}</td><td>${label}</td>
        <td>${sources}</td><td>${capabilities}</td><td title="${esc(callable.join(", "))}">${callable.length} 个</td>
        <td><div class="rowactions" style="display:flex;gap:6px">
          ${button("验证","data-model-verify",row.id)}
          ${isDefault?'<span class="muted" style="align-self:center">当前默认</span>':row.enabled?button("设为默认","data-model-default",row.id):''}
          ${button(row.enabled?"停用":"启用","data-model-toggle",row.id,
            'data-model-enable="'+(!row.enabled)+'"')}</div></td></tr>`;
    }).join("")||'<tr><td colspan="6">暂无来自 Worker 的模型发现记录。点击「发现模型」执行任务。</td></tr>';
    const hist=tasks.data||[];
    const current=hist[0]||null;
    const prog=el("modelTaskProgressV147");
    if(prog)prog.innerHTML=current?Object.entries(current.workers||{}).map(([id,w])=>{
      const info=workerById.get(id);
      return `<tr><td><code>${esc(current.id)}</code><div>${esc(current.kind)} · ${esc(current.model||"全部模型")}</div></td>
        <td><code>${esc(id)}</code><div class="muted">${esc(info?.name||"")}</div></td>
        <td>${esc(w.state)} · ${Number(w.progress||0)}%<div><progress max="100" value="${Number(w.progress||0)}"></progress></div></td>
        <td>${esc((w.models||[]).join(", ")||"—")}<div style="color:var(--bad)">${esc(w.error||"")}</div></td>
        <td>${button("导出此 Worker 日志","data-task-log",current.id,'data-log-worker="'+esc(id)+'"')}</td></tr>`;
    }).join(""):'<tr><td colspan="5">尚未发起任何手动发现或验证任务。</td></tr>';
    const history=el("modelTaskHistoryV147");
    if(history)history.innerHTML=hist.map(task=>{
      const states=Object.values(task.workers||{}).map(w=>w.state);
      const result=states.length?states.join(" / "):"无目标";
      return `<tr><td>${esc(task.created_at)}</td>
        <td>${task.kind==="discover"?"发现模型":"验证模型"}</td><td>${esc(task.model||"全部在线 Worker")}</td>
        <td>${esc(result)}</td><td>${button("导出本轮日志","data-task-log",task.id)}</td></tr>`;
    }).join("")||'<tr><td colspan="5">暂无手动模型任务记录。</td></tr>';
    const overview=el("modelOverviewV147");
    if(overview)overview.textContent=`模型 ${rows.length} · 在线 Worker ${workers.filter(x=>x.online).length} · 已登录就绪 ${live.size} · 手动任务 ${hist.length}`;
  }
  async function refresh(showError=false){
    if(!el("view-text-models")||!el("modelTasksV147"))return;
    try{
      const [models,tasks]=await Promise.all([get("/api/admin/text-models"),get("/api/admin/text-model-tasks")]);
      render(models,tasks);
    }catch(error){
      if(showError&&el("modelOverviewV147"))el("modelOverviewV147").textContent=
        "无法读取模型任务："+String(error.message||error);
    }
  }
  function start(){
    const attempt=()=>{
      build();
      if(el("modelTasksV147"))refresh(true);
      else setTimeout(attempt,120);
    };
    setTimeout(attempt,30);
    setInterval(()=>{if(el("view-text-models")?.classList.contains("active"))refresh(false)},2500);
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",start);
  else start();
})();