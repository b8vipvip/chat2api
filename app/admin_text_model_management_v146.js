(() => {
  const KEY = "__CHAT2API_TEXT_MODEL_MANAGEMENT_V146__";
  if (globalThis[KEY]) return;
  const state = {catalog: null, loading: false};
  globalThis[KEY] = state;
  const byId = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
  const apiFetch = async (path, options={}) => {
    if (typeof api === "function") return api(path, options);
    throw new Error("管理员 API 未就绪");
  };
  function setup() {
    const nav = document.querySelector(".side .nav, aside .nav");
    const content = document.querySelector(".content");
    if (!nav || !content) return;
    if (!byId("modelManagementNavV146")) {
      const button = document.createElement("button");
      button.id = "modelManagementNavV146";
      button.textContent = "模型管理";
      button.dataset.view = "text-models";
      nav.insertBefore(button, [...nav.querySelectorAll("button")].find(x => x.textContent.includes("测试场")) || null);
      button.addEventListener("click", () => showModels());
    }
    if (!byId("view-text-models")) {
      const panel = document.createElement("section");
      panel.id = "view-text-models"; panel.className = "view";
      panel.innerHTML = `
      <div class="panel" style="margin-bottom:14px">
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
          <div><h2>文本模型管理</h2><div class="muted">从所有 Worker 的 ChatGPT 页面实时发现模型，记录验证、来源和更新历史。未验证的发现结果不会参与路由。</div></div>
          <button class="action" id="textModelRefreshV146">刷新服务端状态</button>
        </div>
        <div id="textModelSummaryV146" class="muted" style="margin-top:10px">正在读取模型状态…</div>
        <div class="scroll"><table style="width:100%"><thead><tr><th>模型</th><th>状态</th><th>已发现 Worker</th><th>已验证 Worker</th><th>可路由 Worker</th><th>管理</th></tr></thead><tbody id="textModelRowsV146"></tbody></table></div>
      </div>
      <div class="panel" style="margin-bottom:14px"><h3>Worker 模型来源</h3><div class="muted">保留页面选择器证据、检测时间、账号类型、验证错误及 Worker 身份。</div><div class="scroll"><table style="width:100%"><thead><tr><th>Worker</th><th>在线/登录</th><th>发现状态</th><th>观察到的文本模型</th><th>已验证模型</th><th>更新时间/错误</th></tr></thead><tbody id="textModelWorkersV146"></tbody></table></div></div>
      <div class="panel"><h3>模型获取及管理更新记录</h3><div class="muted">记录 Worker 上报的模型变化、验证失败，以及管理员启用/停用操作，保存在服务器数据卷。</div><div class="scroll"><table style="width:100%"><thead><tr><th>UTC 时间</th><th>事件</th><th>Worker / 模型</th><th>变化</th></tr></thead><tbody id="textModelHistoryV146"></tbody></table></div></div>
      `;
      content.appendChild(panel);
      byId("textModelRefreshV146").onclick = () => refresh(true);
      panel.addEventListener("click", async event => {
        const button = event.target.closest("button[data-manage-model]");
        if (!button) return;
        const model = button.dataset.manageModel;
        const enabled = button.dataset.enableModel === "true";
        button.disabled = true;
        try {
          await apiFetch("/api/admin/text-models/" + encodeURIComponent(model), {method:"PATCH",body:{enabled}});
          await refresh(true);
        } catch (error) {
          alert("更新文本模型失败：" + String(error?.message || error));
        } finally { button.disabled = false; }
      });
    }
  }
  function showModels() {
    setup();
    if (typeof titles !== "undefined") titles["text-models"] = "模型管理";
    if (typeof show === "function") {
      show("text-models");
    } else {
      document.querySelectorAll(".view").forEach(x => x.classList.toggle("active", x.id === "view-text-models"));
      const title = byId("pageTitle"); if (title) title.textContent = "模型管理";
      location.hash = "text-models";
    }
    document.querySelectorAll(".nav button").forEach(x => x.classList.toggle("active", x.id === "modelManagementNavV146"));
    refresh(true);
  }
  // The enabled Text Model Management catalog is the single picker source.
  function syncSelect(id, rows) {
    const element = byId(id);
    if (!element) return;
    const previous = element.value;
    const models = rows.filter(row => row.enabled).sort((a,b) => a.id.localeCompare(b.id));
    const options = models.map(row => ({id:row.id,label:row.id}));
    if (JSON.stringify(options) !== JSON.stringify([...element.options].map(o => ({id:o.value,label:o.textContent})))) {
      element.innerHTML = options.map(row => `<option value="${esc(row.id)}">${esc(row.label)}</option>`).join("");
    }
    element.value = models.some(row => row.id === previous) ? previous : models[0]?.id || "";
  }
  function syncPlayground(catalog) {
    const rows = Array.isArray(catalog.data) ? catalog.data : [];
    syncSelect("testModel", rows);
    syncSelect("pgChatModel", rows);
    const button = byId("pgChatSend");
    if (button && button.textContent === "发送") {
      button.disabled = !rows.some(row => row.enabled);
      button.title = button.disabled ? "文本模型管理中没有启用的模型" : "";
    }
  }
  function render(catalog) {
    const models = catalog.data || [];
    const workers = catalog.workers || [];
    const history = catalog.history || [];
    const summary = byId("textModelSummaryV146");
    if (summary) summary.textContent = `Worker ${workers.length} · 在线 ${workers.filter(x=>x.online).length} · 已发现模型 ${models.length} · 可路由模型 ${models.filter(x=>x.routable_by?.length).length} · 更新记录 ${history.length} 条（最多显示200条）`;
    const tbody = byId("textModelRowsV146");
    if (tbody) tbody.innerHTML = models.length ? models.map(row => `<tr>
      <td><code>${esc(row.id)}</code></td><td>${esc(row.state)}</td>
      <td title="${esc(row.observed_by?.join(", ")||"")}">${row.observed_by?.length||0}</td>
      <td title="${esc(row.verified_by?.join(", ")||"")}">${row.verified_by?.length||0}</td>
      <td title="${esc(row.routable_by?.join(", ")||"")}">${row.routable_by?.length||0}</td>
      <td><button class="action" data-manage-model="${esc(row.id)}" data-enable-model="${row.enabled?"false":"true"}">${row.enabled?"停用":"启用"}</button></td>
    </tr>`).join("") : '<tr><td colspan="6">尚无 Worker 的真实模型选择器证据；请查看下方验证失败原因。</td></tr>';
    const workerBody = byId("textModelWorkersV146");
    if (workerBody) workerBody.innerHTML = workers.map(worker=> `<tr>
      <td><b>${esc(worker.name)}</b><div class="muted"><code>${esc(worker.client_id)}</code> · ${esc(worker.account_type)} · ${esc(worker.extension_version)}</div></td>
      <td>${worker.online?"在线":"离线"} / ${worker.routing_ready?"就绪":"未就绪"}</td>
      <td>${esc(worker.validation_state)} · 候选 ${worker.candidates} / 验证 ${worker.verified_count}</td>
      <td>${worker.observed.map(item=>`<div title="${esc(item.method)}">${esc(item.id)}</div>`).join("")||"—"}</td>
      <td>${worker.validated.map(item=>`<div title="${esc(item.method)}">${esc(item.id)}</div>`).join("")||"—"}</td>
      <td class="muted">${esc(worker.updated_at||"—")}<div style="color:var(--bad)">${esc(worker.validation_error)}</div></td>
    </tr>`).join("");
    const historyBody = byId("textModelHistoryV146");
    if (historyBody) historyBody.innerHTML = history.map(event => {
      const now = event.current || {}, old = event.previous || {};
      const info = event.event === "worker-model-report"
        ? `${old.validation_state||"—"} → ${now.validation_state||"—"}; 候选 ${now.observed?.length||0}; 验证 ${now.validated?.length||0}; ${now.validation_error||""}`
        : (event.enabled ? "管理员启用" : "管理员停用");
      return `<tr><td>${esc(event.at)}</td><td>${esc(event.event)}</td><td><code>${esc(event.client_id||event.model_id||"")}</code></td><td title="${esc(info)}">${esc(info)}</td></tr>`;
    }).join("") || '<tr><td colspan="4">暂无更新记录</td></tr>';
  }
  async function refresh(showError=false) {
    if (state.loading) return;
    state.loading = true;
    try {
      const catalog = await apiFetch("/api/admin/text-models");
      if (!catalog || !Array.isArray(catalog.data)) throw new Error("服务端未返回模型管理数据");
      state.catalog = catalog;
      syncPlayground(catalog);
      render(catalog);
    } catch (error) {
      if (showError && byId("textModelSummaryV146")) byId("textModelSummaryV146").textContent =
        "无法读取模型管理数据：" + String(error?.message || error);
      // No stale/static model choices may be used if the live source fails.
      if (!state.catalog) syncPlayground({data:[]});
    } finally { state.loading = false; }
  }
  function start() {
    setup();
    if (location.hash === "#text-models") showModels();
    refresh(false);
    setInterval(() => refresh(false), 5000);
    document.addEventListener("focusin", event => {
      if (["testModel","pgChatModel"].includes(event.target?.id)) refresh(false);
    }, true);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();