(() => {
  if (globalThis.__CHAT2API_LINUX_WORKER_UPGRADE_V44__) return;
  globalThis.__CHAT2API_LINUX_WORKER_UPGRADE_V44__ = true;

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch]);
  const api = async (path, options = {}) => {
    const response = await fetch(path, {credentials:"same-origin",cache:"no-store",headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = {detail:text}; }
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  };
  const workerIdFromRow = row => {
    if (!row) return "";
    for (const selector of ["[data-worker-diagnostics-v2222]","[data-worker-initialize-v43]","[data-worker-pairing-v2219]"]) {
      const node = row.querySelector(selector);
      if (!node) continue;
      const value = Object.values(node.dataset || {}).find(item => /^wrk_/.test(String(item || "")));
      if (value) return String(value);
    }
    return "";
  };
  const workerNameFromRow = row => String(row?.querySelector("td")?.textContent || "Linux Worker").trim();
  const stageLabel = stage => ({queued:"排队",scheduled:"已安排",starting:"准备更新",download:"下载安装器","system-check":"系统检查",cleanup:"清理残留",packages:"基础依赖","worker-bundle":"Worker 组件",python:"Python 环境",xray:"Xray",enrollment:"身份校验",systemd:"服务更新",health:"健康检查",complete:"更新完成",failed:"更新失败","one-time-enable":"启用在线更新"})[String(stage||"")] || String(stage||"-");
  const terminal = state => ["succeeded","failed","unsupported"].includes(String(state || ""));
  const cache = new Map();
  const watching = new Map();
  let selectedWorkerId = "";

  const dialog = document.createElement("dialog");
  dialog.id = "linuxWorkerUpgradeDialogV44";
  dialog.style.cssText = "width:min(760px,calc(100vw - 32px));max-width:none;border:1px solid #334155;border-radius:12px;background:#0f172a;color:#e5e7eb;padding:0;box-shadow:0 24px 80px rgba(0,0,0,.6)";
  dialog.innerHTML = `<div style="padding:20px">
    <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start"><div><div style="font-size:18px;font-weight:700">Linux Worker 在线更新</div><div id="linuxWorkerUpgradeNameV44" style="margin-top:4px;color:#94a3b8"></div></div><button class="action" id="closeLinuxWorkerUpgradeV44" type="button">关闭</button></div>
    <div id="linuxWorkerUpgradeVersionsV44" style="margin-top:14px;padding:12px;border:1px solid #334155;border-radius:9px;background:#020617;color:#cbd5e1;line-height:1.7"></div>
    <div style="margin-top:14px"><div style="display:flex;justify-content:space-between;gap:10px"><span id="linuxWorkerUpgradeStageV44">等待</span><span id="linuxWorkerUpgradePercentV44">0%</span></div><progress id="linuxWorkerUpgradeProgressV44" value="0" max="100" style="width:100%;height:16px;margin-top:7px"></progress></div>
    <div id="linuxWorkerUpgradeMessageV44" style="margin-top:10px;color:#cbd5e1"></div>
    <div id="linuxWorkerUpgradeBootstrapV44" style="display:none;margin-top:12px;padding:12px;border:1px solid #854d0e;border-radius:9px;background:rgba(113,63,18,.18)"><div style="color:#fde68a">当前 Agent 还没有在线更新命令。只需最后手工执行一次下面的幂等升级，以后即可一直使用“更新”按钮。</div><code id="linuxWorkerUpgradeBootstrapCodeV44" style="display:block;margin-top:8px;white-space:pre-wrap;word-break:break-all"></code><button class="action" id="copyLinuxWorkerUpgradeBootstrapV44" type="button" style="margin-top:8px">复制启用命令</button></div>
    <details open style="margin-top:14px"><summary>实时进度记录</summary><div id="linuxWorkerUpgradeHistoryV44" style="max-height:300px;overflow:auto;margin-top:8px;padding:10px;border:1px solid #334155;border-radius:8px;background:#020617;font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:normal"></div></details>
  </div>`;
  document.body.appendChild(dialog);

  const render = payload => {
    if (!payload) return;
    const current = payload.current || {};
    const target = payload.target || {};
    const upgrade = payload.upgrade || {};
    document.getElementById("linuxWorkerUpgradeVersionsV44").innerHTML = `<div><b>当前 Worker Agent：</b>${esc(current.agent_version || "未知")}　→　<b>目标：</b>${esc(target.agent_version || "-")}</div><div><b>当前 Chrome Bridge：</b>${esc(current.chrome_bridge_version || "未知")}　→　<b>目标：</b>${esc(target.chrome_bridge_version || "-")}</div><div><b>中心 Server Runtime：</b>${esc(target.server_runtime || "-")}</div>`;
    const percent = Math.max(0, Math.min(Number(upgrade.percent || 0), 100));
    document.getElementById("linuxWorkerUpgradeProgressV44").value = percent;
    document.getElementById("linuxWorkerUpgradePercentV44").textContent = `${percent}%`;
    document.getElementById("linuxWorkerUpgradeStageV44").textContent = stageLabel(upgrade.stage || upgrade.state || "等待");
    document.getElementById("linuxWorkerUpgradeMessageV44").textContent = upgrade.message || (payload.online ? "等待更新任务" : "Worker 当前离线");
    const history = Array.isArray(upgrade.history) ? upgrade.history.slice(-40) : [];
    document.getElementById("linuxWorkerUpgradeHistoryV44").innerHTML = history.length ? history.map(item => `<div><span style="color:#64748b">${esc(String(item.at || "").replace("T"," ").replace("Z",""))}</span> <b>${esc(stageLabel(item.stage))}</b> ${esc(item.percent ?? 0)}% · ${esc(item.message || "")}</div>`).join("") : `<span style="color:#64748b">暂无进度记录</span>`;
    document.getElementById("linuxWorkerUpgradeBootstrapV44").style.display = upgrade.state === "unsupported" ? "block" : "none";
  };

  const updateButton = (workerId, payload) => {
    const button = document.querySelector(`[data-worker-upgrade-v44="${CSS.escape(workerId)}"]`);
    if (!button) return;
    const upgrade = payload?.upgrade || {};
    if (upgrade.state === "running" || upgrade.state === "queued") {
      button.disabled = true;
      button.textContent = `更新 ${Math.max(0, Math.min(Number(upgrade.percent || 0), 100))}%`;
    } else {
      button.disabled = false;
      button.textContent = upgrade.state === "succeeded" ? "已更新" : "更新";
      if (upgrade.state === "succeeded") setTimeout(() => { if (button.isConnected) button.textContent = "更新"; }, 2500);
    }
  };

  const monitor = workerId => {
    if (watching.has(workerId)) return;
    const tick = async () => {
      try {
        const payload = await api(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/upgrade-status`);
        cache.set(workerId, payload);
        updateButton(workerId, payload);
        if (selectedWorkerId === workerId && dialog.open) render(payload);
        if (!terminal(payload?.upgrade?.state) && ["queued","running"].includes(String(payload?.upgrade?.state || ""))) {
          watching.set(workerId, setTimeout(tick, 1000));
          return;
        }
      } catch (_) {
        watching.set(workerId, setTimeout(tick, 1500));
        return;
      }
      watching.delete(workerId);
    };
    watching.set(workerId, setTimeout(tick, 350));
  };

  const decorate = () => {
    const tbody = document.getElementById("linuxWorkerRows");
    if (!tbody) return;
    for (const row of tbody.querySelectorAll(":scope > tr")) {
      const workerId = workerIdFromRow(row);
      if (!workerId || row.querySelector("[data-worker-upgrade-v44]")) continue;
      const actionCell = row.lastElementChild;
      if (!actionCell || actionCell.tagName !== "TD") continue;
      const button = document.createElement("button");
      button.className = "action good";
      button.type = "button";
      button.textContent = "更新";
      button.dataset.workerUpgradeV44 = workerId;
      button.dataset.workerName = workerNameFromRow(row);
      button.title = "从中心服务器自动更新 Worker Agent、运行脚本和 Chrome Bridge，并实时显示进度；保留 Worker 身份和 ChatGPT Profile";
      const actions = actionCell.querySelector(".lw-actions") || actionCell;
      actions.insertBefore(button, actions.firstChild);
      if (cache.has(workerId)) updateButton(workerId, cache.get(workerId));
    }
  };

  document.addEventListener("click", async event => {
    const button = event.target?.closest?.("[data-worker-upgrade-v44]");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const workerId = String(button.dataset.workerUpgradeV44 || "");
    if (!workerId) return;
    selectedWorkerId = workerId;
    document.getElementById("linuxWorkerUpgradeNameV44").textContent = button.dataset.workerName || workerId;
    document.getElementById("linuxWorkerUpgradeBootstrapV44").style.display = "none";
    dialog.showModal();
    try {
      const before = await api(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/upgrade-status`);
      cache.set(workerId, before); render(before);
      if (!confirm("确定在线更新该 Linux Worker 吗？\n\n更新过程中 Agent 和浏览器会重启，正在执行的请求会中断；Worker 身份、代理配置和 ChatGPT 登录 Profile 会保留。")) return;
      button.disabled = true; button.textContent = "启动更新…";
      const result = await api(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/upgrade`, {method:"POST",body:"{}"});
      cache.set(workerId, result); render(result); updateButton(workerId, result);
      if (result.needs_bootstrap_once) {
        const code = String(result.bootstrap_command || "");
        document.getElementById("linuxWorkerUpgradeBootstrapCodeV44").textContent = code;
        document.getElementById("linuxWorkerUpgradeBootstrapV44").style.display = "block";
        return;
      }
      monitor(workerId);
    } catch (error) {
      button.disabled = false; button.textContent = "更新";
      document.getElementById("linuxWorkerUpgradeMessageV44").textContent = `更新启动失败：${error.message}`;
    }
  }, true);

  document.getElementById("closeLinuxWorkerUpgradeV44").onclick = () => { selectedWorkerId = ""; if (dialog.open) dialog.close(); };
  dialog.addEventListener("cancel", event => { event.preventDefault(); selectedWorkerId = ""; dialog.close(); });
  document.getElementById("copyLinuxWorkerUpgradeBootstrapV44").onclick = async event => {
    const text = document.getElementById("linuxWorkerUpgradeBootstrapCodeV44").textContent || "";
    if (!text) return;
    try { await navigator.clipboard.writeText(text); event.target.textContent = "已复制"; setTimeout(() => event.target.textContent = "复制启用命令", 1200); }
    catch (_) { alert("复制失败，请手动选择命令复制。"); }
  };

  const attach = () => {
    const tbody = document.getElementById("linuxWorkerRows");
    if (!tbody) { setTimeout(attach, 100); return; }
    new MutationObserver(decorate).observe(tbody, {childList:true,subtree:true});
    decorate();
    setInterval(decorate, 1800);
  };
  attach();
})();
