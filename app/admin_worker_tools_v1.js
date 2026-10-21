/* Worker tools P0 admin console. Uses the existing administrator API key. */
(() => {
  "use strict";
  const root = document.getElementById("view-worker-tools");
  if (!root) return;
  const $ = id => document.getElementById(id);
  let catalog = [];
  let chosenPolicy = null;
  let workers = [];

  function status(value, bad = false) {
    const el = $("workerToolsStatus");
    if (el) { el.textContent = value; el.style.color = bad ? "#ef9d9d" : "#9cb5d4"; }
  }

  async function apiRequest(path, options = {}) {
    const token = (typeof key === "function" ? key() : sessionStorage.getItem("chat2api_admin_key")) || "";
    if (!token) throw new Error("请先输入管理员主密钥并连接");
    const response = await fetch(path, {
      method: options.method || "GET", cache: "no-store",
      headers: {
        Authorization: "Bearer " + token,
        ...(options.body !== undefined ? {"Content-Type": "application/json"} : {}),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    if (!response.ok) {
      let message = "HTTP " + response.status;
      try { message = (await response.json()).detail || message; } catch (_) {}
      throw new Error(String(message));
    }
    return response;
  }
  async function json(path, options) { return (await apiRequest(path, options)).json(); }
  const elem = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };

  function renderCatalog() {
    const target = $("workerToolsCatalog");
    target.replaceChildren();
    for (const tool of catalog) {
      const row = elem("tr");
      row.append(elem("td", tool.name), elem("td", tool.id),
        elem("td", tool.version), elem("td", tool.phase),
        elem("td", tool.available ? "可配置" : "尚未实现"));
      target.append(row);
    }
  }

  function renderPolicy() {
    const target = $("workerToolsPolicyRows");
    target.replaceChildren();
    const scope = $("workerToolsScope").value;
    const configured = chosenPolicy?.tools || {};
    for (const tool of catalog) {
      const row = elem("tr");
      row.append(elem("td", tool.name), elem("td", tool.phase));
      const control = elem("td");
      const select = elem("select");
      select.dataset.probe = tool.id;
      select.disabled = !tool.available;
      const values = scope === "global"
        ? [["", "未配置（默认关闭）"], ["on", "开启"], ["off", "关闭"]]
        : [["", "继承全局"], ["on", "显式开启"], ["off", "显式关闭"]];
      for (const [value, text] of values) {
        const item = elem("option", text); item.value = value; select.append(item);
      }
      const override = configured[tool.id];
      select.value = override?.enabled === true ? "on"
        : override?.enabled === false ? "off" : "";
      control.append(select);
      row.append(control);
      target.append(row);
    }
    $("workerToolsRevision").textContent = "revision " + (chosenPolicy?.revision ?? 0);
  }

  async function loadPolicy() {
    const scope = $("workerToolsScope").value;
    try {
      const result = await json("/api/admin/worker-tools/policies?scope_id=" + encodeURIComponent(scope));
      chosenPolicy = result.policy;
      workers = result.workers || [];
      renderPolicy();
      status("已加载配置 · " + scope);
    } catch (error) { status(error.message, true); }
  }

  async function refreshRuns() {
    const target = $("workerToolsRuns");
    target.replaceChildren();
    try {
      const result = await json("/api/admin/worker-tools/runs?limit=30");
      for (const run of result.data || []) {
        const row = elem("tr");
        row.append(elem("td", run.request_id),
          elem("td", run.capture_status),
          elem("td", run.event_count),
          elem("td", run.last_stage || "-"));
        const actions = elem("td");
        const inspect = elem("button", "查看证据", "action");
        inspect.onclick = () => inspectRequest(run.request_id);
        const download = elem("button", "下载 ZIP", "action");
        download.onclick = () => downloadRequest(run.request_id);
        actions.append(inspect, download); row.append(actions); target.append(row);
      }
      if (!(result.data || []).length)
        target.append(elem("tr", "尚无探针运行记录；开启工具后需要发起新请求"));
    } catch (error) { status(error.message, true); }
  }

  async function inspectRequest(requestId) {
    try {
      const id = requestId || $("workerToolsRequestId").value.trim();
      if (!id) throw new Error("请输入请求 ID");
      const result = await json("/api/admin/requests/" + encodeURIComponent(id) + "/probes");
      $("workerToolsEvidence").textContent = JSON.stringify(result, null, 2);
      status("已读取请求 " + id + " 的探针时间线");
    } catch (error) { status(error.message, true); }
  }

  async function downloadRequest(requestId) {
    try {
      const id = requestId || $("workerToolsRequestId").value.trim();
      if (!id) throw new Error("请输入请求 ID");
      const response = await apiRequest("/api/admin/requests/" +
        encodeURIComponent(id) + "/probes/download");
      const url = URL.createObjectURL(await response.blob());
      const link = elem("a"); link.href = url;
      link.download = "request-probes-" + id + ".zip";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      status("诊断包已准备下载");
    } catch (error) { status(error.message, true); }
  }

  async function save() {
    const scope = $("workerToolsScope").value;
    const tools = {};
    for (const select of document.querySelectorAll("#workerToolsPolicyRows select[data-probe]")) {
      if (select.value === "on" || select.value === "off")
        tools[select.dataset.probe] = {enabled: select.value === "on"};
    }
    try {
      const revision = chosenPolicy?.revision;
      if (!Number.isInteger(revision)) throw new Error("请先读取当前策略");
      await json("/api/admin/worker-tools/policies/" + encodeURIComponent(scope), {
        method: "PUT", body: {expected_revision: revision, tools},
      });
      await loadPolicy();
      status("策略已保存。下次请求由服务端附带有效策略；已在执行的请求不改写");
    } catch (error) { status(error.message, true); }
  }

  async function load() {
    if (!$("workerToolsCatalog")) return;
    try {
      const [c, p] = await Promise.all([
        json("/api/admin/worker-tools/catalog"),
        json("/api/admin/worker-tools/policies?scope_id=global"),
      ]);
      catalog = c.tools || [];
      workers = p.workers || [];
      renderCatalog();
      const picker = $("workerToolsScope");
      const old = picker.value || "global";
      picker.replaceChildren();
      const global = elem("option", "全局默认"); global.value = "global"; picker.append(global);
      const unique = new Map();
      for (const worker of workers) {
        if (!unique.has(worker.worker_id)) unique.set(worker.worker_id, worker);
      }
      for (const worker of unique.values()) {
        const option = elem("option", (worker.name || worker.worker_id) +
          " · " + worker.worker_id + (worker.online ? " · 在线" : " · 离线"));
        option.value = "worker:" + worker.worker_id;
        picker.append(option);
      }
      if ([...picker.options].some(option => option.value === old)) picker.value = old;
      await loadPolicy();
      await refreshRuns();
    } catch (error) { status(error.message, true); }
  }

  $("workerToolsScope").addEventListener("change", loadPolicy);
  $("workerToolsSave").addEventListener("click", save);
  $("workerToolsReload").addEventListener("click", load);
  $("workerToolsRefreshRuns").addEventListener("click", refreshRuns);
  $("workerToolsInspect").addEventListener("click", () => inspectRequest());
  $("workerToolsDownload").addEventListener("click", () => downloadRequest());
  window.loadWorkerTools = load;
  const navigation = document.querySelector('.nav button[data-view="worker-tools"]');
  navigation?.addEventListener("click", () => { load().catch(error => status(error.message, true)); });
  if (root.classList.contains("active") || location.hash === "#worker-tools")
    load().catch(error => status(error.message, true));
})();
