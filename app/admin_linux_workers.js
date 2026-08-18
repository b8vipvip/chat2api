(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const request = async (path, options = {}) => {
    const response = await fetch(path, {credentials: "same-origin", cache: "no-store", headers: {"Content-Type": "application/json"}, ...options});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
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
  const load = async () => {
    try {
      const payload = await request("/api/admin/linux-workers");
      document.getElementById("linuxWorkerRows").innerHTML = payload.data.map(worker => `<tr><td>${esc(worker.name)}</td><td>${esc(worker.status)}</td><td>${esc(worker.os_version || "-")}</td><td>${esc(`${worker.platform || "linux"} ${worker.arch || ""}`)}</td><td>${esc(worker.network_status)}</td><td>${esc(worker.proxy_status)}</td><td>${esc(worker.chatgpt_status)}</td><td>${esc(worker.chrome_bridge_version || "-")}</td><td>${esc(worker.last_seen_at || "-")}</td><td><button class="action danger" data-revoke="${esc(worker.worker_id)}">禁用</button></td></tr>`).join("") || '<tr><td colspan="10">暂无 Worker</td></tr>';
    } catch (error) { document.getElementById("linuxWorkerRows").innerHTML = `<tr><td colspan="10">${esc(error.message)}</td></tr>`; }
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
  document.getElementById("linuxWorkerRows").onclick = async event => {
    const id = event.target.dataset.revoke;
    if (id && confirm("确定禁用此 Worker？")) { await request(`/api/admin/linux-workers/${encodeURIComponent(id)}`, {method: "DELETE"}); load(); }
  };
  if (location.hash === "#linux-workers") button.click();
})();
