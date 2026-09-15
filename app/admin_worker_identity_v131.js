(() => {
  const KEY = "__CHAT2API_ADMIN_WORKER_IDENTITY_V131__";
  if (globalThis[KEY]) return;

  const state = { version: 143, observer: null };
  globalThis[KEY] = state;

  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function ensureWindowHeader(table) {
    const row = table?.querySelector("thead tr");
    if (!row || row.querySelector('[data-v131-worker-id-header]')) return;
    const th = document.createElement("th");
    th.dataset.v131WorkerIdHeader = "1";
    th.textContent = "Worker ID";
    th.title = "对应 Worker 管理列表中的 Worker ID（Extension client ID）";
    const device = row.children[1] || null;
    if (device?.nextSibling) row.insertBefore(th, device.nextSibling);
    else row.appendChild(th);
  }

  function decorateWindowBody(body) {
    const table = body?.closest("table");
    if (!body || !table) return;
    ensureWindowHeader(table);
    for (const tr of body.rows) {
      if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) {
        if (tr.cells[0].colSpan !== 8) tr.cells[0].colSpan = 8;
        continue;
      }
      if (tr.querySelector('[data-v131-worker-id-cell]')) continue;
      const clientId = String(tr.dataset?.client || "").trim();
      const td = document.createElement("td");
      td.dataset.v131WorkerIdCell = "1";
      td.innerHTML = `<code>${esc(clientId || "-")}</code>`;
      const deviceCell = tr.cells[1] || null;
      if (deviceCell?.nextSibling) tr.insertBefore(td, deviceCell.nextSibling);
      else tr.appendChild(td);
    }
  }

  function decorateWindowManager() {
    decorateWindowBody(document.getElementById("wmActiveBody"));
    decorateWindowBody(document.getElementById("wmClosedBody"));
  }

  function linuxState() {
    const value = globalThis.__CHAT2API_LINUX_DEVICE_AUTHORITY_V124__;
    return value && Array.isArray(value.devices) ? value : null;
  }

  function selectedWorkers() {
    const value = linuxState();
    if (!value) return [];
    const device = value.devices.find(row => String(row?.device_id || "") === String(value.selectedDevice || ""));
    return Array.isArray(device?.workers) ? device.workers : [];
  }

  function decorateLinuxWorkerManager() {
    const dialog = document.getElementById("linuxWorkerManagerV124");
    if (dialog) {
      dialog.style.width = "min(1280px, calc(100vw - 28px))";
      dialog.style.maxWidth = "none";
    }
    const refresh = document.getElementById("refreshManagerV124");
    const addWorker = document.getElementById("openAddWorkerV124");
    for (const button of [refresh, addWorker]) {
      if (!button) continue;
      button.style.whiteSpace = "nowrap";
      button.style.minWidth = button === addWorker ? "108px" : "82px";
      button.style.flex = "0 0 auto";
    }
    const actionWrap = refresh?.parentElement;
    if (actionWrap && actionWrap === addWorker?.parentElement) actionWrap.style.flexWrap = "nowrap";
    const box = document.getElementById("managerWorkersV124");
    if (box) box.style.overflowX = "auto";
    const table = box?.querySelector("table");
    const header = table?.querySelector("thead tr");
    const body = table?.querySelector("tbody");
    if (!header || !body) return;
    if (header.cells[0]) {
      if (header.cells[0].textContent !== "Worker ID") header.cells[0].textContent = "Worker ID";
      const title = "对应 Worker 管理列表中的 Worker ID（Extension client ID）";
      if (header.cells[0].title !== title) header.cells[0].title = title;
    }
    const workers = selectedWorkers();
    [...body.rows].forEach((tr, index) => {
      const worker = workers[index];
      if (!worker || !tr.cells[0]) return;
      const extensionId = String(worker.extension_client_id || "").trim();
      const primary = extensionId || "Extension 待连接";
      const html = `<b>${esc(primary)}</b>`;
      if (tr.cells[0].innerHTML !== html) tr.cells[0].innerHTML = html;
    });
  }

  function decoratePersistentWindowCopy() {
    const header = document.querySelector('#extensionDeviceBody')?.closest('table')?.querySelector('thead [data-chat2api-column-key="worker_settings"]');
    if (header) header.title = "并发=同时执行的请求上限；窗口=该 Worker 登录后持续维持的 ChatGPT 物理窗口总数。点击编辑按钮修改。";
  }

  function decorateAccountTierLabels() {
    const body = document.getElementById("extensionDeviceBody");
    if (!body) return;
    for (const row of body.rows) {
      const cell = row.querySelector('[data-chat2api-column-key="account_type"]');
      const pill = cell?.querySelector(".pill");
      if (!pill) continue;
      const value = String(pill.textContent || "").trim().toLowerCase();
      if (value === "付费" || value === "paid") pill.textContent = "Plus";
      else if (value === "pro") pill.textContent = "Pro";
      else if (value === "free") pill.textContent = "Free";
    }
  }

  function decorate() {
    decorateWindowManager();
    decorateLinuxWorkerManager();
    decoratePersistentWindowCopy();
    decorateAccountTierLabels();
  }

  function start() {
    decorate();
    const root = document.body;
    if (!root) return;
    const observer = new MutationObserver(() => decorate());
    observer.observe(root, { childList: true, subtree: true });
    state.observer = observer;
    document.documentElement.dataset.chat2apiWorkerIdentityRevision = "143";
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
