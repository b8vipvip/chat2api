(() => {
  "use strict";
  const VERSION = "v156";
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
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const timeLabel = value => {
    if(!value)return "-";
    const date=new Date(value);
    return Number.isFinite(date.getTime())?date.toLocaleString("zh-CN",{hour12:false}):"-";
  };
  function makeWindowsDevicePanel() {
    const panel=document.createElement("div");
    panel.className="panel";
    panel.id="windowsDeviceListPanelV155";
    panel.innerHTML='<h3 style="margin:0 0 12px">设备列表</h3><div class="toolbar"><button type="button" class="action" id="refreshWindowsDevicesV155">刷新设备</button><button type="button" class="action good" id="newWindowsDeviceV155">新增设备码</button></div><div class="v153-muted" id="windowsDeviceSummaryV155"></div><div class="scroll"><table id="windowsDeviceTableV155"><thead><tr><th>设备名称</th><th>设备标识</th><th>状态</th><th>Worker数量</th><th>系统</th><th>网络</th><th>最后在线</th><th>操作</th></tr></thead><tbody id="windowsDeviceRowsV155"><tr><td colspan="8">正在获取设备信息…</td></tr></tbody></table></div>';
    return panel;
  }
  async function refreshWindowsDevices() {
    if(document.documentElement.dataset.chat2apiUnifiedDeviceConsole==="v160")return;
    const body=byId("windowsDeviceRowsV155");
    if(!body)return;
    const snapshot=globalThis.__chat2apiWindowsSnapshotV156;
    if(!snapshot && document.documentElement.dataset.chat2apiWorkerListReady!=="1") return;
    const data=snapshot && Date.now()-snapshot.at<15000 ? snapshot.data : await api("/api/admin/extensions");
    const clients=(Array.isArray(data.clients)?data.clients:[]).filter(c=>{
      const meta=c.metadata||{};
      // Worker-owned Linux Chrome profiles may not supply a legacy "platform"
      // field. Server-enriched linux_worker_id and real Chrome OS evidence win.
      // A Linux OS alone is not evidence of a managed Linux Worker: an
      // unpaired standalone Chrome Bridge must not disappear from both tabs.
      return !meta.linux_worker_id && !meta.controller_worker_id &&
        meta.linux_bridge_binding_conflict!==true;
    });
    const conflicts=(Array.isArray(data.clients)?data.clients:[])
      .filter(c=>c.metadata?.linux_bridge_binding_conflict===true).length;
    const codes=Array.isArray(data.pairing_codes)?data.pairing_codes:[];
    const byClient=new Map(clients.map(c=>[String(c.client_id||""),c]));
    const groups=new Map();
    const pairedClientIds=new Set();
    for(const code of codes){
      const client=byClient.get(String(code.bound_client_id||""));
      if(!client)continue;
      const id=String(code.bound_device_id||client.device_id||client.metadata?.device_id||client.client_id||"");
      if(!id)continue;
      if(!groups.has(id))groups.set(id,{id,name:code.name||client.name||id,clients:[],lastSeen:code.last_paired_at||"",paired:true});
      const group=groups.get(id);
      group.name=code.name||group.name;
      if(!group.clients.some(row=>row.client_id===client.client_id))group.clients.push(client);
      pairedClientIds.add(String(client.client_id||""));
    }
    for(const client of clients){
      if(pairedClientIds.has(String(client.client_id||"")))continue;
      const id=String(client.device_id||client.metadata?.device_id||client.client_id||"");
      if(!id)continue;
      if(!groups.has(id))groups.set(id,{id,name:client.device_name||client.name||id,clients:[],lastSeen:"",paired:false});
      const group=groups.get(id);
      if(!group.clients.some(row=>row.client_id===client.client_id))group.clients.push(client);
    }
    let online=0;
    body.innerHTML=[...groups.values()].map(group=>{
      const live=group.clients.filter(c=>c.online===true && c.connection_enabled!==false);
      if(live.length)online++;
      const net=live[0]?.metadata?.network_country_code || live[0]?.metadata?.network_probe_status ||
        group.clients[0]?.metadata?.network_country_code || group.clients[0]?.metadata?.network_probe_status || "-";
      const last=group.clients.map(c=>c.last_seen_at||"").filter(Boolean).sort().at(-1)||group.lastSeen;
      const worker=(live[0]||group.clients.find(c=>c.connection_enabled!==false)||group.clients[0])?.client_id||"";
      return `<tr><td><b>${esc(group.name)}</b></td><td><code>${esc(group.id)}</code></td><td><span class="v124-pill ${live.length?"good":"warn"}">${live.length?"在线":"离线"}</span></td><td>${group.clients.length}</td><td>Windows</td><td>${esc(net)}</td><td>${esc(timeLabel(last))}</td><td><button class="action" data-windows-device-worker="${esc(worker)}">查看Worker</button><button class="action" data-windows-device-update="${esc(worker)}">更新</button></td></tr>`;
    }).join("")||'<tr><td colspan="8" class="v153-muted">暂无已绑定的 Windows 设备，请先配对 Windows Worker。</td></tr>';
    byId("windowsDeviceSummaryV155").textContent=`Windows 设备：${groups.size} · 在线设备：${online} · Windows Worker：${clients.length}${conflicts?` · 绑定冲突：${conflicts}（请检查 Worker 诊断）`:""}`;
  }


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
    if (kind === "windows") refreshWindowsDevices().catch(()=>{});
  }

  function makeDialog() {
    if (byId("workerLoginSettingsV153")) return;
    const dialog = document.createElement("dialog");
    dialog.id = "workerLoginSettingsV153";
    dialog.style.cssText = "width:min(560px,calc(100vw - 24px));border:1px solid #334155;border-radius:14px;background:#0f172a;color:#e5e7eb;padding:0";
    dialog.innerHTML = '<form method="dialog" style="padding:20px;display:grid;gap:12px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between"><b style="font-size:18px">Worker 自动登录设置</b><button class="action" id="v153-close" type="button">关闭</button></div>' +
      '<div class="v153-muted">配置与 Worker ID 一一绑定；凭据在服务端加密保存，已启用自动登录时，可由已绑定 Worker 的扩展自动填写邮箱、密码与 TOTP；凭据仅通过受保护的连接发送，验证器密钥始终保留在服务端。若连接不安全或出现验证码挑战，将改为人工登录。首次绑定 Linux Chrome Bridge 仍需人工操作。</div>' +
      '<div><b>Worker ID</b><div id="v153-worker-id" style="overflow-wrap:anywhere"></div></div>' +
      '<label>ChatGPT 登录邮箱<input id="v153-email" type="email" autocomplete="off" placeholder="your@email.com" required></label>' +
      '<label>密码（留空表示保留原密码）<input id="v153-password" type="password" autocomplete="new-password" placeholder="ChatGPT 密码"></label>' +
      '<label>验证器密钥 Base32（留空保留原密钥）<input id="v153-totp" type="password" autocomplete="off" placeholder="TOTP setup secret"></label>' +
      '<label style="display:flex;align-items:center;gap:8px"><input id="v153-clear-totp" type="checkbox">清除已配置的验证器密钥</label>' +
      '<label style="display:flex;align-items:center;gap:8px"><input id="v153-enabled" type="checkbox" checked>自动检测登录失效并重新登录</label>' +
      '<div id="v153-credential-state" class="v153-muted"></div>' +
      '<div id="v153-result" aria-live="polite" class="v153-muted"></div>' +
      '<div class="v153-actions"><button id="v153-otp" type="button" class="action">生成验证码</button><button id="v153-delete" type="button" class="action danger">移除凭据</button><button id="v153-trigger" type="button" class="action">触发登录恢复</button><button id="v153-manual" type="button" class="action">人工接管</button><button id="v153-save" type="button" class="action good">保存</button></div>' +
      '</form>';
    document.body.appendChild(dialog);
    byId("v153-close").addEventListener("click", () => dialog.close());
    dialog.querySelector("form").addEventListener("submit", event => event.preventDefault());
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
    byId("v153-manual").addEventListener("click", async () => {
      const output = byId("v153-result");
      try {
        const data = await api("/api/admin/worker-login/" + encodeURIComponent(selectedWorker) + "/manual", {method:"POST"});
        output.textContent = data.queued ? "已停止自动填写并将登录窗口交给人工操作。" : "无法打开人工登录窗口。";
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
      setTimeout(boot, 350);
      return;
    }
    const style = document.createElement("style");
    style.textContent = "#windowsDeviceListPanelV155{margin-bottom:14px}#windowsDeviceTableV155{width:100%;min-width:970px;border-collapse:separate;border-spacing:0}#windowsDeviceTableV155 th,#windowsDeviceTableV155 td{padding:10px 11px;text-align:left;border-bottom:1px solid rgba(71,85,105,.32)}#windowsDeviceTableV155 th{white-space:nowrap;color:#a9b7ca;font-size:12px}.v153-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 14px}.v153-tabs button{min-width:150px}.v153-muted{color:#94a3b8;font-size:12px;line-height:1.6}.v153-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}#workerLoginSettingsV153 label{display:grid;gap:5px}#workerLoginSettingsV153 input:not([type=checkbox]){width:100%}#workerLoginSettingsV153 input[type=checkbox]{width:auto;min-width:auto}#workerGroup-linux[hidden],#workerGroup-windows[hidden]{display:none!important}#linuxDeviceTableV124{min-width:920px}#linuxDeviceTableV124 .v124-actions{min-width:0}";
    document.head.appendChild(style);
    const wrapper = document.createElement("div");
    wrapper.id = "unifiedWorkerTabsV153";
    wrapper.innerHTML = '<div class="v153-tabs" role="tablist" aria-label="Worker 平台">' +
      '<button class="action good" role="tab" aria-selected="true" id="workerGroupButton-windows" type="button">Windows Worker</button>' +
      '<button class="action" role="tab" aria-selected="false" id="workerGroupButton-linux" type="button">Linux Worker</button>' +
      '</div><div id="workerGroup-windows" role="tabpanel"></div><div id="workerGroup-linux" role="tabpanel" hidden></div>';
    section.insertBefore(wrapper, windowsPanel);
    const windowsDevicePanel=makeWindowsDevicePanel();
    byId("workerGroup-windows").appendChild(windowsDevicePanel);
    byId("workerGroup-windows").appendChild(windowsPanel);
    byId("workerGroup-linux").appendChild(linuxPanel);
    const linuxWorkers=byId("linuxWorkerListPanelV155");
    if(linuxWorkers)byId("workerGroup-linux").appendChild(linuxWorkers);
    byId("newWindowsDeviceV155").addEventListener("click",()=>{document.getElementById("pairingName")?.focus();document.getElementById("pairingName")?.scrollIntoView({behavior:"smooth",block:"center"});});
    byId("refreshWindowsDevicesV155").addEventListener("click",()=>{
      refreshWindowsDevices().catch(error=>{
        const target=byId("windowsDeviceSummaryV155");
        if(target)target.textContent="设备信息读取失败："+String(error.message||error);
      });
    });
    const linuxNav = [...document.querySelectorAll(".nav button")].find(node => node.dataset.view === "linux-workers");
    if (linuxNav) linuxNav.remove();
    const linuxSection = byId("view-linux-workers");
    if (linuxSection) linuxSection.classList.remove("active");
    byId("workerGroupButton-linux").addEventListener("click", () => setTab("linux"));
    byId("workerGroupButton-windows").addEventListener("click", () => setTab("windows"));
    document.addEventListener("click",event=>{
      const update=event.target?.closest?.("[data-windows-device-update]");
      if(update){if(!confirm("请求此设备上的 Chrome 扩展检查更新？开发者模式加载的扩展需要手动更新。"))return;api("/api/admin/extensions/"+encodeURIComponent(update.dataset.windowsDeviceUpdate)+"/update",{method:"POST"}).then(()=>alert("已请求扩展检查更新")).catch(e=>alert(e.message));return;}
      const link=event.target?.closest?.("[data-windows-device-worker]");
      if(!link)return;
      const id=link.dataset.windowsDeviceWorker||"";
      const row=[...document.querySelectorAll("#extensionDeviceBody tr[data-client-id]")].find(node=>node.dataset.clientId===id);
      row?.scrollIntoView({behavior:"smooth",block:"center"});
      if(row){row.style.outline="2px solid #38bdf8";setTimeout(()=>{row.style.outline="";},2000);}
    });
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
    document.addEventListener("chat2api:extensions-loaded", () => { if(selectedType==="windows")refreshWindowsDevices().catch(()=>{}); });
    const initial = location.hash === "#linux-workers" ? "linux" : "windows";
    if (location.hash === "#linux-workers") {
      const ext = document.querySelector('.nav button[data-view="extensions"]');
      ext?.click();
    }
    setTab(initial);
    // Windows device data is fetched lazily from the active tab.
    // Linux's original panel refresh was tied to its own navigation state.
    setInterval(() => {
      if(!document.hidden && selectedType === "windows" && section.classList.contains("active") && !document.querySelector("dialog[open]")){
        refreshWindowsDevices().catch(()=>{});
      }
      if (!document.hidden && selectedType === "linux" && section.classList.contains("active") && !document.querySelector("dialog[open]") && !document.querySelector("#linuxWorkerListPanelV155 [data-v121-limit-popover]:not([hidden])")) {
        byId("refreshLinuxDevicesV124")?.click();
      }
    }, 15000);
    document.documentElement.dataset.chat2apiUnifiedWorkerConsole = VERSION;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once:true});
  else boot();
})();


/* Unified equipment and Worker presentation v160.
 * Existing platform managers remain as hidden event/telemetry owners, not
 * competing visible tables. All edits use the established authenticated APIs.
 */
(() => {
  "use strict";
  const KEY="__CHAT2API_UNIFIED_DEVICE_CONSOLE_V160__";
  if(globalThis[KEY])return;
  const el=id=>document.getElementById(id);
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const text=value=>String(value??"").trim();
  const getLinux=()=>globalThis.__CHAT2API_LINUX_DEVICE_AUTHORITY_V124__?.devices||[];
  let data={clients:[],pairing_codes:[]};
  let refreshing=null;
  let displayedGroups=new Map();
  function aggregate(devices,payload){
    const groups=new Map(),byPair=new Map(),byClient=new Map(),used=new Set(),ownerByClient=new Map();
    for(const c of payload?.clients||[])if(text(c.client_id))byClient.set(text(c.client_id),c);
    const ensure=(key,defaults={})=>{
      if(!groups.has(key))groups.set(key,{key,id:"",linux:null,codes:[],clients:[],...defaults});
      return groups.get(key);
    };
    const attach=(group,clientId)=>{
      const id=text(clientId),client=byClient.get(id);
      if(!client || client.metadata?.linux_bridge_binding_conflict===true)return;
      if(used.has(id))return;
      group.clients.push(client);used.add(id);ownerByClient.set(id,group);
    };
    const workerById=new Map();
    for(const d of devices||[]){
      const id=text(d.device_id);if(!id)continue;
      const group=ensure("linux:"+id,{id,linux:d});
      const pairing=text(d.device_pairing_id);
      if(pairing)byPair.set(pairing,group);
      for(const w of d.workers||[]){
        const workerId=text(w.worker_id);if(workerId)workerById.set(workerId,group);
        attach(group,w.extension_client_id);
      }
    }
    for(const code of payload?.pairing_codes||[]){
      const pairing=text(code.pairing_id);
      if(!pairing)continue;
      const client=byClient.get(text(code.bound_client_id));
      const physical=text(code.bound_device_id||client?.device_id||client?.metadata?.device_id);
      const key=physical?"physical:"+physical:
        (text(code.bound_client_id)?"client:"+text(code.bound_client_id):"pairing:"+pairing);
      const group=byPair.get(pairing)||ownerByClient.get(text(code.bound_client_id))||ensure(key,{id:physical});
      if(!group.codes.some(row=>text(row.pairing_id)===pairing))group.codes.push(code);
      attach(group,code.bound_client_id);
    }
    for(const client of payload?.clients||[]){
      const id=text(client.client_id);if(!id||used.has(id))continue;
      const meta=client.metadata||{};
      const workerGroup=meta.linux_bridge_binding_conflict===true?null:workerById.get(text(meta.linux_worker_id));
      const physical=text(client.device_id||meta.device_id);
      const group=workerGroup||
        ensure(physical?"physical:"+physical:"client:"+id,{id:physical||id});
      attach(group,id);
      // A conflicting Bridge remains visible for diagnosis, never counted
      // as a second Linux Worker or silently attached to another device.
      if(meta.linux_bridge_binding_conflict===true && !group.clients.some(c=>c.client_id===id)){
        group.clients.push(client);used.add(id);
      }
    }
    return [...groups.values()];
  }
  function linuxOnline(worker){
    if(worker?.revoked_at||worker?.enabled===false)return false;
    const last=Date.parse(worker?.last_seen_at||"");
    return Number.isFinite(last)&&Date.now()-last>=0&&Date.now()-last<45000
      &&["ready","waiting_login","login_checking","waiting_proxy","proxy_checking"].includes(text(worker?.status).toLowerCase());
  }
  function systemOf(group){
    if(group.linux)return "Linux";
    for(const client of group.clients){
      const m=client.metadata||{};
      const p=text(m.platform||m.os||m.os_name||m.system||client.platform).toLowerCase();
      if(p.includes("linux"))return "Linux";
      if(p.includes("windows")||p==="win32")return "Windows";
      if(p.includes("mac"))return "macOS";
    }
    return group.clients.length?"未识别":"待识别";
  }
  function renderDevice(group){
    const codes=group.codes;
    const clients=group.clients;
    const workers=group.linux?.workers||[];
    const live=clients.some(c=>c.online===true&&c.connection_enabled!==false) ||
      workers.some(linuxOnline);
    const paired=codes.some(c=>c.pairing_status==="paired"||c.paired===true||c.bound_client_id);
    const disabled=codes.length>0&&codes.every(c=>c.enabled===false);
    const name=codes[0]?.name||group.linux?.device_name||clients[0]?.device_name||
      clients[0]?.name||group.id||"待命名设备";
    const status=live?"在线":disabled?"停用":
      group.linux?({"pending":"待安装","installing":"安装中","enrolling":"注册中","failed":"安装失败"}[group.linux.install_state]||"离线"):
      clients.length||paired?"离线":"待配对";
    const tone=live?"good":disabled?"bad":status==="离线"?"bad":"warn";
    const count=group.linux?workers.filter(w=>!w.revoked_at).length:clients.length;
    const os=systemOf(group);
    const primary=clients.find(c=>c.online===true&&c.connection_enabled!==false)||clients[0];
    const bridge=workers.find(w=>!w.revoked_at)?.metadata?.bridge||{};
    const net=primary?.metadata?.network_country_code||primary?.metadata?.network_probe_status||
      bridge.network_country_code||bridge.network_probe_status||"-";
    const last=[...clients.map(c=>c.last_seen_at),...workers.map(w=>w.last_seen_at)]
      .filter(Boolean).sort().at(-1)||group.linux?.install_updated_at;
    const lastDate=last&&Number.isFinite(Date.parse(last))?
      new Date(last).toLocaleString("zh-CN",{hour12:false}):"-";

    // The equipment row owns one set of controls regardless of the number
    // of underlying registration credentials. A multi-code action requires an
    // explicit choice; never silently mutate an arbitrary first credential.
    const oneCode=codes.length===1?codes[0]:null;
    const actionButton=(action,label,extraClass="")=>{
      const c=oneCode;
      const choice=c
        ? 'data-pairing-list-action="'+action+'" data-pairing-id="'+esc(c.pairing_id)+'"'+
          (action==="rename"?' data-pairing-name="'+esc(c.name||"")+'"':"")+
          (action==="toggle"?' data-enable="'+(c.enabled?"0":"1")+'"':"")
        : 'data-v161-device-code-action="'+action+'" data-v161-device-key="'+esc(group.key)+'"';
      return '<button type="button" class="action '+extraClass+'" '+choice+'>'+label+'</button>';
    };
    let actions="";
    if(codes.length){
      const mixed=codes.some(c=>c.enabled!==false)&&codes.some(c=>c.enabled===false);
      const toggleLabel=mixed?"启用/停用":codes.every(c=>c.enabled===false)?"启用":"停用";
      actions+=actionButton("rename","改名");
      actions+=actionButton("copy","复制");
      actions+=actionButton("toggle",toggleLabel);
    }
    if(group.linux){
      const id=esc(group.linux.device_id);
      actions+='<button type="button" class="action good" data-device-add-worker="'+id+'"'+
        (workers.some(w=>!w.revoked_at)?"":' disabled title="请先完成 Linux 设备安装"')+'>增加 Worker</button>'+
        '<button type="button" class="action" data-device-upgrade="'+id+'">更新</button>';
    }else if(primary?.client_id){
      actions+='<button type="button" class="action" data-windows-device-update="'+esc(primary.client_id)+'">更新</button>';
    }
    if(!group.linux&&!clients.length&&codes.length){
      actions+='<button type="button" class="action" data-open-linux-create-v160="1">Linux 安装</button>';
    }
    // Physical-device deletion applies to all equipment rows, including
    // unpaired and orphan Chrome Bridge records with no pairing credential.
    actions+='<button type="button" class="action danger" data-unified-delete-device="'+esc(group.key)+'">删除设备</button>';
    const physicalId=group.linux?.device_id||group.id||"";
    return '<tr data-v160-device-key="'+esc(group.key)+'"><td><b>'+esc(name)+'</b>'+
      (physicalId?'<div class="v153-muted">'+esc(physicalId)+'</div>':"")+'</td>'+
      '<td><span class="v124-pill '+tone+'">'+esc(status)+'</span></td>'+
      '<td>'+esc(os)+'</td><td>'+count+'</td>'+

      '<td>'+esc(net)+'</td><td>'+esc(lastDate)+'</td>'+
      '<td><div class="v160-actions">'+(actions||'<span class="v153-muted">未关联操作</span>')+'</div></td></tr>';
  }

  function choiceMarkup(group,action){
    if(!group||!Array.isArray(group.codes)||group.codes.length<2)return "";
    const labels={rename:"改名",copy:"复制",toggle:"启用/停用"};
    if(!Object.prototype.hasOwnProperty.call(labels,action))return "";
    return '<div class="dialogHead"><b>选择设备凭据 · '+labels[action]+'</b>'+
      '<button type="button" class="action" data-v161-close-code-choice="1">关闭</button></div>'+
      '<div class="dialogBody"><p class="muted">此设备关联多个凭据，请选择要操作的那一个。这里不会删除实体设备或 Worker。</p>'+
      '<div class="v161-choice-list">'+group.codes.map((c,index)=>{
        const id=esc(c.pairing_id);
        const label=action==="toggle"?(c.enabled===false?"启用":"停用"):labels[action];
        return '<button type="button" class="action" data-pairing-list-action="'+action+'" data-pairing-id="'+id+'"'+
          (action==="rename"?' data-pairing-name="'+esc(c.name||"")+'"':"")+
          (action==="toggle"?' data-enable="'+(c.enabled?"0":"1")+'"':"")+
          ' title="'+esc(c.prefix||"")+'">凭据 '+(index+1)+' · '+label+'</button>';
      }).join("")+'</div></div>';
  }

  function workerState(textValue,tone){
    return '<span class="v124-pill '+tone+'">'+esc(textValue)+'</span>';
  }
  // Never copy the hidden legacy status cell: multiple DOM owners may rewrite
  // it after refresh and regress the Worker table to 已启用/已禁用.
  function windowsWorkerState(client){
    if(!client)return workerState("未确认","warn");
    if(client.connection_enabled===false)return workerState("停用","warn");
    if(client.online===true){
      const used=Number(client.capacity?.used_units??client.active_api_calls??0);
      return client.busy===true||(Number.isFinite(used)&&used>0)?
        workerState("忙碌","warn"):workerState("运行正常","good");
    }
    return workerState("离线","bad");
  }
  function linuxWorkerState(worker,ext){
    if(!worker)return workerState("未确认","warn");
    if(worker.revoked_at)return workerState("已吊销","bad");
    if(worker.enabled===false)return workerState("停用","warn");
    const seen=Date.parse(worker.last_seen_at||"");
    if(Number.isFinite(seen)&&Date.now()-seen>45000)return workerState("离线","bad");
    const raw=text(worker.status).toLowerCase();
    if(raw==="ready"){
      if(!Number.isFinite(seen))return workerState("未确认","warn");
      const used=Number(ext?.capacity?.used_units??ext?.active_api_calls??0);
      return ext?.busy===true||(Number.isFinite(used)&&used>0)?
        workerState("忙碌","warn"):workerState("运行正常","good");
    }
    const labels={
      waiting_login:"待登录",login_checking:"检测登录",
      waiting_proxy:"待配置代理",proxy_checking:"检测代理",
      degraded:"运行异常",offline:"离线",error:"错误",enrolling:"注册中",
    };
    const label=labels[raw]||"未确认";
    return workerState(label,["离线","错误","运行异常"].includes(label)?"bad":"warn");
  }
  function renderWorkers(){
    const target=el("unifiedWorkerRowsV160");if(!target)return;
    const rows=[];
    const workerById=new Map(getLinux().flatMap(d=>(d.workers||[]).map(w=>[text(w.worker_id),w])));
    const clientsById=new Map((data.clients||[]).map(c=>[text(c.client_id),c]));
    const cell=(row,key)=>row.querySelector('td[data-chat2api-column-key="'+key+'"]')?.innerHTML||"-";
    for(const row of document.querySelectorAll("#extensionDeviceBody tr[data-client-id]")){
      const id=esc(row.dataset.clientId||"");
      const client=clientsById.get(text(row.dataset.clientId));
      const os=client?systemOf({linux:null,clients:[client]}):"未识别";
      rows.push('<tr data-unified-worker-id="'+id+'"><td>'+cell(row,"client_id")+'</td><td>'+cell(row,"device_name")+
        '</td><td>'+esc(os)+'</td><td>'+cell(row,"account_type")+'</td><td>'+windowsWorkerState(client)+
        '</td><td>'+cell(row,"occupancy")+'</td><td>'+cell(row,"worker_settings")+
        '</td><td>'+cell(row,"chatgpt")+'</td><td>'+cell(row,"actions")+'</td></tr>');
    }
    for(const row of document.querySelectorAll("#linuxWorkerRowsV155 tr[data-linux-worker-id]")){
      const t=row.cells;if(t.length<8)continue;
      const worker=workerById.get(text(row.dataset.linuxWorkerId));
      const ext=clientsById.get(text(worker?.extension_client_id));
      rows.push('<tr data-unified-worker-id="'+esc(row.dataset.linuxWorkerId)+'"><td><code title="'+esc('Linux Worker 管理绑定：'+text(row.dataset.linuxWorkerId||''))+'">'+esc(text(worker?.extension_client_id)||"—（扩展未绑定）")+'</code>'+
        '</td><td>'+t[5].innerHTML+'</td><td>Linux</td><td>'+t[1].innerHTML+
        '</td><td>'+linuxWorkerState(worker,ext)+'</td><td>'+t[3].innerHTML+'</td><td>'+t[4].innerHTML+
        '</td><td>'+t[6].innerHTML+'</td><td>'+t[7].innerHTML+'</td></tr>');
    }
    target.innerHTML=rows.join("")||'<tr><td colspan="9" class="v153-muted">暂无 Worker。设备安装或配对完成后会显示在这里。</td></tr>';
    const status=el("unifiedWorkerSummaryV160");
    if(status)status.textContent="Worker："+rows.length+"（包含 Windows 与 Linux）";
  }
  function render(){
    const snap=globalThis.__chat2apiWindowsSnapshotV156;
    if(snap?.data)data=snap.data;
    const groups=aggregate(getLinux(),data);
    displayedGroups=new Map(groups.map(group=>[group.key,group]));
    const body=el("unifiedDeviceRowsV160");
    if(body)body.innerHTML=groups.map(renderDevice).join("")||
      '<tr><td colspan="7" class="v153-muted">暂无设备。点击新增设备生成配对码。</td></tr>';
    const total=el("unifiedDeviceSummaryV160");
    if(total)total.textContent="设备："+groups.length+" · Linux："+groups.filter(g=>g.linux).length+
      " · 未配对："+groups.filter(g=>!g.clients.length&&!g.linux).length;
    renderWorkers();
  }
  async function refresh(){
    if(refreshing)return refreshing;
    const task=(async()=>{
      await Promise.allSettled([
        Promise.resolve(globalThis.chat2apiReloadCanonicalWorkerListV59?.()),
        Promise.resolve().then(()=>el("refreshLinuxDevicesV124")?.click()),
      ]);
      render();
    })();
    refreshing=task;
    try{await task;}finally{if(refreshing===task)refreshing=null;}
  }

  async function removeDevice(deviceKey){
    const previewRes=await fetch("/api/admin/unified-devices/delete-preview?key="+encodeURIComponent(deviceKey),{
      credentials:"same-origin",cache:"no-store",
    });
    const preview=await previewRes.json().catch(()=>({}));
    if(!previewRes.ok)throw new Error(preview.detail||"设备删除预检失败");
    const group=displayedGroups.get(deviceKey);
    const title=group?.codes?.[0]?.name||group?.linux?.device_name||group?.clients?.[0]?.device_name||deviceKey;
    const message="确定彻底删除设备「"+title+"」？\n"+
      "将清理："+Number(preview.clients||0)+" 个 Chrome Bridge、"+
      Number(preview.workers||0)+" 个 Linux Worker、"+
      Number(preview.pairing_codes||0)+" 个配对凭据、"+
      Number(preview.linux_installations||0)+" 条 Linux 安装记录。\n"+
      "已有连接将被断开，设备需要重新注册才能使用；已删除记录不可恢复。\n"+
      (group?.linux?"如 Linux 主机仍运行 Agent，请在主机上另外卸载。":"")+
      "\n确认删除整个设备？";
    if(!confirm(message))return;
    const result=await fetch("/api/admin/unified-devices",{
      method:"DELETE",credentials:"same-origin",cache:"no-store",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({device_key:deviceKey,confirm:"DELETE_DEVICE"}),
    });
    const response=await result.json().catch(()=>({}));
    if(!result.ok)throw new Error(response.detail||"删除设备失败");
    await refresh();
  }
  function boot(){
    const section=el("view-extensions"),legacy=el("unifiedWorkerTabsV153");
    const create=el("pairingName")?.closest(".panel");
    const pairing=el("pairingBody")?.closest(".panel");
    if(!section||!legacy||!create||!pairing||!el("linuxWorkerRowsV155")){
      setTimeout(boot,250);return;
    }
    if(el("unifiedDeviceConsoleV160"))return;
    const style=document.createElement("style");
    style.textContent="#unifiedDeviceConsoleV160 .v160-actions{display:flex;align-items:center;gap:6px;width:max-content;white-space:nowrap}"+
      "#unifiedDeviceConsoleV160 .v160-actions button{padding:5px 9px;white-space:nowrap;flex-shrink:0}"+
      "#unifiedDeviceConsoleV160 td:last-child{white-space:nowrap}"+
      "#unifiedDeviceConsoleV160 table{min-width:970px}"+
      "#unifiedDeviceConsoleV160 .v161-choice-list{display:flex;flex-wrap:wrap;gap:8px}"+
      "#unifiedDeviceConsoleV160 .panel{margin-bottom:14px}"+
      "#unifiedDeviceConsoleV160 .v160-creation{margin-top:12px}"+
      "#unifiedDeviceConsoleV160 [hidden]{display:none!important}";
    document.head.appendChild(style);
    const root=document.createElement("div");root.id="unifiedDeviceConsoleV160";
    root.innerHTML='<div class="panel"><h3>设备列表</h3><div class="toolbar">'+
      '<button type="button" class="action good" id="unifiedCreateDeviceV160">新增设备</button>'+
      '<button type="button" class="action" id="unifiedRefreshV160">刷新</button>'+
      '<button type="button" class="action" data-open-linux-create-v160="1">Linux 安装</button>'+
      '<button type="button" class="action" id="unifiedProxyV160">代理管理</button></div>'+
      '<div id="unifiedDeviceSummaryV160" class="v153-muted"></div>'+
      '<div class="scroll"><table id="unifiedDeviceTableV160"><thead><tr><th>设备名称 / 标识</th><th>状态</th><th>系统</th><th>Worker 数量</th><th>网络</th><th>最后在线</th><th>操作</th></tr></thead>'+
      '<tbody id="unifiedDeviceRowsV160"><tr><td colspan="7">正在加载…</td></tr></tbody></table></div>'+
      '<div class="v160-creation" id="unifiedCreationHostV160" hidden></div></div>'+
      '<div class="panel"><h3>Worker列表</h3><div class="v153-muted" id="unifiedWorkerSummaryV160"></div>'+
      '<div class="scroll"><table id="unifiedWorkerTableV160"><thead><tr><th>Worker ID</th><th>设备名称</th><th>系统</th><th>账户类型</th><th>状态</th><th>请求 / 备用窗口</th><th>并发 / 备用设置</th><th>ChatGPT</th><th>操作</th></tr></thead>'+
      '<tbody id="unifiedWorkerRowsV160"></tbody></table></div></div>';
    section.insertBefore(root,legacy);
    const chooser=document.createElement("dialog");
    chooser.id="v161DeviceCodeChooser";
    chooser.setAttribute("aria-label","选择要操作的设备凭据");
    root.appendChild(chooser);
    el("unifiedCreationHostV160").appendChild(create);
    create.style.marginTop="0";
    pairing.hidden=true;
    legacy.hidden=true;
    document.documentElement.dataset.chat2apiUnifiedDeviceConsole="v160";
    const header=()=>{if(section.classList.contains("active")&&el("pageTitle"))el("pageTitle").textContent="Worker管理";};
    el("unifiedCreateDeviceV160").addEventListener("click",()=>{
      const host=el("unifiedCreationHostV160");host.hidden=!host.hidden;
      if(!host.hidden)el("pairingName")?.focus();
    });
    el("unifiedRefreshV160").addEventListener("click",()=>refresh().catch(e=>{el("unifiedDeviceSummaryV160").textContent=e.message;}));
    el("unifiedProxyV160").addEventListener("click",()=>el("proxyManagerV124")?.click());
    root.addEventListener("click",event=>{
      const deletion=event.target.closest("[data-unified-delete-device]");
      if(deletion){
        event.preventDefault();
        const key=deletion.dataset.unifiedDeleteDevice;
        deletion.disabled=true;
        removeDevice(key).catch(e=>alert(e.message)).finally(()=>{deletion.disabled=false;});
        return;
      }
      const choice=event.target.closest("[data-v161-device-code-action]");
      if(choice){
        event.preventDefault();
        const group=displayedGroups.get(choice.dataset.v161DeviceKey||"");
        const markup=choiceMarkup(group,choice.dataset.v161DeviceCodeAction||"");
        if(!markup)return;
        chooser.innerHTML=markup;
        chooser.showModal();
        return;
      }
      if(event.target.closest("[data-v161-close-code-choice]")){
        event.preventDefault();
        if(chooser.open)chooser.close();
        return;
      }
      // The existing document-capture pairing handler performs the original
      // authenticated API action; this listener only closes the chooser.
      if(event.target.closest("#v161DeviceCodeChooser [data-pairing-list-action]")){
        if(chooser.open)chooser.close();
        return;
      }
      const install=event.target.closest("[data-open-linux-create-v160]");
      if(install){event.preventDefault();el("createLinuxDeviceV124")?.click();}
    });
    document.addEventListener("chat2api:extensions-loaded",render);
    document.addEventListener("chat2api:linux-devices-loaded",render);
    document.addEventListener("click",event=>{
      if(event.target.closest('.nav button[data-view="extensions"]'))setTimeout(()=>{header();render();},0);
    });
    render();header();
    refresh().catch(e=>{el("unifiedDeviceSummaryV160").textContent=e.message;});
    setInterval(()=>{
      if(!document.hidden&&section.classList.contains("active")&&!document.querySelector("dialog[open]")&&
        !document.querySelector("[data-v121-limit-popover]:not([hidden])")){
        el("refreshLinuxDevicesV124")?.click();
      }
    },15000);
  }
  globalThis[KEY]=Object.freeze({aggregate,deviceMarkup:renderDevice,choiceMarkup,windowsWorkerState,linuxWorkerState,render,refresh,version:"v162"});
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",boot,{once:true});
  else boot();
})();
