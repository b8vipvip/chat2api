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
      signal: options.signal,
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
    // Catalog and effective policy deliberately share one compact table.
    renderPolicy();
  }

  function renderPolicy() {
    const target = $("workerToolsPolicyRows");
    if (!target) return;
    target.replaceChildren();
    const scope = $("workerToolsScope").value;
    const configured = chosenPolicy?.tools || {};
    for (const tool of catalog) {
      const row = elem("tr");
      row.append(elem("td", tool.name), elem("td", tool.id),
        elem("td", tool.version), elem("td", tool.phase),
        elem("td", tool.available ? "可配置" : "尚未实现"));
      const control = elem("td");
      const select = elem("select");
      select.dataset.probe = tool.id;
      select.disabled = !tool.available;
      const values = scope === "global"
        ? [["", "未配置（默认关闭）"], ["on", "开启"], ["off", "关闭"]]
        : [["", "继承全局"], ["on", "显式开启"], ["off", "显式关闭"]];
      for (const [value, label] of values) {
        const item = elem("option", label); item.value = value; select.append(item);
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

  function enableAbcdEvidencePreset() {
    const selectable = [...document.querySelectorAll("#workerToolsPolicyRows select[data-probe]")]
      .filter(select => !select.disabled);
    if (!chosenPolicy || selectable.length === 0) {
      status("请先读取 Worker 工具策略，再勾选探针", true);
      return;
    }
    // Fill the currently selected policy only. A deliberate Save action is
    // still required so a diagnosis preset cannot silently enable collection.
    for (const select of selectable) select.value = "on";
    const scope = $("workerToolsScope")?.value || "global";
    status("已勾选当前范围 " + scope + " 的 " + selectable.length
      + " 个可用探针。请点击“保存策略”，再运行新的 ABCD 请求。");
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
    if (!$("workerToolsPolicyRows")) return;
    try {
      const [c, p] = await Promise.all([
        json("/api/admin/worker-tools/catalog"),
        json("/api/admin/worker-tools/policies?scope_id=global"),
      ]);
      catalog = c.tools || [];
      workers = p.workers || [];
      const picker = $("workerToolsScope");
      const old = picker.value || "global";
      picker.replaceChildren();
      const global = elem("option", "全局默认"); global.value = "global"; picker.append(global);
      const unique = new Map();
      for (const worker of workers) {
        if (!unique.has(worker.worker_id)) unique.set(worker.worker_id, worker);
      }
      for (const worker of unique.values()) {
        const raw = String(worker.worker_id || "");
        const ext = [worker.extension_client_id,worker.client_id,
          worker.worker_client_id,raw].map(value => String(value||""))
          .find(value => /^ext_[A-Za-z0-9_-]{5,120}$/.test(value));
        const label = (worker.name || "Worker") +
          " · " + (ext || "ext_ 待绑定") +
          (worker.online ? " · 在线" : " · 离线");
        const option = elem("option", label);
        // Keep the backend policy scope key stable and private.
        option.value = "worker:" + worker.worker_id;
        picker.append(option);
      }
      if ([...picker.options].some(option => option.value === old)) picker.value = old;
      await loadPolicy();
      await refreshRuns();
      await nativePageWorkers();
      await nativePageRecords();
    } catch (error) { status(error.message, true); }
  }

  $("workerToolsScope").addEventListener("change", loadPolicy);
  $("workerToolsEnableAbcd").addEventListener("click", enableAbcdEvidencePreset);
  $("workerToolsSave").addEventListener("click", save);
  $("workerToolsReload").addEventListener("click", load);
  $("workerToolsRefreshRuns").addEventListener("click", refreshRuns);
  $("workerToolsInspect").addEventListener("click", () => inspectRequest());
  $("workerToolsDownload").addEventListener("click", () => downloadRequest());
  // Independent, request-bound, versioned Native Page Diagnosis. This tool
  // does not take part in request-v6 completion or per-request probe policies.
  let nativePageTimer = null;
  let nativePageSelectedJobId = "";
  const nativePageStageLabels = Object.freeze({
    queued:"等待调度","windows-dispatching":"向 Windows 扩展下发指令",
    "windows-accepted":"扩展已接收任务",
    "windows-worker_lookup":"核验 Worker 所属窗口",
    "windows-tab_opening":"创建临时标签页",
    "windows-tab_opened":"临时标签页已创建",
    "windows-tab_loading":"历史页面加载中",
    "windows-page_ready":"历史页面已加载",
    "windows-assistant_scanning":"正在定位助手回复",
    "windows-assistant_captured":"DOM 已采集",
    "windows-uploading":"正在回传证据",
    "windows-upload_complete":"证据回传完成",
    "windows-tab_closed":"临时标签页已关闭",
    "windows-failed":"扩展采集失败",
    "complete":"采集完成","failed":"诊断失败",
  });
  function refreshNativePageVisibleMode() {
    const input=$("nativePageForeground");
    const option=$("nativePageWorker")?.selectedOptions?.[0];
    if (!input) return;
    const windows=Boolean(option?.value && option.dataset.platform==="windows");
    input.disabled=!windows;
    if (!windows) input.checked=false;
  }
  function nativePageShowTimeline(record) {
    const target=$("nativePageTimelineList");
    if (!target) return;
    target.replaceChildren();
    $("nativePageTimelineTitle").textContent="执行过程 · " + String(record?.job_id || "").slice(0,12);
    if (!record) {
      target.append(elem("li","尚无诊断任务"));
      return;
    }
    const trace=Array.isArray(record.trace)?record.trace:[];
    if (!trace.length) target.append(elem("li",
      "正在等待 Worker 上报执行阶段（如没有上报，请检查扩展版本和连接）"));
    for (const event of trace) {
      const label=nativePageStageLabels["windows-"+event.stage] || String(event.stage||"未知阶段");
      const ms=Number.isInteger(event.elapsed_ms) ? event.elapsed_ms+"ms" : "—";
      const tab=Number.isInteger(event.tab_id) ? " · 标签页 #"+event.tab_id : "";
      const win=Number.isInteger(event.window_id) ? " · 窗口 #"+event.window_id : "";
      target.append(elem("li",label+" · "+ms+tab+win));
    }
    if (record.status!=="running") target.append(elem("li",
      record.status==="completed" ? "已完成 · ZIP 中包含执行轨迹" :
      "已结束 · "+String(record.error||record.status)));
  }
  function nativePageMessage(message, bad = false) {
    const el = $("nativePageStatus");
    if (!el) return;
    el.textContent = String(message);
    el.style.color = bad ? "#ef9d9d" : "#9cb5d4";
  }
  const nativePageExports = new Map();
  const nativePageExportLimit = 18 * 1024 * 1024;
  const nativePageExportLifetime = 10 * 60 * 1000;
  function nativePageExportButtons() {
    for (const button of root.querySelectorAll("button[data-native-export]")) {
      const entry = nativePageExports.get(button.dataset.nativeExport);
      button.disabled = entry?.state === "loading";
      button.textContent = entry?.state === "loading" ? "下载中…"
        : entry?.state === "ready" ? "再次保存" + button.dataset.exportKind
        : entry?.state === "failed" ? "重试下载" : button.dataset.exportLabel;
    }
  }
  function nativePageReleaseExport(id) {
    const entry = nativePageExports.get(id);
    if (entry?.url) URL.revokeObjectURL(entry.url);
    if (entry?.timer) clearTimeout(entry.timer);
    nativePageExports.delete(id);
  }
  function nativePageRenderExports() {
    const panel = $("nativePageDownloads");
    if (!panel) return;
    panel.replaceChildren();
    const mib = bytes => (bytes / 1048576).toFixed(1) + " MiB";
    for (const entry of nativePageExports.values()) {
      const row = elem("div", undefined, "native-tool-bar");
      row.append(elem("span", entry.filename + " · "));
      if (entry.state === "ready") {
        const link = elem("a", "保存" + entry.kind, "action");
        link.href = entry.url; link.download = entry.filename;
        link.onclick = () => nativePageMessage("已请求浏览器保存文件，请查看浏览器下载列表。");
        entry.link = link;
        row.append(elem("span", mib(entry.loaded) + " · 文件已就绪，保存链接保留 10 分钟。"), link);
      } else if (entry.state === "failed") {
        row.append(elem("span", entry.error));
      } else {
        row.append(elem("span", entry.loaded
          ? "正在接收 " + mib(entry.loaded) + (entry.total ? " / " + mib(entry.total) : "")
          : "正在获取文件…"));
        const cancel = elem("button", "取消", "action");
        cancel.onclick = () => { entry.cancelled = true; entry.controller.abort(); };
        row.append(cancel);
      }
      panel.append(row);
    }
    nativePageExportButtons();
  }
  async function nativePageExport(item, format) {
    const id = item.job_id + ":" + format;
    const existing = nativePageExports.get(id);
    if (existing?.state === "loading") return;
    if (existing?.state === "ready") {
      nativePageRenderExports();
      try { existing.link?.click(); } catch (_) { nativePageMessage("请点击下方保存链接，再查看浏览器下载列表。"); }
      return;
    }
    if (!$("nativePageDownloads")) {
      nativePageMessage("下载界面尚未加载，请刷新页面后重试。", true); return;
    }
    const controller = new AbortController();
    const entry = {state:"loading", loaded:0, total:0, controller,
      kind:format === "zip" ? " ZIP" : "日志",
      filename:(format === "zip" ? "native-page-" +
        (item.target_type === "conversation_url" ? "history-url" : item.request_id) +
        "-" + item.job_id.slice(0,8) + "-v" + item.tool_version + ".zip"
        : "native-page-" + item.job_id.slice(0,8) + "-diagnostic.json").replace(/[\\/\x00-\x1f]/g,"_")};
    nativePageExports.set(id, entry);
    nativePageRenderExports();
    nativePageMessage("正在下载" + entry.kind + "，接收进度显示在下方。");
    const timeout = setTimeout(() => { entry.timedOut = true; controller.abort(); }, 120000);
    let reader;
    try {
      const response = await apiRequest("/api/admin/worker-tools/native-page/jobs/" +
        encodeURIComponent(item.job_id) + (format === "zip" ? "/download" : "/log"),
        {signal:controller.signal});
      const declared = response.headers.get("Content-Length") || "";
      entry.total = format === "zip" && Number(item.size) > 0 ? Number(item.size)
        : !response.headers.get("Content-Encoding") && /^\d+$/.test(declared) ? Number(declared) : 0;
      if (entry.total > nativePageExportLimit) throw new Error("文件超出诊断包大小上限，请重新诊断后重试。");
      let blob;
      if (response.body?.getReader) {
        reader = response.body.getReader();
        const chunks = [];
        let reported = 0;
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          entry.loaded += value.byteLength;
          if (entry.loaded > nativePageExportLimit) throw new Error("文件超出诊断包大小上限。");
          chunks.push(value);
          if (entry.loaded - reported >= 262144) { reported = entry.loaded; nativePageRenderExports(); }
        }
        blob = new Blob(chunks, {type:format === "zip" ? "application/zip" : "application/json"});
      } else {
        blob = await response.blob(); entry.loaded = blob.size;
      }
      if (controller.signal.aborted) throw new Error("下载已中断");
      if (!blob.size || blob.size > nativePageExportLimit || entry.total && blob.size !== entry.total)
        throw new Error("文件未接收完整，请点击重试下载。");
      if (format === "zip") {
        const head = new Uint8Array(await blob.slice(0,4).arrayBuffer());
        if (head[0] !== 80 || head[1] !== 75 || !((head[2] === 3 && head[3] === 4) || (head[2] === 5 && head[3] === 6)))
          throw new Error("返回内容不是有效的诊断 ZIP，请重新登录或重试。");
      }
      if (controller.signal.aborted) throw new Error("下载已中断");
      entry.url = URL.createObjectURL(blob); entry.state = "ready";
      // Bound retained files, while allowing a real user click to retry saving
      // without another authenticated fetch or an expired user activation.
      const ready = [...nativePageExports].filter(([,x]) => x.state === "ready");
      while (ready.length > 3) nativePageReleaseExport(ready.shift()[0]);
      entry.timer = setTimeout(() => { nativePageReleaseExport(id); nativePageRenderExports(); }, nativePageExportLifetime);
      nativePageRenderExports();
      try { entry.link.click(); } catch (_) { /* The visible link remains usable. */ }
      nativePageMessage("文件已接收，已请求浏览器保存。没有出现下载时，请点击下方“保存" + entry.kind + "”。");
    } catch (error) {
      entry.state = "failed";
      entry.error = entry.cancelled ? "下载已取消，可点击重试下载。"
        : entry.timedOut ? "下载超时，可点击重试下载。"
        : error.name === "TypeError" || error.name === "AbortError" ? "网络连接中断，可点击重试下载。"
        : String(error.message || "下载失败，请重试。");
      nativePageRenderExports(); nativePageMessage(entry.error, true);
    } finally {
      clearTimeout(timeout); controller.abort();
      if (reader) { try { await reader.cancel(); } catch (_) {} reader.releaseLock(); }
    }
  }
  window.addEventListener("pagehide", () => {
    for (const [id,entry] of nativePageExports) {
      entry.controller.abort(); nativePageReleaseExport(id);
    }
    nativePageRenderExports();
  });
  async function nativePageWorkers() {
    const target = $("nativePageWorker");
    if (!target) return;
    // Public identity uses the canonical ext_ worker ID on both platforms;
    // the server resolves the internal Linux controller for existing jobs.
    const response = await json("/api/admin/worker-tools/native-page/workers");
    const old = target.value;
    target.replaceChildren();
    const none = elem("option", "请选择 Worker（Windows / Linux）");
    none.value = "";
    target.append(none);
    for (const worker of response.data || []) {
      const wid = String(worker.worker_id || "");
      if (!/^ext_[a-zA-Z0-9_-]{5,120}$/.test(wid)) continue;
      const os = worker.platform === "windows" ? "Windows" : "Linux";
      const windows=worker.platform==="windows";
      const supported=worker.diagnostic_supported===true;
      const reported=String(worker.diagnostic_version||"");
      const extension=String(worker.extension_version||"");
      const versionLabel=windows ? (extension ? " · 扩展 "+extension : "")+
        (reported ? " · 诊断 "+reported : " · 诊断能力未上报")+
        (supported ? " · 已就绪" : reported ? " · 需升级扩展" :
          " · 请重连 Worker 刷新能力")+
        (worker.diagnostic_tool_version ? " · 独立工具 "+worker.diagnostic_tool_version : "") : "";
      const label = wid + " · " + os +
        (worker.name && worker.name !== wid ? " · " + worker.name : "") +
        (worker.online ? " · 在线" : " · 离线") + versionLabel;
      const option = elem("option", label);
      option.value = wid;
      option.dataset.platform = worker.platform || "linux";
      option.dataset.diagnosticSupported = supported ? "true" : "false";
      // Do not disable the option: an admin needs to select it to inspect
      // the installed version even when status metadata is not yet synced.
      // The authenticated POST endpoint remains the final capability gate.
      target.append(option);
    }
    if ([...target.options].some(item => item.value === old)) target.value = old;
    refreshNativePageVisibleMode();
  }
  async function nativePageRecords() {
    if (!$("nativePageRecords")) return;
    const data = await json("/api/admin/worker-tools/native-page/jobs?limit=40");
    const rows = data.data || [];
    const target = $("nativePageRecords");
    target.replaceChildren();
    let active = false;
    if (!nativePageSelectedJobId && rows.length) nativePageSelectedJobId=String(rows[0].job_id||"");
    const selected=rows.find(x=>x.job_id===nativePageSelectedJobId);
    nativePageShowTimeline(selected||rows[0]||null);
    for (const item of rows) {
      const row = elem("tr");
      const state = String(item.status || "");
      const progress = Math.min(100, Math.max(0, Number(item.percent || 0)));
      active ||= state === "running";

      // Keep this table compact. Technical stages, raw error strings and
      // diagnostic counters belong in the existing "导出日志" action only.
      const issue = String(item.error || "");
      const stateLabel = state === "completed" ? "已完成"
        : state === "failed" ? "失败"
        : state === "running" ? "运行中" : "未知";
      const resultLabel = state === "completed" ? "证据已生成"
        : state === "running" ? "诊断中"
        : /history_.*(?:hydration|stalled)|history_assistant_boundary_not_found/.test(issue)
          ? "未获取回复"
        : /(?:429|rate_limit)/.test(issue) ? "加载受限"
        : /(?:access_gate|challenge)/.test(issue) ? "访问受限"
        : /(?:navigation|redirect|target_not_visible|tab_missing)/.test(issue)
          ? "页面不可用"
        : state === "failed" ? "诊断未通过" : "待处理";
      const stageLabel=nativePageStageLabels[item.stage] || String(item.stage||"");
      const elapsed=Number.isInteger(item.elapsed_ms) ? " · "+item.elapsed_ms+"ms" : "";
      const tabId=Number.isInteger(item.diagnostic_tab_id) ? " · Tab #"+item.diagnostic_tab_id : "";
      const stateCell = elem("td", stateLabel + " · " + resultLabel +
        (state==="running" ? " · "+stageLabel+elapsed+tabId : ""));
      row.append(elem("td", String(item.job_id || "").slice(0,12)),
        elem("td", item.target_label || item.request_id || "-", "native-id"),
        elem("td", item.worker_id || "-", "native-id"),
        elem("td", item.tool_version || "未报告"), stateCell);
      const pct = elem("td");
      const bar = elem("progress");
      bar.max = 100; bar.value = progress;
      pct.append(bar, document.createTextNode(" " + progress + "%"));
      row.append(pct);
      const cell = elem("td");
      const log = elem("button", "导出日志", "action");
      log.dataset.nativeExport = item.job_id + ":log";
      log.dataset.exportKind = "日志"; log.dataset.exportLabel = "导出日志";
      log.onclick = () => nativePageExport(item, "log");
      cell.append(log);
      const timeline=elem("button","查看轨迹","action");
      timeline.onclick=()=>{nativePageSelectedJobId=String(item.job_id||"");nativePageShowTimeline(item);};
      cell.append(timeline);
      if (state === "completed") {
        const action = elem("button", "导出诊断 ZIP", "action");
        action.dataset.nativeExport = item.job_id + ":zip";
        action.dataset.exportKind = " ZIP"; action.dataset.exportLabel = "导出诊断 ZIP";
        action.onclick = () => nativePageExport(item, "zip");
        cell.append(action);
      }
      row.append(cell); target.append(row);
    }
    if (!rows.length) {
      const empty = elem("tr");
      const cell = elem("td", "暂无诊断记录");
      cell.colSpan = 7; empty.append(cell); target.append(empty);
    }
    nativePageExportButtons();
    if (nativePageTimer) { clearTimeout(nativePageTimer); nativePageTimer = null; }
    if (active && $("view-worker-tools").classList.contains("active")) {
      nativePageTimer = setTimeout(() => nativePageRecords().catch(
        error => nativePageMessage(error.message, true)), 2200);
    }
  }
  const nativePageRequestPattern = /^(?:req|resp|imgreq)_[a-zA-Z0-9_-]{6,100}$/;
  const nativePageUrlPattern = /^https:\/\/(?:chatgpt\.com|www\.chatgpt\.com|chat\.openai\.com)\/c\/[a-zA-Z0-9-]{12,90}\/?$/;
  async function nativePageStart() {
    const target = $("nativePageRequestId")?.value.trim() || "";
    const isRequest = nativePageRequestPattern.test(target);
    const isHistory = nativePageUrlPattern.test(target);
    if (!isRequest && !isHistory)
      throw new Error("请填写完整请求 ID，或 https://chatgpt.com/c/... 历史链接");
    const chosen = $("nativePageWorker")?.value || "";
    let worker_id = chosen;
    if (isRequest) {
      const found = await json("/api/admin/worker-tools/native-page/request-worker/" +
        encodeURIComponent(target));
      const owner = String(found.worker_id || "");
      if (chosen && owner && chosen !== owner)
        throw new Error("该请求属于 " + owner + "，不能指定其他 Worker 采集");
      worker_id = owner || chosen;
      if (!worker_id) throw new Error("请求记录没有可识别的 ext_ Worker");
      if ([...$("nativePageWorker").options].some(x => x.value === worker_id))
        $("nativePageWorker").value = worker_id;
    } else if (!worker_id) {
      throw new Error("通过历史链接诊断时，请选择登录该 ChatGPT 账号的 Windows 或 Linux Worker");
    }
    refreshNativePageVisibleMode();
    const show_tab=$("nativePageForeground")?.checked===true &&
      $("nativePageWorker")?.selectedOptions?.[0]?.dataset?.platform==="windows";
    const response = await json("/api/admin/worker-tools/native-page/jobs", {
      method: "POST", body: {worker_id, target, show_tab},
    });
    nativePageSelectedJobId=String(response.job.job_id||"");
    nativePageMessage("诊断任务 " + response.job.job_id.slice(0,12) +
      (show_tab ? " 已创建：Windows 空闲时打开可见临时标签页，完成后恢复原活动页。" :
       " 已创建：默认后台打开临时标签页；执行阶段显示在下方。"));
    await nativePageRecords();
  }
  async function nativePageToolAction(action) {
    const worker = $("nativePageWorker")?.value || "";
    if (!worker) throw new Error("请先选择目标 Worker");
    const path = "/api/admin/worker-tools/native-page/workers/" +
      encodeURIComponent(worker) + (action === "update" ? "/update" : "/version");
    const platform = $("nativePageWorker")?.selectedOptions?.[0]?.dataset?.platform;
    nativePageMessage(action === "update" ? "正在更新工具，并验证代码 SHA-256…" : "正在检查工具版本…");
    const result = await json(path,action === "update" ? {method:"POST"} : undefined);
    if(platform==="windows") await nativePageWorkers(); // Refresh actual locally acknowledged version.
    const tool = result.tool || {};
    const latest = result.latest || {};
    const local = String(tool.version || "不可用");
    const remote = String(latest.version || "未知");
    const same = Boolean(tool.sha256 && latest.sha256 && tool.sha256 === latest.sha256);
    const updateState = action === "update"
      ? tool.updated === false ? " · 已是最新，无需重复安装" : " · 安装完成并通过 SHA-256 校验"
      : same ? " · 当前已是最新" : latest.version
        ? " · 有独立诊断工具更新可用" : "";
    const latestLabel = "独立发布分支最新";
    nativePageMessage("Worker " + worker + " · 已安装 " + local +
      " · " + latestLabel + " " + remote + " · " +
      (result.latest_error ? "查询失败：" + result.latest_error
        : tool.installed ? "已安装" : "未安装") + updateState,
      Boolean(result.latest_error));
  }
  $("nativePageWorker")?.addEventListener("change",refreshNativePageVisibleMode);
  let nativeWorkerLookup = 0;
  $("nativePageRequestId")?.addEventListener("change", async () => {
    const id = $("nativePageRequestId")?.value.trim() || "";
    if (nativePageUrlPattern.test(id)) {
      nativePageMessage("已识别原生历史链接，请选择登录该账号的 Worker；无需请求 ID。");
      return;
    }
    if (!nativePageRequestPattern.test(id)) return;
    const current = ++nativeWorkerLookup;
    try {
      const found = await json("/api/admin/worker-tools/native-page/request-worker/" +
        encodeURIComponent(id));
      if (current !== nativeWorkerLookup) return;
      if (found.worker_id && [...$("nativePageWorker").options].some(x => x.value === found.worker_id)) {
        $("nativePageWorker").value = found.worker_id;
        refreshNativePageVisibleMode();
        nativePageMessage("已从请求记录定位 Worker：" + found.worker_id +
          (found.conversation_link_saved
            ? "，并找到了已保存的原生会话链接。无需另外填写。"
            : "。未保存会话链接时可直接在同一输入框粘贴 ChatGPT /c/ 链接单独诊断。"));
      }
    } catch (error) { nativePageMessage(error.message, true); }
  });
  $("nativePageStart")?.addEventListener("click", () => nativePageStart().catch(
    error => nativePageMessage(error.message, true)));
  $("nativePageVersion")?.addEventListener("click", () => nativePageToolAction("version").catch(
    error => nativePageMessage(error.message, true)));
  $("nativePageUpdate")?.addEventListener("click", () => nativePageToolAction("update").catch(
    error => nativePageMessage(error.message, true)));
  $("nativePageReload")?.addEventListener("click", () => Promise.all([
    nativePageWorkers(),nativePageRecords()
  ]).catch(error => nativePageMessage(error.message, true)));

  window.loadWorkerTools = load;
  const navigation = document.querySelector('.nav button[data-view="worker-tools"]');
  navigation?.addEventListener("click", () => { load().catch(error => status(error.message, true)); });
  if (root.classList.contains("active") || location.hash === "#worker-tools")
    load().catch(error => status(error.message, true));
})();
