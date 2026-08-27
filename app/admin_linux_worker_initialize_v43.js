(() => {
  if (globalThis.__CHAT2API_LINUX_WORKER_INITIALIZE_V43__) return;
  globalThis.__CHAT2API_LINUX_WORKER_INITIALIZE_V43__ = true;

  const workerIdFromRow = row => {
    if (!row) return "";
    const diagnostics = row.querySelector("[data-worker-diagnostics-v2222]");
    if (diagnostics?.dataset?.workerDiagnosticsV2222) return String(diagnostics.dataset.workerDiagnosticsV2222);
    for (const node of row.querySelectorAll("button,a")) {
      for (const [key, value] of Object.entries(node.dataset || {})) {
        if (/worker/i.test(key) && /^wrk_/.test(String(value || ""))) return String(value);
      }
    }
    return "";
  };

  const request = async workerId => {
    const response = await fetch(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/initialize`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {"Content-Type": "application/json"},
      body: "{}",
    });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = {detail: text}; }
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  };

  const decorate = () => {
    const tbody = document.getElementById("linuxWorkerRows");
    if (!tbody) return;
    for (const row of tbody.querySelectorAll(":scope > tr")) {
      const workerId = workerIdFromRow(row);
      if (!workerId || row.querySelector("[data-worker-initialize-v43]")) continue;
      const actionCell = row.lastElementChild;
      if (!actionCell || actionCell.tagName !== "TD") continue;
      const button = document.createElement("button");
      button.className = "action";
      button.type = "button";
      button.textContent = "初始化";
      button.dataset.workerInitializeV43 = workerId;
      button.title = "重启 Worker Agent、Xray/Xvfb 与 Chrome，重置 Bridge Service Worker 运行态，并保留一个 ChatGPT 初始化窗口";
      const actions = actionCell.querySelector(".lw-actions") || actionCell;
      actions.insertBefore(button, actions.firstChild);
    }
  };

  document.addEventListener("click", async event => {
    const button = event.target?.closest?.("[data-worker-initialize-v43]");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const workerId = String(button.dataset.workerInitializeV43 || "");
    if (!workerId) return;
    if (!confirm("确定初始化该 Linux Worker 吗？\n\n将重启 Worker 服务和浏览器，当前正在执行的请求会中断；ChatGPT 登录资料会保留。")) return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "初始化中…";
    try {
      const result = await request(workerId);
      button.textContent = "已启动";
      if (result.mode === "compatibility" || result.needs_worker_upgrade) {
        alert(result.message || "当前 Worker Agent 需要先进行一次修复升级，已完成旧版兼容恢复。");
      }
      setTimeout(() => {
        if (button.isConnected) {
          button.disabled = false;
          button.textContent = original;
        }
        const refresh = document.getElementById("refreshLinuxWorkers") || document.querySelector("#view-linux-workers button[data-linux-worker-refresh]");
        refresh?.click?.();
      }, 5000);
    } catch (error) {
      button.disabled = false;
      button.textContent = original;
      alert(`Worker 初始化失败：${error.message}`);
    }
  }, true);

  const attach = () => {
    const tbody = document.getElementById("linuxWorkerRows");
    if (!tbody) {
      setTimeout(attach, 100);
      return;
    }
    new MutationObserver(decorate).observe(tbody, {childList: true, subtree: true});
    decorate();
    setInterval(decorate, 2000);
  };

  attach();
})();
