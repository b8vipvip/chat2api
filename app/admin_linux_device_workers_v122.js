(() => {
  const KEY = "__CHAT2API_LINUX_DEVICE_WORKERS_V122__";
  if (globalThis[KEY]) return;

  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const request = async (path, options = {}) => {
    const {headers = {}, ...rest} = options;
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...rest,
      headers: {"Content-Type":"application/json", ...headers},
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
    return payload;
  };

  let devices = [];
  let selectedDevice = null;

  const proxyReady = worker => {
    const meta = worker?.metadata && typeof worker.metadata === "object" ? worker.metadata : {};
    const summary = meta.proxy_summary && typeof meta.proxy_summary === "object" ? meta.proxy_summary : {};
    return ["connected","ready"].includes(String(worker?.proxy_status || "").toLowerCase()) && Boolean(String(summary.protocol || "").trim());
  };
  const chatgptText = worker => {
    const meta = worker?.metadata && typeof worker.metadata === "object" ? worker.metadata : {};
    const bridge = meta.bridge && typeof meta.bridge === "object" ? meta.bridge : {};
    return String(worker?.chatgpt_status || "").toLowerCase() === "ready" || (String(bridge.login_state || "").toLowerCase() === "ready" && bridge.composer_ready === true)
      ? "已登录"
      : "未登录";
  };
  const statusText = worker => {
    const state = String(worker?.status || "").toLowerCase();
    if (state === "ready") return "运行正常";
    if (["waiting_login","login_checking"].includes(state)) return "待登录";
    if (state === "waiting_proxy") return "待配置代理";
    if (["degraded","error","offline"].includes(state)) return "异常/离线";
    return state || "-";
  };

  const relayWorkerAction = (action, worker) => {
    const tbody = document.getElementById("linuxWorkerRows");
    const host = tbody?.querySelector("tr td:last-child");
    if (!tbody || !host || !worker?.worker_id) return false;
    const button = document.createElement("button");
    button.type = "button";
    button.style.display = "none";
    const workerId = String(worker.worker_id);
    const workerName = `${selectedDevice?.name || "设备"} · Worker ${worker.worker_slot || ""}`;
    if (action === "login") {
      button.dataset.login = workerId;
      button.dataset.workerName = workerName;
    } else if (action === "proxy") {
      button.dataset.proxy = workerId;
      button.dataset.workerName = workerName;
    } else if (action === "pairing") {
      button.dataset.workerPairingV2219 = workerId;
      button.dataset.workerName = workerName;
    } else if (action === "delete") {
      button.dataset.workerDeleteV2219 = workerId;
      button.dataset.workerName = workerName;
    } else {
      return false;
    }
    host.appendChild(button);
    button.click();
    setTimeout(() => button.remove(), 0);
    return true;
  };

  const init = () => {
    const section = document.getElementById("view-linux-workers");
    const toolbar = section?.querySelector(".toolbar");
    const tbody = document.getElementById("linuxWorkerRows");
    if (!section || !toolbar || !tbody) {
      setTimeout(init, 120);
      return;
    }
    if (section.dataset.deviceWorkersV122 === "1") return;
    section.dataset.deviceWorkersV122 = "1";

    const style = document.createElement("style");
    style.textContent = `
      #linuxDeviceWorkerDialogV122 table{width:100%;border-collapse:collapse}
      #linuxDeviceWorkerDialogV122 th,#linuxDeviceWorkerDialogV122 td{padding:9px 8px;border-bottom:1px solid #263449;text-align:left;vertical-align:middle}
      #linuxDeviceWorkerDialogV122 th{font-size:12px;color:#94a3b8;white-space:nowrap}
      #linuxDeviceWorkerDialogV122 .dw-actions{display:flex;gap:6px;flex-wrap:wrap}
      #linuxDeviceWorkerDialogV122 .dw-command{word-break:break-all;white-space:normal;line-height:1.55}
      #linuxDeviceWorkerDialogV122 .dw-muted{color:#94a3b8;font-size:12px}
    `;
    document.head.appendChild(style);

    const title = document.createElement("div");
    title.textContent = "设备列表";
    title.style.cssText = "font-size:16px;font-weight:700;margin:0 0 12px;color:#e5e7eb";
    toolbar.parentNode.insertBefore(title, toolbar);

    const nameInput = document.getElementById("linuxWorkerName");
    if (nameInput) nameInput.placeholder = "设备名称";
    const createDevice = document.getElementById("createLinuxWorker");
    if (createDevice) createDevice.textContent = "新增设备";

    const manage = document.createElement("button");
    manage.className = "action good";
    manage.id = "manageDeviceWorkersV122";
    manage.type = "button";
    manage.textContent = "管理设备 Worker";
    const refresh = document.getElementById("refreshLinuxWorkers");
    toolbar.insertBefore(manage, refresh || null);

    const help = toolbar.nextElementSibling;
    if (help && help.tagName === "DIV") {
      help.textContent = "设备列表一行代表一台物理 Linux 设备。每台设备可增加多个相互隔离的 Worker；每个 Worker 使用独立 Chrome Profile、扩展身份、代理/CDP 资源和 ChatGPT 登录状态。Worker 的并发与窗口数仍在“Worker管理”中分别设置。";
    }
    const header = tbody.closest("table")?.querySelector("thead tr");
    if (header?.children?.[0]) header.children[0].textContent = "设备名称";

    const dialog = document.createElement("dialog");
    dialog.id = "linuxDeviceWorkerDialogV122";
    dialog.style.cssText = "width:min(1080px,calc(100vw - 28px));max-width:none;border:1px solid #334155;border-radius:12px;background:#0f172a;color:#e5e7eb;padding:0;box-shadow:0 24px 90px rgba(0,0,0,.65)";
    dialog.innerHTML = `<div style="padding:18px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
        <div><div style="font-size:18px;font-weight:700">设备 Worker 管理</div><div class="dw-muted" style="margin-top:4px">同一设备的 Worker 互不共享 ChatGPT 登录态；请给 Worker 1/2/3 分别登录需要的账号。</div></div>
        <button class="action" id="closeDeviceWorkersV122" type="button">关闭</button>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:15px">
        <label>设备 <select id="deviceWorkerSelectV122" style="padding:8px 10px;border:1px solid #334155;border-radius:8px;background:#0b1322;color:#e5e7eb"></select></label>
        <button class="action" id="refreshDeviceWorkersV122" type="button">刷新</button>
        <button class="action good" id="addDeviceWorkerV122" type="button">增加 Worker</button>
      </div>
      <div id="deviceWorkerSummaryV122" class="dw-muted" style="margin-top:10px"></div>
      <div style="overflow:auto;margin-top:12px"><table><thead><tr><th>Worker</th><th>状态</th><th>代理</th><th>ChatGPT</th><th>Extension</th><th>操作</th></tr></thead><tbody id="deviceWorkerBodyV122"></tbody></table></div>
      <div id="deviceWorkerPendingV122" style="margin-top:14px"></div>
      <div id="deviceWorkerCommandV122" style="display:none;margin-top:14px;padding:12px;border:1px solid #334155;border-radius:9px;background:#020617">
        <div style="font-weight:700">安装命令</div>
        <div class="dw-muted" style="margin-top:5px">在所选设备的 SSH/终端执行一次。安装完成后点“刷新”，即可单独配置代理、登录 ChatGPT 和设置配对码。</div>
        <code id="deviceWorkerCommandTextV122" class="dw-command" style="display:block;margin-top:8px"></code>
        <button class="action" id="copyDeviceWorkerCommandV122" type="button" style="margin-top:9px">复制命令</button>
      </div>
      <div id="deviceWorkerResultV122" class="dw-muted" style="min-height:20px;margin-top:10px"></div>
    </div>`;
    document.body.appendChild(dialog);

    const select = document.getElementById("deviceWorkerSelectV122");
    const body = document.getElementById("deviceWorkerBodyV122");
    const pending = document.getElementById("deviceWorkerPendingV122");
    const result = document.getElementById("deviceWorkerResultV122");
    const commandBox = document.getElementById("deviceWorkerCommandV122");
    const commandText = document.getElementById("deviceWorkerCommandTextV122");

    const render = () => {
      selectedDevice = devices.find(item => String(item.primary_worker_id || item.worker_id || "") === select.value) || devices[0] || null;
      const workers = Array.isArray(selectedDevice?.device_workers) ? selectedDevice.device_workers : [];
      document.getElementById("deviceWorkerSummaryV122").textContent = selectedDevice
        ? `${selectedDevice.name || "设备"} · ${selectedDevice.hostname || ""} · 已有 ${workers.length} 个 Worker${selectedDevice.next_worker_slot ? ` · 下一个 Worker ${selectedDevice.next_worker_slot}` : ""}`
        : "暂无已安装设备";
      body.innerHTML = workers.map(worker => {
        const proxy = proxyReady(worker);
        const bridge = worker?.metadata?.bridge && typeof worker.metadata.bridge === "object" ? worker.metadata.bridge : {};
        const extension = worker.extension_client_id || bridge.client_id ? (bridge.online === false ? "离线" : "已连接") : "未绑定";
        return `<tr>
          <td><b>Worker ${esc(worker.worker_slot || "-")}</b><div class="dw-muted">${esc(worker.name || worker.worker_id || "")}</div></td>
          <td>${esc(statusText(worker))}</td>
          <td>${proxy ? "已配置" : "未配置"}</td>
          <td>${esc(chatgptText(worker))}</td>
          <td>${esc(extension)}</td>
          <td><div class="dw-actions">
            <button class="action" data-v122-action="proxy" data-worker-id="${esc(worker.worker_id)}">代理</button>
            <button class="action" data-v122-action="login" data-worker-id="${esc(worker.worker_id)}" ${proxy ? "" : "disabled"} title="${proxy ? "打开此 Worker 的独立 ChatGPT 登录窗口" : "请先为此 Worker 配置并验证代理"}">登录</button>
            <button class="action" data-v122-action="pairing" data-worker-id="${esc(worker.worker_id)}">配对码</button>
            ${Number(worker.worker_slot || 1) > 1 ? `<button class="action danger" data-v122-action="delete" data-worker-id="${esc(worker.worker_id)}">删除</button>` : ""}
          </div></td>
        </tr>`;
      }).join("") || '<tr><td colspan="6" class="dw-muted">此设备尚未完成 Worker 安装。</td></tr>';

      const slotInstalls = Array.isArray(selectedDevice?.slot_installations) ? selectedDevice.slot_installations : [];
      const waiting = slotInstalls.filter(item => !item.worker_id && !["failed","disabled"].includes(String(item.state || "")));
      pending.innerHTML = waiting.length ? `<div style="font-weight:700;margin-bottom:7px">待执行安装命令</div>${waiting.map(item => `<div style="padding:9px;border:1px solid #334155;border-radius:8px;margin-top:7px"><b>Worker ${esc(item.worker_slot)}</b> · ${esc(item.name || "")}<div class="dw-command" style="margin-top:5px"><code>${esc(item.install_command || "")}</code></div><div class="dw-actions" style="margin-top:7px"><button class="action" data-copy-slot-install="${esc(item.install_id)}">复制</button><button class="action danger" data-cancel-slot-install="${esc(item.install_id)}">取消命令</button></div></div>`).join("")}` : "";
      document.getElementById("addDeviceWorkerV122").disabled = !selectedDevice?.primary_worker_id || !selectedDevice?.next_worker_slot;
    };

    const refreshDevices = async (keepSelection = true) => {
      const previous = keepSelection ? select.value : "";
      const payload = await request("/api/admin/linux-worker-installations");
      devices = (Array.isArray(payload.data) ? payload.data : []).filter(item => item.worker_id || item.record_type === "installation");
      select.innerHTML = devices.map(item => `<option value="${esc(item.primary_worker_id || item.worker_id || "")}">${esc(item.name || item.hostname || "设备")}${item.worker_count ? ` · ${item.worker_count} Worker` : ""}</option>`).join("");
      if (previous && [...select.options].some(option => option.value === previous)) select.value = previous;
      render();
    };

    manage.onclick = async () => {
      result.textContent = "正在读取设备…";
      commandBox.style.display = "none";
      try {
        await refreshDevices(false);
        result.textContent = "";
        dialog.showModal();
      } catch (error) { alert(error.message); }
    };
    select.onchange = render;
    document.getElementById("refreshDeviceWorkersV122").onclick = async () => {
      result.textContent = "正在刷新…";
      try { await refreshDevices(true); result.textContent = "已刷新"; } catch (error) { result.textContent = error.message; }
    };
    document.getElementById("addDeviceWorkerV122").onclick = async () => {
      if (!selectedDevice?.primary_worker_id) return;
      const slot = Number(selectedDevice.next_worker_slot || 0);
      if (!slot) return;
      result.textContent = `正在生成 Worker ${slot} 安装命令…`;
      try {
        const payload = await request(`/api/admin/linux-workers/${encodeURIComponent(selectedDevice.primary_worker_id)}/slots`, {method:"POST",body:JSON.stringify({slot})});
        commandText.textContent = payload.install_command || "";
        commandBox.style.display = "block";
        result.textContent = `Worker ${payload.worker_slot} 已预留。请复制命令到 ${payload.device_name || selectedDevice.name || "该设备"} 执行。`;
        await refreshDevices(true);
      } catch (error) { result.textContent = error.message; }
    };
    document.getElementById("copyDeviceWorkerCommandV122").onclick = async () => {
      if (!commandText.textContent) return;
      await navigator.clipboard.writeText(commandText.textContent);
      result.textContent = "安装命令已复制";
    };
    body.onclick = event => {
      const button = event.target.closest?.("[data-v122-action]");
      if (!button || button.disabled) return;
      const worker = (selectedDevice?.device_workers || []).find(item => String(item.worker_id || "") === String(button.dataset.workerId || ""));
      if (!worker) return;
      relayWorkerAction(button.dataset.v122Action, worker);
    };
    pending.onclick = async event => {
      const copy = event.target.closest?.("[data-copy-slot-install]");
      const cancel = event.target.closest?.("[data-cancel-slot-install]");
      const installs = Array.isArray(selectedDevice?.slot_installations) ? selectedDevice.slot_installations : [];
      if (copy) {
        const item = installs.find(value => String(value.install_id || "") === String(copy.dataset.copySlotInstall || ""));
        if (item?.install_command) await navigator.clipboard.writeText(item.install_command);
        result.textContent = "安装命令已复制";
      } else if (cancel) {
        const installId = String(cancel.dataset.cancelSlotInstall || "");
        if (!installId || !confirm("确定取消这条待执行 Worker 安装命令？")) return;
        try {
          await request(`/api/admin/linux-worker-installations/${encodeURIComponent(installId)}`, {method:"DELETE"});
          await refreshDevices(true);
          result.textContent = "待执行命令已取消，可重新生成此 Worker Slot。";
        } catch (error) { result.textContent = error.message; }
      }
    };
    document.getElementById("closeDeviceWorkersV122").onclick = () => dialog.close();
    dialog.addEventListener("cancel", event => { event.preventDefault(); dialog.close(); });

    globalThis[KEY] = Object.freeze({version:122, refreshDevices});
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once:true});
  else init();
})();
