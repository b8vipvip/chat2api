(() => {
  const section = document.getElementById("view-linux-workers");
  const tbody = document.getElementById("linuxWorkerRows");
  if (!section || !tbody || globalThis.__CHAT2API_LINUX_WORKER_CHINESE_PROGRESS_V22_18__) return;
  globalThis.__CHAT2API_LINUX_WORKER_CHINESE_PROGRESS_V22_18__ = true;

  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const stageNames = {
    created:"已生成安装命令",
    "system-check":"系统环境检查",
    cleanup:"清理安装残留",
    packages:"安装基础依赖",
    "worker-bundle":"更新 Worker 组件",
    python:"安装 Python 运行环境",
    xray:"安装 Xray Core",
    enrollment:"注册 Worker 身份",
    systemd:"启动 Worker 服务",
    health:"检查 Worker 健康状态",
    complete:"安装完成",
    failed:"安装失败",
    error:"安装错误",
  };
  const stageName = value => stageNames[String(value || "").toLowerCase()] || (value ? `安装步骤：${value}` : "等待安装");
  const beijingShort = value => {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    const p = new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false})
      .formatToParts(date).reduce((acc, item) => (acc[item.type] = item.value, acc), {});
    return `${p.month}-${p.day} ${p.hour}:${p.minute}`;
  };

  let rows = [];
  let fetchedAt = 0;
  let busy = false;
  const load = async force => {
    if (!force && rows.length && Date.now() - fetchedAt < 650) return rows;
    const response = await fetch("/api/admin/linux-worker-installations", {credentials:"same-origin",cache:"no-store"});
    if (!response.ok) return rows;
    const payload = await response.json();
    rows = Array.isArray(payload.data) ? payload.data : [];
    fetchedAt = Date.now();
    return rows;
  };
  const render = async force => {
    if (busy) return;
    busy = true;
    try {
      await load(Boolean(force));
      Array.from(tbody.querySelectorAll(":scope > tr")).forEach((tr, index) => {
        const row = rows[index];
        const cell = tr.children[2];
        if (!row || !cell) return;
        if (row.record_type !== "installation") {
          if (cell.textContent !== "-") cell.textContent = "-";
          return;
        }
        const history = Array.isArray(row.install_history) ? row.install_history.slice(-8).reverse() : [];
        const currentStage = stageName(row.install_stage);
        const currentMessage = String(row.install_message || "").trim();
        const summary = `${currentStage}${currentMessage ? ` · ${currentMessage}` : ""}`;
        if (!history.length) {
          cell.innerHTML = `<span class="lw-muted">${esc(summary)}</span>`;
          return;
        }
        cell.innerHTML = `<details><summary>${esc(summary)}</summary><div style="min-width:280px;max-width:520px;white-space:normal;line-height:1.55">${history.map(item => {
          const at = beijingShort(item.at);
          const stage = stageName(item.stage);
          const message = String(item.message || "").trim();
          return `<div>${at ? `<code>${esc(at)}</code> ` : ""}${esc(stage)}${message ? ` · ${esc(message)}` : ""}</div>`;
        }).join("")}</div></details>`;
      });
    } catch (_) {
    } finally {
      busy = false;
    }
  };

  new MutationObserver(() => setTimeout(() => render(false), 0)).observe(tbody, {childList:true,subtree:false});
  setInterval(() => { if (section.classList.contains("active")) render(false); }, 1200);
  render(true);
})();
