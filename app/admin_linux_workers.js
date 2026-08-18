(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const request = async (path, options = {}) => {
    const response = await fetch(path, {credentials: "same-origin", cache: "no-store", headers: {"Content-Type": "application/json"}, ...options});
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
    return payload;
  };
  const nav = document.querySelector(".nav");
  const content = document.querySelector(".content");
  if (!nav || !content || document.getElementById("view-linux-workers")) return;

  const button = document.createElement("button");
  button.dataset.view = "linux-workers";
  button.textContent = "Linux Worker";
  nav.appendChild(button);

  const section = document.createElement("section");
  section.className = "view";
  section.id = "view-linux-workers";
  section.innerHTML = `<div class="panel"><div class="toolbar"><input id="linuxWorkerName" placeholder="Worker 名称"><button class="action good" id="createLinuxWorker">新增 Linux Worker</button><button class="action" id="refreshLinuxWorkers">刷新</button></div><div id="linuxWorkerInstall" class="secret hidden"></div><div class="scroll"><table><thead><tr><th>名称</th><th>状态</th><th>系统</th><th>平台</th><th>网络</th><th>代理</th><th>ChatGPT</th><th>Chrome Bridge</th><th>最后在线</th><th>操作</th></tr></thead><tbody id="linuxWorkerRows"></tbody></table></div></div>`;
  content.insertBefore(section, content.lastElementChild);

  const proxyDialog = document.createElement("dialog");
  proxyDialog.id = "linuxWorkerProxyDialog";
  proxyDialog.style.cssText = "width:min(720px,calc(100vw - 32px));border:1px solid #334155;border-radius:12px;background:#0f172a;color:#e5e7eb;padding:0;box-shadow:0 24px 80px rgba(0,0,0,.55)";
  proxyDialog.innerHTML = `<div style="padding:20px"><div style="display:flex;align-items:center;justify-content:space-between;gap:12px"><div><div style="font-size:18px;font-weight:700">配置 Worker 代理</div><div id="linuxProxyWorkerName" style="margin-top:4px;color:#94a3b8"></div></div><button class="action" id="closeLinuxProxy" type="button">关闭</button></div><div style="margin-top:16px;color:#94a3b8;line-height:1.6">支持 VLESS、VMess、Trojan、Shadowsocks 分享链接。节点凭据只通过已认证 Worker 通道发送到目标服务器，不保存到中心 Worker 记录。</div><textarea id="linuxProxyShareLink" autocomplete="off" spellcheck="false" placeholder="粘贴 vless://、vmess://、trojan:// 或 ss:// 节点链接" style="box-sizing:border-box;width:100%;min-height:110px;margin-top:14px;padding:12px;border:1px solid #334155;border-radius:8px;background:#020617;color:#e5e7eb;resize:vertical"></textarea><div id="linuxProxyResult" style="min-height:22px;margin-top:12px;color:#94a3b8"></div><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px"><button class="action" id="testLinuxProxy" type="button">测试当前代理</button><button class="action good" id="applyLinuxProxy" type="button">校验并应用</button></div></div>`;
  document.body.appendChild(proxyDialog);
  let selectedWorkerId = "";

  const proxyLabel = worker => {
    const summary = worker?.metadata?.proxy_summary || {};
    const base = String(worker.proxy_status || "-");
    if (!summary.protocol) return base;
    const endpoint = summary.server ? ` · ${summary.server}${summary.port ? `:${summary.port}` : ""}` : "";
    return `${base} · ${summary.protocol}${endpoint}`;
  };

  const load = async () => {
    try {
      const payload = await request("/api/admin/linux-workers");
      document.getElementById("linuxWorkerRows").innerHTML = payload.data.map(worker => `<tr><td>${esc(worker.name)}</td><td>${esc(worker.status)}</td><td>${esc(worker.os_version || "-")}</td><td>${esc(`${worker.platform || "linux"} ${worker.arch || ""}`)}</td><td>${esc(worker.network_status)}</td><td>${esc(proxyLabel(worker))}</td><td>${esc(worker.chatgpt_status)}</td><td>${esc(worker.chrome_bridge_version || "-")}</td><td>${esc(worker.last_seen_at || "-")}</td><td><button class="action" data-proxy="${esc(worker.worker_id)}" data-worker-name="${esc(worker.name)}">代理</button> <button class="action danger" data-revoke="${esc(worker.worker_id)}">禁用</button></td></tr>`).join("") || '<tr><td colspan="10">暂无 Worker</td></tr>';
    } catch (error) {
      document.getElementById("linuxWorkerRows").innerHTML = `<tr><td colspan="10">${esc(error.message)}</td></tr>`;
    }
  };

  const setProxyBusy = busy => {
    document.getElementById("applyLinuxProxy").disabled = busy;
    document.getElementById("testLinuxProxy").disabled = busy;
    document.getElementById("closeLinuxProxy").disabled = busy;
  };

  const closeProxyDialog = () => {
    document.getElementById("linuxProxyShareLink").value = "";
    document.getElementById("linuxProxyResult").textContent = "";
    selectedWorkerId = "";
    if (proxyDialog.open) proxyDialog.close();
  };

  const openProxyDialog = (workerId, workerName) => {
    selectedWorkerId = workerId;
    document.getElementById("linuxProxyWorkerName").textContent = workerName || workerId;
    document.getElementById("linuxProxyShareLink").value = "";
    document.getElementById("linuxProxyResult").textContent = "";
    proxyDialog.showModal();
    document.getElementById("linuxProxyShareLink").focus();
  };

  button.addEventListener("click", () => {
    document.querySelectorAll(".view").forEach(node => node.classList.remove("active"));
    document.querySelectorAll(".nav button").forEach(node => node.classList.toggle("active", node === button));
    section.classList.add("active");
    document.getElementById("pageTitle").textContent = "Linux Worker";
    location.hash = "linux-workers";
    load();
  });

  document.getElementById("createLinuxWorker").addEventListener("click", async () => {
    const payload = await request("/api/admin/linux-workers/enrollments", {method: "POST", body: JSON.stringify({name: document.getElementById("linuxWorkerName").value || "Linux Worker"})});
    const box = document.getElementById("linuxWorkerInstall");
    box.classList.remove("hidden");
    box.innerHTML = `<b>有效期至 ${esc(payload.expires_at)}</b><pre>${esc(payload.install_command)}</pre><button class="action">复制安装命令</button>`;
    box.querySelector("button").onclick = () => navigator.clipboard.writeText(payload.install_command);
  });

  document.getElementById("refreshLinuxWorkers").onclick = load;
  document.getElementById("closeLinuxProxy").onclick = closeProxyDialog;
  proxyDialog.addEventListener("cancel", event => { event.preventDefault(); closeProxyDialog(); });

  document.getElementById("applyLinuxProxy").onclick = async () => {
    if (!selectedWorkerId) return;
    const input = document.getElementById("linuxProxyShareLink");
    const resultNode = document.getElementById("linuxProxyResult");
    const shareLink = input.value.trim();
    if (!shareLink) { resultNode.textContent = "请先粘贴节点链接。"; return; }
    setProxyBusy(true);
    resultNode.textContent = "正在校验 Xray 配置、应用并测试 ChatGPT 连通性…";
    try {
      const result = await request(`/api/admin/linux-workers/${encodeURIComponent(selectedWorkerId)}/proxy`, {method: "POST", body: JSON.stringify({share_link: shareLink})});
      input.value = "";
      const proxy = result.proxy || {};
      resultNode.textContent = `应用成功：${proxy.protocol || "proxy"}${proxy.server ? ` · ${proxy.server}:${proxy.port || ""}` : ""} · HTTP ${result.test?.http_status || "reachable"}`;
      await load();
    } catch (error) {
      resultNode.textContent = error.message;
    } finally {
      setProxyBusy(false);
    }
  };

  document.getElementById("testLinuxProxy").onclick = async () => {
    if (!selectedWorkerId) return;
    const resultNode = document.getElementById("linuxProxyResult");
    setProxyBusy(true);
    resultNode.textContent = "正在通过当前 SOCKS 代理测试 ChatGPT…";
    try {
      const result = await request(`/api/admin/linux-workers/${encodeURIComponent(selectedWorkerId)}/proxy/test`, {method: "POST", body: "{}"});
      resultNode.textContent = result.ok ? `当前代理可达 · HTTP ${result.http_status || "reachable"}` : `当前代理不可达：${result.error || "test failed"}`;
    } catch (error) {
      resultNode.textContent = error.message;
    } finally {
      setProxyBusy(false);
    }
  };

  document.getElementById("linuxWorkerRows").onclick = async event => {
    const proxyId = event.target.dataset.proxy;
    if (proxyId) {
      openProxyDialog(proxyId, event.target.dataset.workerName || proxyId);
      return;
    }
    const id = event.target.dataset.revoke;
    if (id && confirm("确定禁用此 Worker？")) {
      await request(`/api/admin/linux-workers/${encodeURIComponent(id)}`, {method: "DELETE"});
      load();
    }
  };

  setInterval(() => {
    if (section.classList.contains("active") && !proxyDialog.open) load();
  }, 3000);
  if (location.hash === "#linux-workers") button.click();
})();
