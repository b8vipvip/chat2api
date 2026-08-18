(() => {
  const VERSION = "0.21.5";
  const POLL_MS = 1000;
  let pollInFlight = false;

  function extensionViewActive() {
    return document.getElementById("view-extensions")?.classList.contains("active");
  }

  function patchHeader() {
    const table = document.querySelector("#view-extensions #extensionDeviceBody")?.closest("table");
    const headers = table ? [...table.querySelectorAll("thead th")] : [];
    const target = headers.find(node => node.textContent.trim() === "绑定 API Key 数" || node.textContent.trim() === "API 调用数（实时并发）");
    if (target) {
      target.textContent = "API 调用数（实时并发）";
      target.title = "当前正在该扩展上执行的 API 请求数 / 配置的最大并发数";
    }
  }

  async function refreshLiveConcurrency() {
    if (!extensionViewActive() || pollInFlight) return;
    pollInFlight = true;
    try {
      patchHeader();
      const data = await api("/api/admin/extensions");
      const byClient = new Map((data.clients || []).map(row => [String(row.client_id || ""), row]));
      const rows = document.querySelectorAll("#extensionDeviceBody tr");
      for (const tr of rows) {
        if (!tr.cells || tr.cells.length < 5) continue;
        const clientId = tr.cells[0]?.textContent?.trim() || "";
        const item = byClient.get(clientId);
        if (!item) continue;
        const active = Number.isFinite(Number(item.active_api_calls))
          ? Number(item.active_api_calls)
          : Number(item.capacity?.active_requests || 0);
        const limit = Number(item.max_concurrency || item.capacity?.limit_units || 0);
        tr.cells[4].textContent = limit > 0 ? `${active} / ${limit}` : String(active);
        tr.cells[4].title = "实时活动 API 请求 / 最大并发";
      }
    } catch (_) {
      // The historical extension-management loader owns visible error reporting.
      // Poll failures stay silent so a transient refresh cannot overwrite it.
    } finally {
      pollInFlight = false;
    }
  }

  const baseShow = typeof show === "function" ? show : null;
  if (baseShow) {
    show = async viewName => {
      await baseShow(viewName);
      if (viewName === "extensions") await refreshLiveConcurrency();
    };
  }

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;
  patchHeader();
  refreshLiveConcurrency();
  setInterval(() => {
    if (extensionViewActive()) refreshLiveConcurrency();
  }, POLL_MS);
})();
