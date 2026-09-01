(() => {
  const KEY = "__CHAT2API_WORKER_DISABLE_AUTHORITY_UI_V62__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = { version: 62 };

  function paintWorkerManagement() {
    const body = document.getElementById("extensionDeviceBody");
    if (!body) return;
    for (const row of body.querySelectorAll(":scope > tr")) {
      const enable = row.querySelector('button[data-worker-list-action="enable"]');
      const disable = row.querySelector('button[data-worker-list-action="disconnect"]');
      const action = enable || disable;
      if (!action) continue;
      const isEnabled = Boolean(disable);
      const wantedText = isEnabled ? "禁用" : "启用";
      if (action.textContent !== wantedText) action.textContent = wantedText;
      action.title = isEnabled
        ? "禁用此 Worker；在线时先关闭受管的多余 ChatGPT 窗口并只保留 1 个"
        : "启用此 Worker；扩展重新连接后恢复请求路由和备用窗口";
      const status = row.querySelector('[data-chat2api-column-key="status"]');
      if (status) {
        const html = isEnabled
          ? '<span class="pill ok">已启用</span>'
          : '<span class="pill">已禁用</span>';
        if (status.innerHTML !== html) status.innerHTML = html;
      }
    }
  }

  function paintLinuxWorkers() {
    const body = document.getElementById("linuxWorkerRows");
    if (!body) return;
    for (const row of body.querySelectorAll(":scope > tr")) {
      if (row.children.length < 2) continue;
      const button = row.querySelector("button[data-revoke]");
      if (!button) continue;
      const isEnabled = button.dataset.workerEnabled !== "0";
      const status = row.cells[1];
      if (!status) continue;
      const html = isEnabled
        ? '<span class="lw-pill good">已启用</span>'
        : '<span class="lw-pill bad">已禁用</span>';
      if (status.innerHTML !== html) status.innerHTML = html;
    }
  }

  function paint() {
    paintWorkerManagement();
    paintLinuxWorkers();
  }

  const observer = new MutationObserver(() => queueMicrotask(paint));
  observer.observe(document.documentElement, { childList: true, subtree: true });
  globalThis.addEventListener("chat2api:linux-worker-rows", () => queueMicrotask(paint));
  document.addEventListener("click", event => {
    if (event.target?.closest?.('[data-worker-list-action],[data-revoke]')) {
      setTimeout(paint, 0);
      setTimeout(paint, 250);
    }
  }, true);
  setInterval(paint, 1000);
  paint();
})();
