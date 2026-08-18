(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const request = async (path, options = {}) => {
    const {headers = {}, ...rest} = options;
    const response = await fetch(path, {credentials:"same-origin",cache:"no-store",...rest,headers:{"Content-Type":"application/json",...headers}});
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

  const loginDialog = document.createElement("dialog");
  loginDialog.id = "linuxWorkerLoginDialog";
  loginDialog.style.cssText = "width:min(1320px,calc(100vw - 24px));max-width:none;border:1px solid #334155;border-radius:12px;background:#020617;color:#e5e7eb;padding:0;box-shadow:0 24px 90px rgba(0,0,0,.72)";
  loginDialog.innerHTML = `<div style="padding:14px"><div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px"><div><div style="font-size:18px;font-weight:700">远程登录 ChatGPT</div><div id="linuxLoginWorkerName" style="margin-top:3px;color:#94a3b8"></div></div><div style="display:flex;align-items:center;gap:10px"><span id="linuxLoginStatus" style="color:#94a3b8">准备中…</span><button class="action" id="closeLinuxLogin" type="button">结束登录</button></div></div><div style="font-size:12px;color:#94a3b8;margin-bottom:10px">这是服务器 Xvfb :99 中真实 Chrome 画面。请直接在画面里输入账号、密码、验证码或完成 CAPTCHA；chat2api 不保存这些输入内容。点击画面后即可键盘输入。</div><div id="linuxLoginViewport" style="position:relative;background:#000;border:1px solid #334155;border-radius:8px;overflow:hidden;min-height:360px;display:flex;align-items:center;justify-content:center"><img id="linuxLoginFrame" tabindex="0" draggable="false" alt="远程 Chrome 画面" style="display:block;max-width:100%;max-height:calc(100vh - 210px);outline:none;cursor:default;user-select:none"><div id="linuxLoginPlaceholder" style="position:absolute;color:#94a3b8">正在连接 Worker 画面…</div></div></div>`;
  document.body.appendChild(loginDialog);

  let selectedWorkerId = "";
  let loginWorkerId = "";
  let loginTicket = "";
  let loginSourceWidth = 1920;
  let loginSourceHeight = 1080;
  let loginFrameTimer = 0;
  let loginClosing = false;
  let inputChain = Promise.resolve();

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
      document.getElementById("linuxWorkerRows").innerHTML = payload.data.map(worker => `<tr><td>${esc(worker.name)}</td><td>${esc(worker.status)}</td><td>${esc(worker.os_version || "-")}</td><td>${esc(`${worker.platform || "linux"} ${worker.arch || ""}`)}</td><td>${esc(worker.network_status)}</td><td>${esc(proxyLabel(worker))}</td><td>${esc(worker.chatgpt_status)}</td><td>${esc(worker.chrome_bridge_version || "-")}</td><td>${esc(worker.last_seen_at || "-")}</td><td><button class="action" data-login="${esc(worker.worker_id)}" data-worker-name="${esc(worker.name)}">登录</button> <button class="action" data-proxy="${esc(worker.worker_id)}" data-worker-name="${esc(worker.name)}">代理</button> <button class="action danger" data-revoke="${esc(worker.worker_id)}">禁用</button></td></tr>`).join("") || '<tr><td colspan="10">暂无 Worker</td></tr>';
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

  const loginHeaders = () => ({"X-Chat2API-Login-Ticket": loginTicket});
  const clearLoginTimer = () => { if (loginFrameTimer) clearTimeout(loginFrameTimer); loginFrameTimer = 0; };
  const setLoginStatus = text => { document.getElementById("linuxLoginStatus").textContent = text; };

  const queueRemoteInput = payload => {
    if (!loginWorkerId || !loginTicket || loginClosing) return;
    inputChain = inputChain.then(() => request(`/api/admin/linux-workers/${encodeURIComponent(loginWorkerId)}/login-session/input`, {
      method:"POST", headers:loginHeaders(), body:JSON.stringify(payload),
    })).catch(error => setLoginStatus(error.message));
  };

  const fetchLoginFrame = async () => {
    clearLoginTimer();
    if (!loginWorkerId || !loginTicket || loginClosing || !loginDialog.open) return;
    try {
      const result = await request(`/api/admin/linux-workers/${encodeURIComponent(loginWorkerId)}/login-session/frame`, {headers:loginHeaders()});
      if (result.complete) {
        loginTicket = "";
        setLoginStatus("ChatGPT 已登录，远程会话已自动结束");
        document.getElementById("linuxLoginPlaceholder").textContent = "登录状态已确认，可以关闭此窗口。";
        document.getElementById("linuxLoginPlaceholder").style.display = "block";
        await load();
        return;
      }
      loginSourceWidth = Number(result.source_width || 1920);
      loginSourceHeight = Number(result.source_height || 1080);
      const image = document.getElementById("linuxLoginFrame");
      image.src = `data:${result.mime || "image/jpeg"};base64,${result.frame}`;
      document.getElementById("linuxLoginPlaceholder").style.display = "none";
      setLoginStatus("已连接 · 点击画面后可输入");
      loginFrameTimer = setTimeout(fetchLoginFrame, 850);
    } catch (error) {
      setLoginStatus(error.message);
      loginFrameTimer = setTimeout(fetchLoginFrame, 1800);
    }
  };

  const closeLoginDialog = async () => {
    if (loginClosing) return;
    loginClosing = true;
    clearLoginTimer();
    const workerId = loginWorkerId;
    const ticket = loginTicket;
    loginTicket = "";
    loginWorkerId = "";
    if (workerId && ticket) {
      try {
        await request(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/login-session`, {method:"DELETE",headers:{"X-Chat2API-Login-Ticket":ticket}});
      } catch (_) {}
    }
    document.getElementById("linuxLoginFrame").removeAttribute("src");
    document.getElementById("linuxLoginPlaceholder").style.display = "block";
    document.getElementById("linuxLoginPlaceholder").textContent = "远程登录会话已结束。";
    if (loginDialog.open) loginDialog.close();
    loginClosing = false;
  };

  const openLoginDialog = async (workerId, workerName) => {
    if (loginDialog.open) await closeLoginDialog();
    loginWorkerId = workerId;
    loginClosing = false;
    loginTicket = "";
    document.getElementById("linuxLoginWorkerName").textContent = workerName || workerId;
    document.getElementById("linuxLoginPlaceholder").style.display = "block";
    document.getElementById("linuxLoginPlaceholder").textContent = "正在创建安全登录会话…";
    setLoginStatus("连接中…");
    loginDialog.showModal();
    try {
      const result = await request(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/login-session`, {method:"POST",body:"{}"});
      loginTicket = result.ticket;
      loginSourceWidth = Number(result.source_width || 1920);
      loginSourceHeight = Number(result.source_height || 1080);
      setLoginStatus(`会话已建立 · 空闲 ${Math.round(Number(result.idle_timeout_seconds || 1200) / 60)} 分钟自动关闭`);
      fetchLoginFrame();
    } catch (error) {
      setLoginStatus(error.message);
      document.getElementById("linuxLoginPlaceholder").textContent = "无法打开远程登录会话。";
    }
  };

  const remotePoint = event => {
    const image = document.getElementById("linuxLoginFrame");
    const rect = image.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      x: Math.round((event.clientX - rect.left) / rect.width * loginSourceWidth),
      y: Math.round((event.clientY - rect.top) / rect.height * loginSourceHeight),
    };
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
    const payload = await request("/api/admin/linux-workers/enrollments", {method:"POST",body:JSON.stringify({name:document.getElementById("linuxWorkerName").value || "Linux Worker"})});
    const box = document.getElementById("linuxWorkerInstall");
    box.classList.remove("hidden");
    box.innerHTML = `<b>有效期至 ${esc(payload.expires_at)}</b><pre>${esc(payload.install_command)}</pre><button class="action">复制安装命令</button>`;
    box.querySelector("button").onclick = () => navigator.clipboard.writeText(payload.install_command);
  });

  document.getElementById("refreshLinuxWorkers").onclick = load;
  document.getElementById("closeLinuxProxy").onclick = closeProxyDialog;
  proxyDialog.addEventListener("cancel", event => { event.preventDefault(); closeProxyDialog(); });
  document.getElementById("closeLinuxLogin").onclick = closeLoginDialog;
  loginDialog.addEventListener("cancel", event => { event.preventDefault(); closeLoginDialog(); });

  const remoteImage = document.getElementById("linuxLoginFrame");
  remoteImage.addEventListener("click", event => {
    remoteImage.focus({preventScroll:true});
    const point = remotePoint(event); if (!point) return;
    queueRemoteInput({kind:"mouse",action:"click",button:event.button + 1,...point});
  });
  remoteImage.addEventListener("contextmenu", event => {
    event.preventDefault(); const point = remotePoint(event); if (!point) return;
    queueRemoteInput({kind:"mouse",action:"click",button:3,...point});
  });
  remoteImage.addEventListener("wheel", event => {
    event.preventDefault(); const point = remotePoint(event); if (!point) return;
    queueRemoteInput({kind:"mouse",action:"scroll",delta:Math.sign(event.deltaY) * Math.max(1,Math.min(5,Math.round(Math.abs(event.deltaY)/100)||1)),...point});
  }, {passive:false});
  remoteImage.addEventListener("keydown", event => {
    if (!loginTicket) return;
    const modifiers = [];
    if (event.ctrlKey) modifiers.push("ctrl"); if (event.altKey) modifiers.push("alt"); if (event.shiftKey) modifiers.push("shift"); if (event.metaKey) modifiers.push("super");
    const supportedSpecial = new Set(["Enter","Tab","Escape","Backspace","Delete","ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home","End","PageUp","PageDown"," "]);
    if (event.key.length !== 1 && !supportedSpecial.has(event.key)) return;
    event.preventDefault();
    queueRemoteInput({kind:"key",key:event.key,modifiers});
  });

  document.getElementById("applyLinuxProxy").onclick = async () => {
    if (!selectedWorkerId) return;
    const input = document.getElementById("linuxProxyShareLink");
    const resultNode = document.getElementById("linuxProxyResult");
    const shareLink = input.value.trim();
    if (!shareLink) { resultNode.textContent = "请先粘贴节点链接。"; return; }
    setProxyBusy(true); resultNode.textContent = "正在校验 Xray 配置、应用并测试 ChatGPT 连通性…";
    try {
      const result = await request(`/api/admin/linux-workers/${encodeURIComponent(selectedWorkerId)}/proxy`, {method:"POST",body:JSON.stringify({share_link:shareLink})});
      input.value = ""; const proxy = result.proxy || {};
      resultNode.textContent = `应用成功：${proxy.protocol || "proxy"}${proxy.server ? ` · ${proxy.server}:${proxy.port || ""}` : ""} · HTTP ${result.test?.http_status || "reachable"}`;
      await load();
    } catch (error) { resultNode.textContent = error.message; } finally { setProxyBusy(false); }
  };

  document.getElementById("testLinuxProxy").onclick = async () => {
    if (!selectedWorkerId) return;
    const resultNode = document.getElementById("linuxProxyResult"); setProxyBusy(true); resultNode.textContent = "正在通过当前 SOCKS 代理测试 ChatGPT…";
    try {
      const result = await request(`/api/admin/linux-workers/${encodeURIComponent(selectedWorkerId)}/proxy/test`, {method:"POST",body:"{}"});
      resultNode.textContent = result.ok ? `当前代理可达 · HTTP ${result.http_status || "reachable"}` : `当前代理不可达：${result.error || "test failed"}`;
    } catch (error) { resultNode.textContent = error.message; } finally { setProxyBusy(false); }
  };

  document.getElementById("linuxWorkerRows").onclick = async event => {
    const loginId = event.target.dataset.login;
    if (loginId) { openLoginDialog(loginId, event.target.dataset.workerName || loginId); return; }
    const proxyId = event.target.dataset.proxy;
    if (proxyId) { openProxyDialog(proxyId, event.target.dataset.workerName || proxyId); return; }
    const id = event.target.dataset.revoke;
    if (id && confirm("确定禁用此 Worker？")) { await request(`/api/admin/linux-workers/${encodeURIComponent(id)}`, {method:"DELETE"}); load(); }
  };

  setInterval(() => {
    if (section.classList.contains("active") && !proxyDialog.open && !loginDialog.open) load();
  }, 3000);
  if (location.hash === "#linux-workers") button.click();
})();
