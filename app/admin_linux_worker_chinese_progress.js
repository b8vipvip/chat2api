(() => {
  const section = document.getElementById("view-linux-workers");
  const tbody = document.getElementById("linuxWorkerRows");
  if (!section || !tbody || globalThis.__CHAT2API_LINUX_WORKER_PROXY_HEALTH_V55__) return;
  globalThis.__CHAT2API_LINUX_WORKER_PROXY_HEALTH_V55__ = true;

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

  const healthByWorker = new Map();
  const healthInflight = new Map();
  const observedProxyCells = new WeakSet();
  const HEALTH_TTL_MS = 60000;
  const HEALTH_RETRY_MS = 20000;

  const configuredProxy = row => {
    const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    const summary = meta.proxy_summary && typeof meta.proxy_summary === "object" ? meta.proxy_summary : {};
    return Boolean(String(summary.protocol || "").trim());
  };
  const proxyName = row => {
    const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    const summary = meta.proxy_summary && typeof meta.proxy_summary === "object" ? meta.proxy_summary : {};
    return String(summary.name || summary.server || summary.protocol || "").trim();
  };
  const parseProbe = (result, name) => {
    const probes = Array.isArray(result?.probes) ? result.probes : [];
    return probes.find(item => String(item?.name || "") === name) || null;
  };
  const parseHealth = result => {
    const network = parseProbe(result, "network_access");
    const chatgpt = ["chatgpt_home", "conversation_route", "sentinel_route"].map(name => parseProbe(result, name));
    const chatgptKnown = chatgpt.every(Boolean);
    const chatgptReady = chatgptKnown ? chatgpt.every(item => item?.ok === true) : Boolean(result?.generation_backend_ready ?? result?.ok);
    const networkReady = network ? network.ok === true : null;
    const rawLatency = Number(network?.total_s || parseProbe(result, "chatgpt_home")?.total_s || 0);
    return {
      checkedAt: Date.now(),
      networkReady,
      chatgptReady,
      latencyMs: Number.isFinite(rawLatency) && rawLatency > 0 ? Math.max(1, Math.round(rawLatency * 1000)) : 0,
      error: String(result?.error || ""),
    };
  };
  const requestProxyHealth = async row => {
    const workerId = String(row?.worker_id || "");
    if (!workerId || !configuredProxy(row) || healthInflight.has(workerId)) return;
    const previous = healthByWorker.get(workerId);
    const age = previous ? Date.now() - Number(previous.checkedAt || 0) : Infinity;
    const ttl = previous?.error ? HEALTH_RETRY_MS : HEALTH_TTL_MS;
    if (age < ttl) return;
    const task = (async () => {
      try {
        const response = await fetch(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/commands`, {
          method:"POST",
          credentials:"same-origin",
          cache:"no-store",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({command:"test_proxy",arguments:{},wait:true,timeout_seconds:35}),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
        healthByWorker.set(workerId, parseHealth(payload.result || {}));
      } catch (error) {
        healthByWorker.set(workerId, {checkedAt:Date.now(),networkReady:false,chatgptReady:false,latencyMs:0,error:String(error?.message || error)});
      } finally {
        healthInflight.delete(workerId);
        setTimeout(() => render(false), 0);
      }
    })();
    healthInflight.set(workerId, task);
  };
  const pill = (text, tone) => `<span class="lw-pill ${tone}">${esc(text)}</span>`;
  const proxyHealthHtml = row => {
    if (!configuredProxy(row)) return `<span class="lw-muted">未配置</span>`;
    const workerId = String(row?.worker_id || "");
    const health = healthByWorker.get(workerId);
    const name = proxyName(row);
    if (!health) {
      return `<div style="display:flex;flex-wrap:wrap;gap:5px">${pill("已配置","good")}${pill("网络检测中","warn")}${pill("GPT检测中","warn")}${pill("延迟 --","warn")}</div>${name ? `<div class="lw-muted" style="margin-top:4px">${esc(name)}</div>` : ""}`;
    }
    const networkText = health.networkReady === true ? "网络正常" : health.networkReady === false ? "网络异常" : "网络检测中";
    const networkTone = health.networkReady === true ? "good" : health.networkReady === false ? "bad" : "warn";
    const gptText = health.chatgptReady === true ? "GPT正常" : health.chatgptReady === false ? "GPT异常" : "GPT检测中";
    const gptTone = health.chatgptReady === true ? "good" : health.chatgptReady === false ? "bad" : "warn";
    const latencyText = health.latencyMs > 0 ? `延迟 ${health.latencyMs} ms` : "延迟 --";
    const latencyTone = health.latencyMs > 0 ? "good" : "warn";
    const title = health.error ? ` title="${esc(health.error)}"` : "";
    return `<div style="display:flex;flex-wrap:wrap;gap:5px"${title}>${pill("已配置","good")}${pill(networkText,networkTone)}${pill(gptText,gptTone)}${pill(latencyText,latencyTone)}</div>${name ? `<div class="lw-muted" style="margin-top:4px">${esc(name)}</div>` : ""}`;
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
  const observeProxyCell = cell => {
    if (!cell || observedProxyCells.has(cell)) return;
    observedProxyCells.add(cell);
    new MutationObserver(() => setTimeout(() => render(false), 0)).observe(cell, {childList:true,subtree:true,characterData:true});
  };
  const render = async force => {
    if (busy) return;
    busy = true;
    try {
      await load(Boolean(force));
      Array.from(tbody.querySelectorAll(":scope > tr")).forEach((tr, index) => {
        const row = rows[index];
        if (!row) return;

        const progressCell = tr.children[2];
        if (progressCell) {
          if (row.record_type !== "installation") {
            if (progressCell.textContent !== "-") progressCell.textContent = "-";
          } else {
            const history = Array.isArray(row.install_history) ? row.install_history.slice(-8).reverse() : [];
            const currentStage = stageName(row.install_stage);
            const currentMessage = String(row.install_message || "").trim();
            const summary = `${currentStage}${currentMessage ? ` · ${currentMessage}` : ""}`;
            if (!history.length) {
              const html = `<span class="lw-muted">${esc(summary)}</span>`;
              if (progressCell.innerHTML !== html) progressCell.innerHTML = html;
            } else {
              const html = `<details><summary>${esc(summary)}</summary><div style="min-width:280px;max-width:520px;white-space:normal;line-height:1.55">${history.map(item => {
                const at = beijingShort(item.at);
                const stage = stageName(item.stage);
                const message = String(item.message || "").trim();
                return `<div>${at ? `<code>${esc(at)}</code> ` : ""}${esc(stage)}${message ? ` · ${esc(message)}` : ""}</div>`;
              }).join("")}</div></details>`;
              if (progressCell.innerHTML !== html) progressCell.innerHTML = html;
            }
          }
        }

        const proxyCell = tr.children[6];
        if (proxyCell && row.record_type !== "installation") {
          observeProxyCell(proxyCell);
          const html = proxyHealthHtml(row);
          proxyCell.dataset.chat2apiProxyHealthOwner = "v55";
          if (proxyCell.innerHTML !== html) proxyCell.innerHTML = html;
          requestProxyHealth(row);
        }
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