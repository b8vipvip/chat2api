(() => {
  const KEY = "__CHAT2API_LINUX_WORKER_ENABLE_UI_V46__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = { version: 62, master_switch: true, freeze_guard: true };

  const rowsEndpoint = "/api/admin/linux-workers";
  const enabledByWorker = new Map();
  let refreshInFlight = null;
  let rowsObserver = null;

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

  function consumeRows(rows) {
    if (!Array.isArray(rows)) return false;
    for (const row of rows) {
      const id = String(row?.worker_id || "");
      if (id) enabledByWorker.set(id, row.enabled !== false);
    }
    paint();
    return true;
  }

  function paint() {
    for (const button of document.querySelectorAll("button[data-revoke]")) {
      const workerId = String(button.dataset.revoke || "");
      if (!workerId) continue;
      const isEnabled = enabledByWorker.get(workerId) !== false;
      const nextEnabled = isEnabled ? "1" : "0";
      const nextText = isEnabled ? "禁用" : "启用";
      const nextTitle = isEnabled
        ? "禁用此 Worker：先收缩 chat2api 管理的 ChatGPT 窗口到 1 个，再关闭扩展连接与请求路由"
        : "启用此 Worker：重新允许扩展连接；连接成功后按备用窗口配置自动补齐";

      if (button.dataset.workerEnabled !== nextEnabled) button.dataset.workerEnabled = nextEnabled;
      if (button.textContent !== nextText) button.textContent = nextText;
      if (button.title !== nextTitle) button.title = nextTitle;
      button.classList.toggle("danger", isEnabled);
    }
  }

  async function legacyRefresh() {
    if (refreshInFlight) return refreshInFlight;
    refreshInFlight = (async () => {
      const payload = await api(rowsEndpoint);
      consumeRows(payload.data || []);
    })().catch(() => {}).finally(() => { refreshInFlight = null; });
    return refreshInFlight;
  }

  async function refreshFromOwner() {
    const sharedRows = globalThis.__CHAT2API_LINUX_WORKER_ROWS__;
    if (Array.isArray(sharedRows)) consumeRows(sharedRows);

    const sharedRefresh = globalThis.__CHAT2API_LINUX_WORKER_REFRESH__;
    if (typeof sharedRefresh === "function") {
      try { await sharedRefresh(); } catch (_) {}
      const freshRows = globalThis.__CHAT2API_LINUX_WORKER_ROWS__;
      if (Array.isArray(freshRows)) consumeRows(freshRows);
      return;
    }
    await legacyRefresh();
  }

  // Capture phase intentionally intercepts the legacy permanent revoke button
  // before admin_linux_workers.js can issue DELETE. The visual slot is reused as
  // a reversible Worker master enable/disable switch.
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
      ? "确定启用此 Worker？扩展恢复连接后，请求路由与备用 ChatGPT 窗口会按当前配置自动恢复。"
      : "确定禁用此 Worker？系统会先关闭 chat2api 管理的多余 ChatGPT 窗口并只保留 1 个，确认完成后再禁用扩展连接与请求路由。";
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
      refreshFromOwner();
    });
  }, true);

  // Observe only direct tbody row replacements. Button text changes must not
  // retrigger the observer and recreate the historical console freeze loop.
  const workerRows = document.getElementById("linuxWorkerRows");
  if (workerRows) {
    rowsObserver = new MutationObserver(() => paint());
    rowsObserver.observe(workerRows, { childList: true, subtree: false });
  }

  globalThis.addEventListener("chat2api:linux-worker-rows", event => {
    consumeRows(event?.detail?.rows);
  });

  const linuxSection = document.getElementById("view-linux-workers");
  const linuxNav = document.querySelector('button[data-view="linux-workers"]');
  linuxNav?.addEventListener("click", () => setTimeout(refreshFromOwner, 0));
  document.getElementById("refreshLinuxWorkers")?.addEventListener("click", () => setTimeout(refreshFromOwner, 0));

  setInterval(() => {
    if (!linuxSection?.classList.contains("active")) return;
    if (typeof globalThis.__CHAT2API_LINUX_WORKER_REFRESH__ === "function") {
      const sharedRows = globalThis.__CHAT2API_LINUX_WORKER_ROWS__;
      if (Array.isArray(sharedRows)) consumeRows(sharedRows);
      return;
    }
    legacyRefresh();
  }, 5000);

  if (linuxSection?.classList.contains("active")) refreshFromOwner();
})();
