(() => {
  const KEY = "__CHAT2API_LINUX_WORKER_ENABLE_UI_V46__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = { version: 46 };

  const rowsEndpoint = "/api/admin/linux-workers";
  const enabledByWorker = new Map();
  let refreshInFlight = null;

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
    return payload;
  }

  function paint() {
    for (const button of document.querySelectorAll("button[data-revoke]")) {
      const workerId = String(button.dataset.revoke || "");
      if (!workerId) continue;
      const isEnabled = enabledByWorker.get(workerId) !== false;
      button.dataset.workerEnabled = isEnabled ? "1" : "0";
      button.textContent = isEnabled ? "禁用" : "启用";
      button.title = isEnabled
        ? "停止把请求路由到此 Worker；Agent/Bridge 保持运行"
        : "重新允许请求路由到此 Worker；不需要重启 Agent/Bridge";
      button.classList.toggle("danger", isEnabled);
    }
  }

  async function refresh() {
    if (refreshInFlight) return refreshInFlight;
    refreshInFlight = (async () => {
      const payload = await api(rowsEndpoint);
      for (const row of payload.data || []) {
        const id = String(row.worker_id || "");
        if (id) enabledByWorker.set(id, row.enabled !== false);
      }
      paint();
    })().catch(() => {}).finally(() => { refreshInFlight = null; });
    return refreshInFlight;
  }

  // Capture phase intentionally intercepts the legacy permanent revoke button
  // before admin_linux_workers.js can issue DELETE.  The visual slot is reused,
  // but the operation is now a reversible routing-only toggle.
  document.addEventListener("click", event => {
    const button = event.target?.closest?.("button[data-revoke]");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const workerId = String(button.dataset.revoke || "");
    if (!workerId || button.disabled) return;
    const current = enabledByWorker.get(workerId) !== false;
    const next = !current;
    const question = next
      ? "确定启用此 Worker？启用后新的 API 请求可以再次路由到它。"
      : "确定禁用此 Worker？只停止请求路由，Worker Agent、Chrome 与扩展连接不会被关闭。";
    if (!confirm(question)) return;
    button.disabled = true;
    api(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled: next }),
    }).then(result => {
      enabledByWorker.set(workerId, result.enabled !== false);
      paint();
    }).catch(error => alert(error.message)).finally(() => {
      button.disabled = false;
      refresh();
    });
  }, true);

  const observer = new MutationObserver(() => paint());
  observer.observe(document.documentElement, { childList: true, subtree: true });
  setInterval(refresh, 1000);
  refresh();
})();
