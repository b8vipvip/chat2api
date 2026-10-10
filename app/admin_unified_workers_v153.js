(() => {
  "use strict";
  const VERSION = "v153";
  let selectedWorker = "";
  let selectedType = "windows";
  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/json"},
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "HTTP " + response.status);
    return payload;
  };
  const byId = id => document.getElementById(id);

  function setTab(kind) {
    selectedType = kind;
    for (const tab of ["windows", "linux"]) {
      const active = tab === kind;
      const node = byId("workerGroup-" + tab);
      const button = byId("workerGroupButton-" + tab);
      if (node) node.hidden = !active;
      if (button) {
        button.setAttribute("aria-selected", String(active));
        button.classList.toggle("good", active);
      }
    }
    const title = byId("pageTitle");
    if (title) title.textContent = "Worker管理 · " + (kind === "linux" ? "Linux Worker" : "Windows Worker");
    if (kind === "linux") byId("refreshLinuxDevicesV124")?.click();
  }

  function makeDialog() {
    if (byId("workerLoginSettingsV153")) return;
    const dialog = document.createElement("dialog");
    dialog.id = "workerLoginSettingsV153";
    dialog.style.cssText = "width:min(560px,calc(100vw - 24px));border:1px solid #334155;border-radius:14px;background:#0f172a;color:#e5e7eb;padding:0";
    dialog.innerHTML = '<form method="dialog" style="padding:20px;display:grid;gap:12px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between"><b style="font-size:18px">Worker 自动登录设置</b><button class="action" value="cancel" type="submit">关闭</button></div>' +
      '<div class="v153-muted">配置与 Worker ID 一一绑定；凭据在服务端加密保存，已启用自动登录时，可由已绑定 Worker 的扩展自动填写邮箱、密码与 TOTP；凭据仅通过受保护的连接发送，验证器密钥始终保留在服务端。若连接不安全或出现验证码挑战，将改为人工登录。首次绑定 Linux Chrome Bridge 仍需人工操作。</div>' +
      '<div><b>Worker ID</b><div id="v153-worker-id" style="overflow-wrap:anywhere"></div></div>' +
      '<label>ChatGPT 登录邮箱<input id="v153-email" type="email" autocomplete="off" placeholder="your@email.com" required></label>' +
      '<label>密码（留空表示保留原密码）<input id="v153-password" type="password" autocomplete="new-password" placeholder="ChatGPT 密码"></label>' +
      '<label>验证器密钥 Base32（留空保留原密钥）<input id="v153-totp" type="password" autocomplete="off" placeholder="TOTP setup secret"></label>' +
      '<label style="display:flex;align-items:center;gap:8px"><input id="v153-clear-totp" type="checkbox">清除已配置的验证器密钥</label>' +
      '<label style="display:flex;align-items:center;gap:8px"><input id="v153-enabled" type="checkbox" checked>自动检测登录失效并重新登录</label>' +
      '<div id="v153-credential-state" class="v153-muted"></div>' +
      '<div id="v153-result" aria-live="polite" class="v153-muted"></div>' +
      '<div class="v153-actions"><button id="v153-otp" type="button" class="action">生成验证码</button><button id="v153-delete" type="button" class="action danger">移除凭据</button><button id="v153-trigger" type="button" class="action">触发登录恢复</button><button id="v153-save" type="button" class="action good">保存</button></div>' +
      '</form>';
    document.body.appendChild(dialog);
    byId("v153-save").addEventListener("click", async () => {
      const output = byId("v153-result");
      try {
        const body = {username: byId("v153-email").value.trim(), enabled: byId("v153-enabled").checked};
        const password = byId("v153-password").value;
        const totp = byId("v153-totp").value;
        if (password) body.password = password;
        if (totp || byId("v153-clear-totp").checked) body.totp_secret = totp;
        await api("/api/admin/worker-login/" + encodeURIComponent(selectedWorker), {method:"PUT", body:JSON.stringify(body)});
        byId("v153-password").value = "";
        byId("v153-totp").value = "";
        byId("v153-clear-totp").checked = false;
        output.textContent = "已保存加密凭据。";
        await loadProfile();
      } catch (err) { output.textContent = err.message; }
    });
    byId("v153-otp").addEventListener("click", async () => {
      try {
        const data = await api("/api/admin/worker-login/" + encodeURIComponent(selectedWorker) + "/totp");
        byId("v153-result").textContent = "当前 TOTP 验证码：" + data.code + "（约 " + data.valid_for_seconds + " 秒有效）";
      } catch (err) { byId("v153-result").textContent = err.message; }
    });
    byId("v153-delete").addEventListener("click", async () => {
      if (!confirm("确定删除此 Worker 的登录凭据？")) return;
      try {
        await api("/api/admin/worker-login/" + encodeURIComponent(selectedWorker), {method:"DELETE"});
        byId("v153-password").value = "";
        byId("v153-totp").value = "";
        byId("v153-result").textContent = "凭据已删除。";
        await loadProfile();
      } catch (err) { byId("v153-result").textContent = err.message; }
    });
    byId("v153-trigger").addEventListener("click", async () => {
      const output = byId("v153-result");
      try {
        const data = await api("/api/admin/worker-login/" + encodeURIComponent(selectedWorker) + "/trigger", {method:"POST"});
        output.textContent = data.queued ? (data.status === "automating" ? "已发起自动登录，等待 Worker 验证结果。" : "连接不符合自动传输条件，已请求打开登录窗口供人工操作。") : (data.reason === "in_progress" ? "该 Worker 已有登录恢复任务进行中。" : "自动恢复处于冷却期。");
      } catch (err) { output.textContent = err.message; }
    });
    dialog.addEventListener("close", () => {
      selectedWorker = "";
      byId("v153-email").value = "";
      byId("v153-password").value = "";
      byId("v153-totp").value = "";
    });
  }

  const stateNames = {
    idle: "未执行", opening: "正在打开登录窗口", automating: "正在自动登录",
    waiting_otp: "正在获取 TOTP", manual_required: "需要人工登录",
    logged_in: "已验证登录成功", timeout: "恢复超时", failed: "恢复失败",
    offline: "Worker 离线", paused: "失败次数过多，已暂停自动恢复"
  };
  function updateProfileState(state) {
    byId("v153-credential-state").textContent =
      "配置：" + (state.configured ? "已保存" : "未设置") +
      " · 密码：" + (state.has_password ? "已保存" : "未设置") +
      " · TOTP：" + (state.has_totp ? "已保存" : "未设置") +
      " · 状态：" + (stateNames[state.runtime] || state.runtime || "未知") +
      " · 近30分钟失败：" + (state.recent_failures || 0) + "/3";
  }

  async function loadProfile() {
    if (!selectedWorker) return;
    const workerId = selectedWorker;
    const state = await api("/api/admin/worker-login/" + encodeURIComponent(workerId));
    if (selectedWorker !== workerId) return;
    byId("v153-worker-id").textContent = state.worker_id;
    byId("v153-email").value = state.username || "";
    byId("v153-enabled").checked = state.configured ? Boolean(state.enabled) : true;
    updateProfileState(state);
  }

  function boot() {
    if (byId("unifiedWorkerTabsV153")) return;
    const section = byId("view-extensions");
    const linuxTable = byId("linuxDeviceTableV124");
    const windowsRows = byId("extensionDeviceBody");
    const linuxPanel = linuxTable?.closest(".panel");
    const windowsPanel = windowsRows?.closest(".panel");
    if (!section || !linuxPanel || !windowsPanel) {
      setTimeout(boot, 150);
      return;
    }
    const style = document.createElement("style");
    style.textContent = ".v153-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 14px}.v153-tabs button{min-width:150px}.v153-muted{color:#94a3b8;font-size:12px;line-height:1.6}.v153-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}#workerLoginSettingsV153 label{display:grid;gap:5px}#workerLoginSettingsV153 input:not([type=checkbox]){width:100%}#workerLoginSettingsV153 input[type=checkbox]{width:auto;min-width:auto}#workerGroup-linux[hidden],#workerGroup-windows[hidden]{display:none!important}#linuxDeviceTableV124{min-width:1450px}#linuxDeviceTableV124 .v124-actions{min-width:0}";
    document.head.appendChild(style);
    const wrapper = document.createElement("div");
    wrapper.id = "unifiedWorkerTabsV153";
    wrapper.innerHTML = '<div class="v153-tabs" role="tablist" aria-label="Worker 平台">' +
      '<button class="action good" role="tab" aria-selected="true" id="workerGroupButton-windows" type="button">Windows Worker</button>' +
      '<button class="action" role="tab" aria-selected="false" id="workerGroupButton-linux" type="button">Linux Worker</button>' +
      '</div><div id="workerGroup-windows" role="tabpanel"></div><div id="workerGroup-linux" role="tabpanel" hidden></div>';
    section.insertBefore(wrapper, windowsPanel);
    byId("workerGroup-windows").appendChild(windowsPanel);
    byId("workerGroup-linux").appendChild(linuxPanel);
    const linuxNav = [...document.querySelectorAll(".nav button")].find(node => node.dataset.view === "linux-workers");
    if (linuxNav) linuxNav.remove();
    const linuxSection = byId("view-linux-workers");
    if (linuxSection) linuxSection.classList.remove("active");
    byId("workerGroupButton-linux").addEventListener("click", () => setTab("linux"));
    byId("workerGroupButton-windows").addEventListener("click", () => setTab("windows"));
    document.addEventListener("click", event => {
      const edit = event.target?.closest?.("[data-worker-login-edit]");
      if (edit) {
        event.preventDefault();
        selectedWorker = edit.dataset.workerLoginEdit;
        makeDialog();
        byId("v153-result").textContent = "";
        byId("workerLoginSettingsV153").showModal();
        loadProfile().catch(err => { byId("v153-result").textContent = err.message; });
        return;
      }
      const view = event.target?.closest?.('.nav button[data-view="extensions"]');
      if (view) setTimeout(() => setTab(selectedType), 0);
    }, true);
    makeDialog();
    setInterval(async () => {
      const dialog = byId("workerLoginSettingsV153");
      const workerId = selectedWorker;
      if (!dialog?.open || !workerId) return;
      try {
        const state = await api("/api/admin/worker-login/" + encodeURIComponent(workerId));
        if (dialog.open && selectedWorker === workerId) updateProfileState(state);
      } catch (_) { /* Remain on the last verified state until the next poll. */ }
    }, 3000);
    const initial = location.hash === "#linux-workers" ? "linux" : "windows";
    if (location.hash === "#linux-workers") {
      const ext = document.querySelector('.nav button[data-view="extensions"]');
      ext?.click();
    }
    setTab(initial);
    // Linux's original panel refresh was tied to its own navigation state.
    setInterval(() => {
      if (selectedType === "linux" && section.classList.contains("active") && !document.querySelector("dialog[open]")) {
        byId("refreshLinuxDevicesV124")?.click();
      }
    }, 5000);
    document.documentElement.dataset.chat2apiUnifiedWorkerConsole = VERSION;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once:true});
  else boot();
})();
