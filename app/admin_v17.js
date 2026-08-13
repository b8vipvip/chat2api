(() => {
  const VERSION = "0.17.0";
  const SESSION_MARKER = "__chat2api_admin_session__";
  let adminLoggedIn = false;

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url || "";
    const headersObject = init?.headers || {};
    const hasAuthorization = headersObject instanceof Headers
      ? headersObject.has("Authorization")
      : Object.keys(headersObject || {}).some(key => key.toLowerCase() === "authorization");
    if (!hasAuthorization && (url === "/v1/models" || url.endsWith("/v1/models"))) {
      return nativeFetch("/api/admin/models", {...init, credentials: "same-origin"});
    }
    return nativeFetch(input, {...init, credentials: init.credentials || "same-origin"});
  };

  // Replace the old administrator-key transport while preserving business API
  // calls made by the playground. SESSION_MARKER means cookie-authenticated admin.
  key = () => adminLoggedIn ? SESSION_MARKER : "";
  headers = (credential = key()) => {
    const value = String(credential || "");
    if (!value || value === SESSION_MARKER) return {};
    return {Authorization: "Bearer " + value};
  };
  api = async (path, opt = {}) => {
    const credential = opt.key === undefined ? key() : opt.key;
    const h = headers(credential);
    if (opt.body !== undefined) h["Content-Type"] = "application/json";
    const response = await nativeFetch(path, {
      method: opt.method || "GET",
      headers: h,
      body: opt.body === undefined ? undefined : JSON.stringify(opt.body),
      cache: "no-store",
      credentials: "same-origin",
    });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {detail: text}; }
    if (!response.ok) throw new Error(data.detail || `${response.status} ${text}`);
    return data;
  };

  const auth = document.querySelector(".auth");
  if (auth) {
    auth.innerHTML = `
      <span id="adminIdentity" class="muted" style="align-self:center;white-space:nowrap"></span>
      <button class="action" id="adminLogout" style="display:none">退出登录</button>
      <button class="action" id="refresh">刷新</button>`;
    document.getElementById("refresh").onclick = () => show((location.hash || "#overview").slice(1));
  }

  const gate = document.createElement("div");
  gate.id = "adminLoginGate";
  gate.style.cssText = "position:fixed;inset:0;z-index:100;background:rgba(5,10,18,.9);display:flex;align-items:center;justify-content:center;padding:20px";
  gate.innerHTML = `
    <div class="panel" style="width:min(440px,100%);padding:24px;box-shadow:0 28px 90px rgba(0,0,0,.5)">
      <div class="muted" style="font-size:12px;letter-spacing:.12em;text-transform:uppercase">chat2api administrator</div>
      <h2 style="margin:7px 0 5px">管理员登录</h2>
      <p class="muted" style="margin:0 0 16px">控制台使用独立管理员账号密码。业务 API Key 和 Chrome 扩展设备凭据与管理员登录完全分离。</p>
      <label class="muted">账号</label>
      <input id="adminUsername" autocomplete="username" style="width:100%;margin:5px 0 12px" value="admin">
      <label class="muted">密码</label>
      <input id="adminPassword" type="password" autocomplete="current-password" style="width:100%;margin:5px 0 14px">
      <button class="action good" id="adminLoginButton" style="width:100%">登录控制台</button>
      <div id="adminLoginError" class="bad" style="margin-top:10px"></div>
    </div>`;
  document.body.appendChild(gate);

  function setAuthenticated(value, username = "") {
    adminLoggedIn = Boolean(value);
    gate.style.display = adminLoggedIn ? "none" : "flex";
    const identity = document.getElementById("adminIdentity");
    if (identity) identity.textContent = adminLoggedIn ? `管理员 · ${username || "admin"}` : "";
    const logout = document.getElementById("adminLogout");
    if (logout) logout.style.display = adminLoggedIn ? "inline-block" : "none";
    if (!adminLoggedIn) status("请登录管理员账号", "muted");
  }

  async function checkSession() {
    try {
      const response = await nativeFetch("/api/admin/auth/session", {cache: "no-store", credentials: "same-origin"});
      const data = await response.json();
      setAuthenticated(Boolean(data.authenticated), data.username || "");
      if (data.authenticated) {
        await show((location.hash || "#overview").slice(1));
      }
    } catch (_) {
      setAuthenticated(false);
    }
  }

  document.getElementById("adminLoginButton").onclick = async () => {
    const errorBox = document.getElementById("adminLoginError");
    errorBox.textContent = "";
    try {
      const response = await nativeFetch("/api/admin/auth/login", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          username: document.getElementById("adminUsername").value.trim(),
          password: document.getElementById("adminPassword").value,
        }),
        credentials: "same-origin",
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "登录失败");
      document.getElementById("adminPassword").value = "";
      setAuthenticated(true, data.username || "admin");
      await show((location.hash || "#overview").slice(1));
    } catch (error) {
      errorBox.textContent = String(error?.message || error);
    }
  };
  document.getElementById("adminPassword").addEventListener("keydown", event => {
    if (event.key === "Enter") document.getElementById("adminLoginButton").click();
  });
  document.getElementById("adminLogout").onclick = async () => {
    await nativeFetch("/api/admin/auth/logout", {method: "POST", credentials: "same-origin"}).catch(() => {});
    setAuthenticated(false);
  };

  // Remove historical master-key wording from existing views.
  const keyFooter = document.querySelector("#view-keys .footer");
  if (keyFooter) keyFooter.textContent = "业务 API Key 的可复制副本使用服务端独立数据密钥加密保存；管理员密码变化不会影响业务 Key。";
  const testHint = document.querySelector(".testKeyHint");
  if (testHint) testHint.textContent = "留空时使用左侧选择的业务 API Key；管理员登录凭据不能用于 /v1 API 调用。完整 Key 不写入测试报告。";
  const playgroundIntro = document.querySelector("#view-playground .panel > .muted");
  if (playgroundIntro) playgroundIntro.textContent = "执行标准化用例并生成质量报告。测试必须使用业务 API Key；管理员账号密码仅用于控制台登录。";

  loadTestKeys = async () => {
    const current = document.getElementById("testKeySelect").value || "";
    const data = await api("/api/admin/keys");
    testKeyCatalog = (data.data || []).filter(item => item.managed !== false);
    const options = [];
    for (const item of testKeyCatalog) {
      const usable = item.enabled && !item.expired && !item.revoked_at && item.secret_recoverable;
      const stateText = item.revoked_at ? "已撤销" : item.expired ? "已过期" : !item.enabled ? "已停用" : !item.secret_recoverable ? "无法恢复" : "可用";
      options.push(`<option value="${esc(item.key_id)}" ${usable ? "" : "disabled"}>${esc(item.name)} · ${esc(item.prefix)} · ${esc(stateText)}</option>`);
    }
    if (!options.length) options.push('<option value="" disabled selected>请先在 API Key 页面创建业务 Key</option>');
    const select = document.getElementById("testKeySelect");
    select.innerHTML = options.join("");
    if ([...select.options].some(option => option.value === current && !option.disabled)) select.value = current;
  };

  resolveTestCredential = async () => {
    const pasted = document.getElementById("testKeyInput").value.trim();
    if (pasted) return {token: pasted, label: `手动粘贴 ${maskKey(pasted)}`, source: "pasted", key_id: null};
    const selected = document.getElementById("testKeySelect").value || "";
    if (!selected) throw new Error("请先创建或选择业务 API Key");
    const item = testKeyCatalog.find(row => row.key_id === selected);
    if (!item) throw new Error("所选测试 Key 不存在，请刷新后重试");
    if (!item.secret_recoverable) throw new Error("所选 Key 没有可恢复密文，请手动粘贴完整 Key");
    const data = await api(`/api/admin/keys/${encodeURIComponent(selected)}/secret`);
    return {token: data.token, label: `${item.name} · ${item.prefix}`, source: "managed", key_id: selected};
  };

  // Extension management view.
  const nav = document.querySelector(".nav");
  const content = document.querySelector(".content");
  if (nav && content && !document.querySelector('[data-view="extensions"]')) {
    const button = document.createElement("button");
    button.dataset.view = "extensions";
    button.textContent = "扩展管理";
    const modelButton = nav.querySelector('[data-view="models"]');
    nav.insertBefore(button, modelButton || nav.querySelector('[data-view="docs"]'));

    const view = document.createElement("section");
    view.className = "view";
    view.id = "view-extensions";
    view.innerHTML = `
      <div class="panel">
        <h2>扩展配对与设备管理</h2>
        <p class="muted">配对码只在首次绑定设备时使用。绑定成功后扩展保存自己的设备凭据，以后 Chrome/扩展上线会自动连接服务端，无需再次输入配对码。</p>
        <div class="toolbar">
          <input id="pairingName" placeholder="配对码名称，例如：办公室 Chrome">
          <button class="action good" id="createPairing">创建配对码</button>
        </div>
        <div id="pairingSecret" class="secret hidden"><b>新配对码（仅显示这一次）：</b> <code id="pairingCodeValue"></code> <button class="action" id="copyPairingCode">复制</button></div>
      </div>
      <div class="panel" style="margin-top:14px">
        <h3>配对码列表</h3>
        <div class="scroll"><table><thead><tr><th>名称</th><th>前缀</th><th>绑定设备</th><th>扩展</th><th>连接状态</th><th>最后连接</th><th>操作</th></tr></thead><tbody id="pairingBody"></tbody></table></div>
      </div>
      <div class="panel" style="margin-top:14px">
        <h3>绑定设备</h3>
        <div class="scroll"><table><thead><tr><th>扩展 ID</th><th>设备标识</th><th>版本</th><th>状态</th><th>绑定 API Key 数</th><th>最后在线</th><th>操作</th></tr></thead><tbody id="extensionDeviceBody"></tbody></table></div>
      </div>`;
    content.appendChild(view);
    titles.extensions = "扩展管理";
  }

  function connectionPill(row) {
    if (!row.connection_enabled) return pill("disabled");
    if (row.online && row.busy) return pill("busy");
    if (row.online) return pill("online");
    return pill("offline");
  }

  async function loadExtensions() {
    if (!adminLoggedIn) return;
    try {
      const data = await api("/api/admin/extensions");
      const pairingBody = document.getElementById("pairingBody");
      pairingBody.innerHTML = (data.pairing_codes || []).map(row => {
        const client = row.client || {};
        const state = row.connection_status || "unbound";
        return `<tr>
          <td>${esc(row.name)}</td><td><code>${esc(row.prefix)}</code></td>
          <td><code>${esc(row.bound_device_id || "未绑定")}</code></td>
          <td><code>${esc(row.bound_client_id || "-")}</code></td>
          <td>${pill(state)}</td><td>${fmtTime(client.last_seen_at)}</td>
          <td><button class="action" onclick="togglePairing('${esc(row.pairing_id)}',${!row.enabled})">${row.enabled ? "停用配对码" : "启用配对码"}</button></td>
        </tr>`;
      }).join("") || '<tr><td colspan="7">暂无配对码，请先创建。</td></tr>';

      const deviceBody = document.getElementById("extensionDeviceBody");
      deviceBody.innerHTML = (data.clients || []).map(row => `<tr>
        <td><code>${esc(row.client_id)}</code></td>
        <td><code>${esc(row.device_id || row.metadata?.device_id || "旧版设备未上报")}</code></td>
        <td>${esc(row.version || "-")}</td><td>${connectionPill(row)}</td>
        <td>${esc(row.bound_api_keys ?? 0)}</td><td>${fmtTime(row.last_seen_at)}</td>
        <td>${row.connection_enabled
          ? `<button class="action danger" onclick="disconnectExtension('${esc(row.client_id)}')">断开连接</button>`
          : `<button class="action good" onclick="enableExtension('${esc(row.client_id)}')">允许连接</button>`}</td>
      </tr>`).join("") || '<tr><td colspan="7">暂无绑定扩展。</td></tr>';
    } catch (error) {
      status("扩展管理加载失败：" + error.message, "bad");
    }
  }

  document.getElementById("createPairing").onclick = async () => {
    try {
      const name = document.getElementById("pairingName").value.trim() || "Chrome 扩展";
      const data = await api("/api/admin/pairing-codes", {method: "POST", body: {name}});
      document.getElementById("pairingCodeValue").textContent = data.code;
      document.getElementById("pairingSecret").classList.remove("hidden");
      await loadExtensions();
    } catch (error) { status(error.message, "bad"); }
  };
  document.getElementById("copyPairingCode").onclick = () => navigator.clipboard.writeText(document.getElementById("pairingCodeValue").textContent || "");

  window.togglePairing = async (pairingId, enabled) => {
    try {
      await api(`/api/admin/pairing-codes/${encodeURIComponent(pairingId)}`, {method: "PATCH", body: {enabled}});
      await loadExtensions();
    } catch (error) { status(error.message, "bad"); }
  };
  window.disconnectExtension = async clientId => {
    if (!confirm("断开该扩展并禁止它自动接入？之后可点击“允许连接”恢复。")) return;
    try {
      await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/disconnect`, {method: "POST"});
      await loadExtensions();
    } catch (error) { status(error.message, "bad"); }
  };
  window.enableExtension = async clientId => {
    try {
      await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/enable`, {method: "POST"});
      await loadExtensions();
    } catch (error) { status(error.message, "bad"); }
  };

  const baseShow = show;
  show = async v => {
    if (!adminLoggedIn) { setAuthenticated(false); return; }
    baseShow(v);
    if (v === "extensions") await loadExtensions();
  };
  document.querySelectorAll(".nav button").forEach(button => button.onclick = () => show(button.dataset.view));

  checkSession();
})();
