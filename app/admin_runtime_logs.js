(() => {
  const KEY = "__CHAT2API_ADMIN_RUNTIME_LOGS_V37__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = { installedAt: new Date().toISOString() };

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);

  function ensureStyles() {
    if (document.getElementById("chat2apiRuntimeLogsStyles")) return;
    const style = document.createElement("style");
    style.id = "chat2apiRuntimeLogsStyles";
    style.textContent = `
      #view-runtime-logs .runtimeLogToolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
      #view-runtime-logs .runtimeLogToolbar>*{min-width:120px}
      #view-runtime-logs .runtimeLogTable{width:100%;table-layout:fixed;white-space:normal}
      #view-runtime-logs .runtimeLogTable th:nth-child(1){width:190px}
      #view-runtime-logs .runtimeLogTable th:nth-child(2){width:90px}
      #view-runtime-logs .runtimeLogTable th:nth-child(3){width:220px}
      #view-runtime-logs .runtimeLogMessage{white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
      #view-runtime-logs details pre{white-space:pre-wrap;word-break:break-word;margin:8px 0 0;font-size:12px;color:#ffb4b9}
      #view-runtime-logs .runtimeLogMeta{color:var(--muted);font-size:12px;margin-bottom:10px}
      #view-runtime-logs .runtimeLogLevel.ERROR,#view-runtime-logs .runtimeLogLevel.CRITICAL{color:var(--bad)}
      #view-runtime-logs .runtimeLogLevel.WARNING{color:var(--warn)}
      #view-runtime-logs .runtimeLogLevel.INFO{color:var(--ok)}
    `;
    document.head.appendChild(style);
  }

  function ensureNavigation() {
    const nav = document.querySelector(".nav");
    if (!nav || nav.querySelector('[data-view="runtime-logs"]')) return;
    const button = document.createElement("button");
    button.dataset.view = "runtime-logs";
    button.textContent = "运行日志";
    nav.appendChild(button);
    button.addEventListener("click", event => {
      event.preventDefault();
      showRuntimeLogs();
    });
  }

  function ensureView() {
    if (document.getElementById("view-runtime-logs")) return;
    const content = document.querySelector(".content");
    if (!content) return;
    const section = document.createElement("section");
    section.className = "view";
    section.id = "view-runtime-logs";
    section.innerHTML = `
      <div class="panel">
        <h2>chat2api 运行日志</h2>
        <div class="muted" style="margin-bottom:12px">记录服务端运行、扩展控制、异常堆栈和关键状态变化。敏感 Token、密码、配对码和 API Key 会在写入前自动脱敏。</div>
        <div class="runtimeLogToolbar">
          <select id="runtimeLogLevel">
            <option value="">全部级别</option>
            <option value="ERROR">ERROR</option>
            <option value="WARNING">WARNING</option>
            <option value="INFO">INFO</option>
            <option value="DEBUG">DEBUG</option>
          </select>
          <input id="runtimeLogLogger" placeholder="Logger，例如 chat2api.capacity">
          <input id="runtimeLogSearch" placeholder="搜索错误 / client / control">
          <select id="runtimeLogLimit">
            <option value="200">最近 200 条</option>
            <option value="500" selected>最近 500 条</option>
            <option value="1000">最近 1000 条</option>
            <option value="2000">最近 2000 条</option>
          </select>
          <button class="action" id="runtimeLogRefresh">刷新</button>
          <button class="action good" id="runtimeLogExport">导出日志</button>
        </div>
        <div id="runtimeLogMeta" class="runtimeLogMeta">尚未读取。</div>
        <div class="scroll" style="max-height:680px">
          <table class="runtimeLogTable">
            <thead><tr><th>时间</th><th>级别</th><th>Logger</th><th>消息 / 异常</th></tr></thead>
            <tbody id="runtimeLogBody"><tr><td colspan="4" class="muted">点击刷新读取日志。</td></tr></tbody>
          </table>
        </div>
      </div>`;
    content.appendChild(section);
    section.querySelector("#runtimeLogRefresh").addEventListener("click", () => loadLogs().catch(showError));
    section.querySelector("#runtimeLogExport").addEventListener("click", () => exportLogs().catch(showError));
    for (const id of ["runtimeLogLevel", "runtimeLogLogger", "runtimeLogSearch", "runtimeLogLimit"]) {
      section.querySelector(`#${id}`)?.addEventListener("change", () => loadLogs().catch(showError));
    }
    section.querySelector("#runtimeLogSearch")?.addEventListener("keydown", event => {
      if (event.key === "Enter") loadLogs().catch(showError);
    });
  }

  function params(exportMode = false) {
    const section = document.getElementById("view-runtime-logs");
    const query = new URLSearchParams();
    const limit = section?.querySelector("#runtimeLogLimit")?.value || (exportMode ? "5000" : "500");
    query.set("limit", exportMode ? String(Math.max(5000, Number(limit) || 5000)) : limit);
    const level = section?.querySelector("#runtimeLogLevel")?.value || "";
    const logger = section?.querySelector("#runtimeLogLogger")?.value?.trim() || "";
    const q = section?.querySelector("#runtimeLogSearch")?.value?.trim() || "";
    if (level) query.set("level", level);
    if (logger) query.set("logger", logger);
    if (q) query.set("q", q);
    return query;
  }

  async function request(path) {
    const response = await fetch(path, { cache: "no-store", credentials: "same-origin" });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; }
    catch (_) { payload = { detail: text }; }
    if (!response.ok) throw new Error(payload.detail || `${response.status} ${response.statusText}`);
    return payload;
  }

  function showError(error) {
    const text = String(error?.message || error || "未知错误");
    const meta = document.getElementById("runtimeLogMeta");
    if (meta) { meta.className = "runtimeLogMeta bad"; meta.textContent = `日志读取失败：${text}`; }
    if (typeof globalThis.status === "function") globalThis.status(`运行日志失败：${text}`, "bad");
  }

  function renderRows(rows) {
    const body = document.getElementById("runtimeLogBody");
    if (!body) return;
    if (!Array.isArray(rows) || !rows.length) {
      body.innerHTML = '<tr><td colspan="4" class="muted">当前筛选条件下没有日志。</td></tr>';
      return;
    }
    body.innerHTML = rows.map(row => {
      const level = String(row?.level || "INFO").toUpperCase();
      const context = row?.context && typeof row.context === "object" && Object.keys(row.context).length
        ? `<div class="muted">context=${esc(JSON.stringify(row.context))}</div>` : "";
      const exception = String(row?.exception || "");
      const exceptionHtml = exception
        ? `<details open><summary class="bad">异常堆栈</summary><pre>${esc(exception)}</pre></details>` : "";
      return `<tr>
        <td>${esc(row?.at || "-")}</td>
        <td class="runtimeLogLevel ${esc(level)}">${esc(level)}</td>
        <td><code>${esc(row?.logger || "chat2api")}</code></td>
        <td><div class="runtimeLogMessage">${esc(row?.message || "")}</div>${context}${exceptionHtml}</td>
      </tr>`;
    }).join("");
  }

  async function loadLogs() {
    const meta = document.getElementById("runtimeLogMeta");
    if (meta) { meta.className = "runtimeLogMeta muted"; meta.textContent = "正在读取运行日志…"; }
    const data = await request(`/api/admin/runtime-logs?${params(false)}`);
    renderRows(data.data || []);
    if (meta) {
      meta.className = "runtimeLogMeta muted";
      meta.textContent = `缓冲区 ${Number(data.buffered || 0)} 条，本次返回 ${Number(data.returned || 0)} 条；日志已持久化并自动轮转。`;
    }
  }

  async function exportLogs() {
    const response = await fetch(`/api/admin/runtime-logs/export?${params(true)}`, {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match?.[1] || `chat2api-runtime-logs-${Date.now()}.log`;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
    if (typeof globalThis.status === "function") globalThis.status(`运行日志已导出：${filename}`, "ok");
  }

  function showRuntimeLogs() {
    ensureStyles();
    ensureView();
    document.querySelectorAll(".view").forEach(node => node.classList.toggle("active", node.id === "view-runtime-logs"));
    document.querySelectorAll(".nav button").forEach(node => node.classList.toggle("active", node.dataset.view === "runtime-logs"));
    const title = document.getElementById("pageTitle");
    if (title) title.textContent = "运行日志";
    if (location.hash !== "#runtime-logs") history.replaceState(null, "", "#runtime-logs");
    loadLogs().catch(showError);
  }

  ensureStyles();
  ensureNavigation();
  ensureView();

  window.addEventListener("hashchange", () => {
    if (location.hash === "#runtime-logs") showRuntimeLogs();
  });
  if (location.hash === "#runtime-logs") setTimeout(showRuntimeLogs, 0);
})();
