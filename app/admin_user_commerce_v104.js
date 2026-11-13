(() => {
  "use strict";
  const nav = document.querySelector(".nav");
  const content = document.querySelector(".content");
  if (!nav || !content || document.getElementById("view-user-pricing")) return;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[ch]);
  let adminRoleResolved = false;
  let adminRoleRefreshGeneration = 0;
  const keyHeader = () => {
    // Never send a possibly stale root key until the current Session role is
    // known. A delegated Session must not inherit a prior administrator key.
    if (!adminRoleResolved || (currentAdminRole && currentAdminRole !== "super_admin")) return {};
    const value = String(document.getElementById("adminKey")?.value || "").trim();
    return value ? {"x-api-key": value} : {};
  };
  async function adminApi(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: {"Content-Type":"application/json", ...keyHeader(), ...(options.headers || {})},
    });
    let payload = {};
    try { payload = await response.json(); } catch {}
    if (!response.ok) throw new Error(payload.detail || payload?.error?.message || `HTTP ${response.status}`);
    return payload;
  }

  let currentAdminRole = null;
  let sessionAuthenticated = false;
  const canWriteUsers = () => ["super_admin", "user_admin"].includes(currentAdminRole);
  const canReadAudit = () => currentAdminRole === "super_admin";
  function applyAdminRole() {
    // Expose the resolved, non-secret role for diagnostics and deterministic
    // UI readiness checks; server middleware remains the authorization gate.
    document.documentElement.dataset.adminCommerceRole = currentAdminRole || "";
    document.documentElement.dataset.adminCommerceSessionReady = adminRoleResolved ? "true" : "false";
    pricingButton.hidden = !["super_admin", "billing_admin"].includes(currentAdminRole);
    paymentButton.hidden = !["super_admin", "billing_admin"].includes(currentAdminRole);
    usersButton.hidden = !["super_admin", "user_admin", "system_admin", "support"].includes(currentAdminRole);
    const auditPanel = document.getElementById("adminAuditPanel");
    if (auditPanel) auditPanel.hidden = !canReadAudit();
    const canWritePaymentSecrets = currentAdminRole === "super_admin";
    document.querySelectorAll("#view-user-payments input, #view-user-payments select, #view-user-payments textarea").forEach((node) => {
      node.disabled = !canWritePaymentSecrets;
    });
    ["commerceSavePayment", "commerceTestPayment"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.disabled = !canWritePaymentSecrets;
    });
  }

  async function refreshAdminRole() {
    const generation = ++adminRoleRefreshGeneration;
    let sessionCheckSucceeded = false;
    try {
      const response = await fetch("/api/admin/auth/session", {credentials:"same-origin", cache:"no-store"});
      if (!response.ok) throw new Error("Admin session lookup failed");
      const session = await response.json();
      if (generation !== adminRoleRefreshGeneration) return;
      sessionAuthenticated = !!session.authenticated;
      currentAdminRole = sessionAuthenticated ? (session.role || "unknown") : null;
      sessionCheckSucceeded = true;
    } catch (_) {
      if (generation !== adminRoleRefreshGeneration) return;
      sessionAuthenticated = false;
      currentAdminRole = null;
    }
    if (sessionCheckSucceeded && !sessionAuthenticated && !currentAdminRole && String(document.getElementById("adminKey")?.value || "").trim()) {
      currentAdminRole = "super_admin";
    }
    adminRoleResolved = sessionCheckSucceeded;
    applyAdminRole();
  }

  const pricingButton = document.createElement("button");
  pricingButton.dataset.view = "user-pricing";
  pricingButton.textContent = "价格配置";
  nav.appendChild(pricingButton);
  const paymentButton = document.createElement("button");
  paymentButton.dataset.view = "user-payments";
  paymentButton.textContent = "支付配置";
  nav.appendChild(paymentButton);
  const usersButton = document.createElement("button");
  usersButton.dataset.view = "admin-users";
  usersButton.textContent = "用户管理";
  nav.appendChild(usersButton);

  content.insertAdjacentHTML("beforeend", `
    <section class="view" id="view-user-pricing">
      <div class="panel">
        <h2>用户模型价格配置</h2>
        <div class="muted">用户端模型广场与费用统计使用这里的价格。默认值参考公开 API Token 价格；保存后以本页配置为准。</div>
        <div class="toolbar" style="margin-top:14px">
          <label><span class="muted">启用用户计费</span><br><select id="commerceBillingEnabled"><option value="false">关闭（仅统计）</option><option value="true">启用（余额扣费）</option></select></label>
          <label><span class="muted">USD → CNY 计费换算率</span><br><input id="commerceFx" type="number" min="0.0001" max="100" step="0.0001"></label>
          <button class="action" id="commerceAddModel">新增模型</button>
          <button class="action good" id="commerceSavePricing">保存价格</button>
        </div>
        <div class="scroll"><table><thead><tr><th>启用</th><th>模型 ID</th><th>显示名称</th><th>输入 / 1M</th><th>缓存输入 / 1M</th><th>输出 / 1M</th><th>操作</th></tr></thead><tbody id="commercePricingBody"></tbody></table></div>
        <div id="commercePricingStatus" class="muted" style="margin-top:10px"></div>
      </div>
    </section>
    <section class="view" id="view-user-payments">
      <div class="panel">
        <h2>用户端支付配置</h2>
        <div class="muted">当前接入 ZPAY，用于用户费用中心的支付宝 / 微信充值。商户密钥加密保存，读取配置时不会返回明文。受 G0 DataKeyring 迁移门禁约束：Billing Admin 仅可查看，旧版支付密钥及其配置修改暂时仅允许 root Super Admin。</div>
        <div class="panels" style="margin-top:14px">
          <div class="panel">
            <h3>基础配置</h3>
            <div style="display:grid;gap:10px">
              <label><span class="muted">支付总开关</span><br><select id="commercePayEnabled"><option value="false">关闭</option><option value="true">启用</option></select></label>
              <label><span class="muted">ZPAY 商户 ID (PID)</span><br><input id="commercePid" autocomplete="off"></label>
              <label><span class="muted">ZPAY 商户密钥</span><br><input id="commercePayKey" type="password" autocomplete="new-password" placeholder="留空表示保持现有密钥"></label>
              <label><span class="muted">公网 Origin（用于支付回调）</span><br><input id="commerceOrigin" placeholder="https://api.example.com"></label>
            </div>
          </div>
          <div class="panel">
            <h3>支付渠道</h3>
            <div style="display:grid;gap:10px">
              <label><span class="muted">支付宝</span><br><select id="commerceAlipayEnabled"><option value="true">启用</option><option value="false">关闭</option></select></label>
              <label><span class="muted">支付宝渠道 ID（可选）</span><br><input id="commerceAlipayCid" placeholder="多个 ID 使用英文逗号分隔"></label>
              <label><span class="muted">微信支付</span><br><select id="commerceWechatEnabled"><option value="true">启用</option><option value="false">关闭</option></select></label>
              <label><span class="muted">微信渠道 ID（可选）</span><br><input id="commerceWechatCid" placeholder="多个 ID 使用英文逗号分隔"></label>
            </div>
          </div>
        </div>
        <div class="toolbar" style="margin-top:14px"><button class="action good" id="commerceSavePayment">保存支付配置</button><button class="action" id="commerceTestPayment">测试 API 连接</button><span id="commercePaymentStatus" class="muted"></span></div>
      </div>
    </section>
    <section class="view" id="view-admin-users">
      <div class="panel">
        <h2>用户管理</h2>
        <p class="muted">查看注册用户、账户状态、名下 API Key 和在线会话。停用用户会撤销其全部用户控制台会话，并阻止其名下 API Key 继续调用；重新启用不会恢复已撤销的会话。</p>
        <div class="toolbar" style="margin-top:14px">
          <input id="adminUserSearch" placeholder="搜索邮箱、昵称或用户 ID" style="min-width:260px">
          <button class="action" id="adminUserSearchButton">搜索</button>
          <button class="action" id="adminUserReload">刷新</button>
          <button class="action" id="adminUserPrev">上一页</button>
          <span id="adminUserPage" class="muted">-</span>
          <button class="action" id="adminUserNext">下一页</button>
          <span id="adminUserStatus" class="muted"></span>
        </div>
        <div class="scroll"><table>
          <thead><tr><th>邮箱</th><th>昵称</th><th>用户 ID</th><th>状态</th><th>注册时间</th><th>API Key</th><th>会话</th><th>余额</th><th>操作</th></tr></thead>
          <tbody id="adminUsersBody"><tr><td colspan="9" class="muted">打开用户管理后加载</td></tr></tbody>
        </table></div>
      </div>
      <div class="panel" id="adminUserKeysPanel" style="display:none;margin-top:14px">
        <div class="toolbar"><h3 id="adminUserKeysTitle" style="margin:0;flex:1">用户 API Key</h3><button class="action" id="adminUserKeysClose">关闭</button></div>
        <div class="scroll"><table>
          <thead><tr><th>名称</th><th>前缀</th><th>状态</th><th>权限</th><th>创建时间</th><th>最后使用</th><th>Token 额度</th><th>管理操作</th></tr></thead>
          <tbody id="adminUserKeysBody"></tbody>
        </table></div>
        <p class="muted">此处只显示 Key 元数据，不显示或恢复完整密钥。</p>
      </div>
      <div class="panel" id="adminAuditPanel" style="margin-top:14px">
        <div class="toolbar"><h3 style="margin:0;flex:1">最近管理审计</h3><button class="action" id="adminAuditReload">刷新审计</button></div>
        <div class="scroll"><table>
          <thead><tr><th>时间</th><th>操作</th><th>操作者</th><th>目标</th><th>详情</th><th>事件哈希</th></tr></thead>
          <tbody id="adminAuditBody"><tr><td colspan="6" class="muted">加载用户管理后读取</td></tr></tbody>
        </table></div>
      </div>
    </section>`);

  function activate(view) {
    const allowed = {
      "user-pricing": ["super_admin", "billing_admin"],
      "user-payments": ["super_admin", "billing_admin"],
      "admin-users": ["super_admin", "user_admin", "system_admin", "support"]
    };
    if (allowed[view] && !allowed[view].includes(currentAdminRole)) return;
    document.querySelectorAll(".nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
    const title = document.getElementById("pageTitle");
    const titles = {"admin-users":"用户管理","user-pricing":"价格配置","user-payments":"支付配置"};
    if (title) title.textContent = titles[view] || view;
    if (view === "admin-users") loadUsers().catch(showUserError);
    if (view === "user-pricing") loadPricing().catch(showPricingError);
    if (view === "user-payments") loadPayments().catch(showPaymentError);
  }
  usersButton.addEventListener("click", (event) => { event.stopPropagation(); activate("admin-users"); });
  pricingButton.addEventListener("click", (event) => { event.stopPropagation(); activate("user-pricing"); });
  paymentButton.addEventListener("click", (event) => { event.stopPropagation(); activate("user-payments"); });

  let pricingRows = [];
  function renderPricing() {
    const body = document.getElementById("commercePricingBody");
    body.innerHTML = pricingRows.map((row, index) => `<tr data-price-index="${index}">
      <td><input data-field="enabled" type="checkbox" ${row.enabled ? "checked" : ""}></td>
      <td><input data-field="model_id" value="${esc(row.model_id)}" style="min-width:180px"></td>
      <td><input data-field="name" value="${esc(row.name || row.model_id)}" style="min-width:160px"></td>
      <td><input data-field="input_usd_per_million" type="number" min="0" step="0.001" value="${esc(row.input_usd_per_million)}"></td>
      <td><input data-field="cached_input_usd_per_million" type="number" min="0" step="0.001" value="${esc(row.cached_input_usd_per_million)}"></td>
      <td><input data-field="output_usd_per_million" type="number" min="0" step="0.001" value="${esc(row.output_usd_per_million)}"></td>
      <td><button class="action danger" data-remove-price="${index}">删除</button></td>
    </tr>`).join("");
    body.querySelectorAll("[data-remove-price]").forEach((button) => button.onclick = () => { pricingRows.splice(Number(button.dataset.removePrice), 1); renderPricing(); });
  }
  function collectPricingRows() {
    return [...document.querySelectorAll("#commercePricingBody tr")].map((tr) => {
      const field = (name) => tr.querySelector(`[data-field="${name}"]`);
      return {
        enabled: field("enabled").checked,
        model_id: field("model_id").value.trim(),
        name: field("name").value.trim(),
        input_usd_per_million: Number(field("input_usd_per_million").value || 0),
        cached_input_usd_per_million: Number(field("cached_input_usd_per_million").value || 0),
        output_usd_per_million: Number(field("output_usd_per_million").value || 0),
      };
    });
  }
  let adminUserRows = [];
  let activeAdminUserId = "";
  let adminUserOffset = 0;
  const adminUserLimit = 100;
  let adminUserTotal = 0;
  async function loadUsers() {
    const status = document.getElementById("adminUserStatus");
    status.textContent = "正在加载用户…";
    const query = document.getElementById("adminUserSearch").value.trim();
    const params = new URLSearchParams({limit:String(adminUserLimit),offset:String(adminUserOffset)});
    if (query) params.set("q", query);
    const payload = await adminApi("/api/admin/users?" + params.toString());
    adminUserRows = payload.data || [];
    adminUserTotal = Number(payload.total || 0);
    renderUsers();
    status.textContent = "共 " + adminUserTotal + " 个用户";
    if (canReadAudit()) await loadAudit();
  }
  async function loadAudit() {
    const payload = await adminApi("/api/admin/audit?limit=50");
    const body = document.getElementById("adminAuditBody");
    body.innerHTML = (payload.data || []).map((event) => {
      const target = String(event.target_type || "") + ":" + String(event.target_id || "");
      return '<tr><td>' + esc(event.timestamp || '-') + '</td>' +
        '<td>' + esc(event.action || '-') + '</td>' +
        '<td>' + esc(event.actor || '-') + '</td>' +
        '<td><code>' + esc(target) + '</code></td>' +
        '<td>' + esc(JSON.stringify(event.details || {})) + '</td>' +
        '<td><code>' + esc(String(event.event_hash || "").slice(0, 16)) + '</code></td></tr>';
    }).join("") || '<tr><td colspan="6" class="muted">暂无审计事件</td></tr>';
  }
  function renderUsers() {
    const body = document.getElementById("adminUsersBody");
    if (!adminUserRows.length) {
      body.innerHTML = '<tr><td colspan="9" class="muted">暂无匹配用户</td></tr>';
      document.getElementById("adminUserPage").textContent = "0-0 / " + adminUserTotal;
      document.getElementById("adminUserPrev").disabled = true;
      document.getElementById("adminUserNext").disabled = true;
      return;
    }
    body.innerHTML = adminUserRows.map((user) => {
      const enabled = Boolean(user.enabled);
      const balance = user.balance_cents == null ? "—" : "¥" + (Number(user.balance_cents) / 100).toFixed(2);
      const writeActions = canWriteUsers()
        ? '<button class="action ' + (enabled ? 'danger' : 'good') + '" data-user-action="toggle" data-user-id="' + esc(user.user_id) + '" data-enabled="' + (enabled ? 'false' : 'true') + '">' + (enabled ? '停用' : '启用') + '</button>' +
          '<button class="action" data-user-action="revoke" data-user-id="' + esc(user.user_id) + '">撤销会话</button>'
        : '';
      return '<tr>' +
        '<td>' + esc(user.email) + '</td>' +
        '<td>' + esc(user.display_name) + '</td>' +
        '<td><code>' + esc(user.user_id) + '</code></td>' +
        '<td><span class="pill ' + (enabled ? 'ok' : 'bad') + '">' + (enabled ? '正常' : '已停用') + '</span></td>' +
        '<td>' + esc(user.created_at || '-') + '</td>' +
        '<td>' + Number(user.key_count || 0) + '</td>' +
        '<td>' + Number(user.active_sessions || 0) + '</td>' +
        '<td>' + balance + '</td>' +
        '<td><div class="rowactions">' + writeActions +
        '<button class="action" data-user-action="keys" data-user-id="' + esc(user.user_id) + '">查看 Key</button>' +
        '</div></td></tr>';
    }).join("");
    const page = document.getElementById("adminUserPage");
    const first = adminUserTotal ? adminUserOffset + 1 : 0;
    const last = Math.min(adminUserOffset + adminUserRows.length, adminUserTotal);
    page.textContent = first + "-" + last + " / " + adminUserTotal;
    document.getElementById("adminUserPrev").disabled = adminUserOffset <= 0;
    document.getElementById("adminUserNext").disabled = adminUserOffset + adminUserLimit >= adminUserTotal;
  }
  function showUserError(error) {
    document.getElementById("adminUserStatus").textContent = "错误：" + error.message;
  }
  async function loadUserKeys(userId) {
    activeAdminUserId = String(userId || "");
    const panel = document.getElementById("adminUserKeysPanel");
    const user = adminUserRows.find((row) => row.user_id === activeAdminUserId);
    document.getElementById("adminUserKeysTitle").textContent = "用户 API Key · " + (user?.email || activeAdminUserId);
    const payload = await adminApi("/api/admin/users/" + encodeURIComponent(activeAdminUserId) + "/keys");
    const body = document.getElementById("adminUserKeysBody");
    body.innerHTML = (payload.data || []).map((key) => {
      const configuredEnabled = Boolean(key.configured_enabled);
      const state = key.revoked_at ? "已永久撤销" : key.expired ? "已过期" : key.quota_exhausted ? "额度耗尽" : key.enabled ? "正常" : "已停用";
      const cannotEnable = !configuredEnabled && (key.expired || key.quota_exhausted);
      const actions = key.revoked_at
        ? '<span class="muted">不可操作</span>'
        : !canWriteUsers()
          ? '<span class="muted">只读</span>'
          : '<div class="rowactions">' +
            '<button class="action ' + (configuredEnabled ? 'danger' : 'good') + '" data-admin-key-action="toggle" data-key-id="' + esc(key.key_id) + '" data-enabled="' + (configuredEnabled ? 'false' : 'true') + '"' + (cannotEnable ? ' disabled title="Key 已过期或额度耗尽"' : '') + '>' + (configuredEnabled ? '停用' : '启用') + '</button>' +
            '<button class="action" data-admin-key-action="quota" data-key-id="' + esc(key.key_id) + '" data-current-quota="' + (key.token_quota == null ? '' : String(Number(key.token_quota))) + '">设置额度</button>' +
            '<button class="action danger" data-admin-key-action="revoke" data-key-id="' + esc(key.key_id) + '">永久撤销</button>' +
            '</div>';
      return '<tr><td>' + esc(key.name) + '</td>' +
        '<td><code>' + esc(key.prefix) + '</code></td>' +
        '<td>' + esc(state) + '</td>' +
        '<td>' + esc((key.scopes || []).join(", ")) + '</td>' +
        '<td>' + esc(key.created_at || '-') + '</td>' +
        '<td>' + esc(key.last_used_at || '-') + '</td>' +
        '<td>' + (key.token_quota == null ? "不限" : Number(key.tokens_used || 0) + " / " + Number(key.token_quota || 0)) + '</td>' +
        '<td>' + actions + '</td></tr>';
    }).join("") || '<tr><td colspan="8" class="muted">该用户没有业务 API Key</td></tr>';
    panel.style.display = "block";
  }
  document.getElementById("adminUserKeysBody").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-admin-key-action]");
    if (!button || !activeAdminUserId) return;
    const action = button.dataset.adminKeyAction;
    const keyId = button.dataset.keyId;
    const status = document.getElementById("adminUserStatus");
    const url = "/api/admin/users/" + encodeURIComponent(activeAdminUserId) + "/keys/" + encodeURIComponent(keyId);
    button.disabled = true;
    try {
      if (action === "toggle") {
        const enabled = button.dataset.enabled === "true";
        const message = enabled
          ? "确定重新启用此 API Key？"
          : "确定停用此 API Key？正在使用它的客户端将无法继续调用。";
        if (!window.confirm(message)) return;
        await adminApi(url, {method:"PATCH", body:JSON.stringify({enabled})});
        status.textContent = enabled ? "API Key 已启用" : "API Key 已停用";
      } else if (action === "quota") {
        const raw = window.prompt("设置 Token 总额度；留空表示不限额度。", button.dataset.currentQuota || "");
        if (raw === null) return;
        const value = raw.trim();
        if (value && !/^\d+$/.test(value)) throw new Error("额度必须是非负整数，或留空表示不限");
        const quota = value ? Number(value) : null;
        if (quota !== null && (!Number.isSafeInteger(quota) || quota < 0)) throw new Error("额度超出安全整数范围");
        await adminApi(url, {method:"PATCH", body:JSON.stringify({token_quota:quota})});
        status.textContent = quota === null ? "API Key 已设为不限额度" : "API Key 额度已更新为 " + quota;
      } else if (action === "revoke") {
        if (!window.confirm("永久撤销此 API Key？此操作不可恢复，使用该 Key 的客户端将立即失去访问权限。")) return;
        await adminApi(url, {method:"DELETE"});
        status.textContent = "API Key 已永久撤销";
      } else {
        return;
      }
      await loadUserKeys(activeAdminUserId);
      await loadUsers();
    } catch (error) {
      status.textContent = "API Key 操作失败：" + error.message;
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById("adminUsersBody").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-user-action]");
    if (!button) return;
    const userId = button.dataset.userId;
    const action = button.dataset.userAction;
    const status = document.getElementById("adminUserStatus");
    button.disabled = true;
    try {
      if (action === "keys") {
        await loadUserKeys(userId);
      } else if (action === "toggle") {
        const enabled = button.dataset.enabled === "true";
        const prompt = enabled
          ? "确定重新启用该用户？已撤销的会话不会恢复。"
          : "确定停用该用户？其所有控制台会话将被撤销，名下 API Key 将立即无法调用。";
        if (!window.confirm(prompt)) return;
        const result = await adminApi("/api/admin/users/" + encodeURIComponent(userId), {
          method: "PATCH",
          body: JSON.stringify({enabled}),
        });
        status.textContent = enabled ? "用户已启用" : "用户已停用；撤销会话 " + Number(result.revoked_sessions || 0) + " 个";
        await loadUsers();
      } else if (action === "revoke") {
        if (!window.confirm("确定撤销该用户的全部控制台会话？")) return;
        const result = await adminApi("/api/admin/users/" + encodeURIComponent(userId) + "/sessions/revoke", {
          method: "POST",
          body: "{}",
        });
        status.textContent = "已撤销 " + Number(result.revoked_sessions || 0) + " 个会话";
        await loadUsers();
      }
    } catch (error) {
      status.textContent = "操作失败：" + error.message;
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById("adminUserSearchButton").addEventListener("click", () => {
    adminUserOffset = 0;
    loadUsers().catch(showUserError);
  });
  document.getElementById("adminUserReload").addEventListener("click", () => loadUsers().catch(showUserError));
  document.getElementById("adminUserPrev").addEventListener("click", () => {
    adminUserOffset = Math.max(0, adminUserOffset - adminUserLimit);
    loadUsers().catch(showUserError);
  });
  document.getElementById("adminUserNext").addEventListener("click", () => {
    if (adminUserOffset + adminUserLimit < adminUserTotal) {
      adminUserOffset += adminUserLimit;
      loadUsers().catch(showUserError);
    }
  });
  document.getElementById("adminUserSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      adminUserOffset = 0;
      loadUsers().catch(showUserError);
    }
  });
  document.getElementById("adminUserKeysClose").addEventListener("click", () => {
    document.getElementById("adminUserKeysPanel").style.display = "none";
  });
  document.getElementById("adminAuditReload").addEventListener("click", () => {
    if (canReadAudit()) loadAudit().catch(showUserError);
  });

  async function loadPricing() {
    const payload = await adminApi("/api/admin/user-pricing");
    document.getElementById("commerceBillingEnabled").value = String(Boolean(payload.billing_enabled));
    document.getElementById("commerceFx").value = Number(payload.usd_cny_rate || 0);
    pricingRows = (payload.models || []).map((row) => ({...row}));
    renderPricing();
    document.getElementById("commercePricingStatus").textContent = `已加载 ${pricingRows.length} 个模型 · ${payload.updated_at || ""}`;
  }
  async function savePricing() {
    const payload = await adminApi("/api/admin/user-pricing", {method:"PUT", body:JSON.stringify({
      billing_enabled: document.getElementById("commerceBillingEnabled").value === "true",
      usd_cny_rate: Number(document.getElementById("commerceFx").value || 0),
      models: collectPricingRows(),
    })});
    pricingRows = payload.models || [];
    renderPricing();
    document.getElementById("commercePricingStatus").textContent = `保存成功 · ${payload.updated_at || ""}`;
  }
  function showPricingError(error) { document.getElementById("commercePricingStatus").textContent = `错误：${error.message}`; }

  async function loadPayments() {
    const payload = await adminApi("/api/admin/user-payments");
    document.getElementById("commercePayEnabled").value = String(Boolean(payload.enabled));
    document.getElementById("commercePid").value = payload.pid || "";
    document.getElementById("commercePayKey").value = "";
    document.getElementById("commercePayKey").placeholder = payload.key_configured ? "已保存密钥；留空表示保持不变" : "请输入 ZPAY 商户密钥";
    document.getElementById("commerceOrigin").value = payload.public_origin || "";
    document.getElementById("commerceAlipayEnabled").value = String(Boolean(payload.alipay_enabled));
    document.getElementById("commerceWechatEnabled").value = String(Boolean(payload.wechat_enabled));
    document.getElementById("commerceAlipayCid").value = payload.alipay_cid || "";
    document.getElementById("commerceWechatCid").value = payload.wechat_cid || "";
    document.getElementById("commercePaymentStatus").textContent = payload.configured ? `ZPAY 已配置 · PID ${payload.pid_hint || ""}` : "ZPAY 尚未配置完整";
  }
  async function savePayments() {
    const body = {
      enabled: document.getElementById("commercePayEnabled").value === "true",
      pid: document.getElementById("commercePid").value.trim(),
      key: document.getElementById("commercePayKey").value || null,
      alipay_enabled: document.getElementById("commerceAlipayEnabled").value === "true",
      wechat_enabled: document.getElementById("commerceWechatEnabled").value === "true",
      alipay_cid: document.getElementById("commerceAlipayCid").value.trim(),
      wechat_cid: document.getElementById("commerceWechatCid").value.trim(),
      public_origin: document.getElementById("commerceOrigin").value.trim(),
    };
    const payload = await adminApi("/api/admin/user-payments", {method:"PUT", body:JSON.stringify(body)});
    document.getElementById("commercePayKey").value = "";
    document.getElementById("commercePayKey").placeholder = payload.key_configured ? "已保存密钥；留空表示保持不变" : "请输入 ZPAY 商户密钥";
    document.getElementById("commercePaymentStatus").textContent = `保存成功 · ${payload.configured ? "商户凭据已配置" : "商户凭据未完整"}`;
  }
  async function testPayments() {
    const status = document.getElementById("commercePaymentStatus");
    status.textContent = "正在测试 ZPAY API…";
    const payload = await adminApi("/api/admin/user-payments/test", {method:"POST", body:"{}"});
    status.textContent = `测试成功 · ${payload.credential_status === "accepted" ? "凭据已接受" : "接口可达"}${payload.message ? ` · ${payload.message}` : ""}`;
  }
  function showPaymentError(error) { document.getElementById("commercePaymentStatus").textContent = `错误：${error.message}`; }

  document.getElementById("commerceAddModel").onclick = () => { pricingRows.push({model_id:"",name:"",enabled:true,input_usd_per_million:0,cached_input_usd_per_million:0,output_usd_per_million:0}); renderPricing(); };
  document.getElementById("commerceSavePricing").onclick = () => savePricing().catch(showPricingError);
  document.getElementById("commerceSavePayment").onclick = () => savePayments().catch(showPaymentError);
  document.getElementById("commerceTestPayment").onclick = () => testPayments().catch(showPaymentError);
  function activateRoleDefault() {
    const initial = location.hash.replace(/^#/, "");
    const allowed = {
      "user-pricing": ["super_admin", "billing_admin"],
      "user-payments": ["super_admin", "billing_admin"],
      "admin-users": ["super_admin", "user_admin", "system_admin", "support"]
    };
    if (allowed[initial] && allowed[initial].includes(currentAdminRole)) {
      activate(initial);
    } else if (currentAdminRole === "user_admin") {
      location.hash = "admin-users";
      activate("admin-users");
    } else if (currentAdminRole === "billing_admin") {
      location.hash = "user-pricing";
      activate("user-pricing");
    }
  }
  // The C-track session is authoritative. Consume its event detail instead
  // of issuing a second racing session request with potentially stale role data.
  window.addEventListener("chat2api-admin-session-changed", (event) => {
    adminRoleRefreshGeneration += 1;
    const session = event.detail || {};
    sessionAuthenticated = !!session.authenticated;
    currentAdminRole = sessionAuthenticated ? (session.role || "unknown") : null;
    adminRoleResolved = true;
    applyAdminRole();
    activateRoleDefault();
  });
  refreshAdminRole().then(activateRoleDefault);
})();
