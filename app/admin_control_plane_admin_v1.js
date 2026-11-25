/* C-track management views for the single app/admin.py console owner. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const C_ROLES = {
    "control-admins": ["super_admin"],
    "client-policy": ["super_admin", "system_admin"],
    "client-logs": ["super_admin", "system_admin", "support"],
    "registration-invitations": ["super_admin", "system_admin", "user_admin"],
    "subscriptions": ["super_admin", "billing_admin"],
    "model-entitlements": ["super_admin", "system_admin", "support"],
    "plugins": ["super_admin", "plugin_admin"],
    "quota-ledger": ["super_admin", "system_admin", "api_key_admin", "billing_admin"],
    "api-keys": ["super_admin", "api_key_admin", "support"],
    "user-pricing": ["super_admin", "billing_admin"],
    "user-payments": ["super_admin", "billing_admin"],
    "admin-users": ["super_admin", "user_admin", "system_admin", "support"]
  };
  const WEB_USER_NAV_ITEMS = [
    "client-policy", "client-logs", "registration-invitations",
    "subscriptions", "model-entitlements", "plugins", "quota-ledger",
    "user-pricing", "user-payments", "admin-users"
  ];

  function buildWebUserNavigation() {
    const nav = document.querySelector(".nav");
    if (!nav) return null;
    let group = document.getElementById("web-user-nav-group");
    if (!group) {
      group = document.createElement("details");
      group.id = "web-user-nav-group";
      group.className = "nav-group";
      group.dataset.navGroup = "web-user";
      const summary = document.createElement("summary");
      summary.textContent = "Web 用户端";
      summary.setAttribute("aria-label", "展开 Web 用户端二级导航");
      const submenu = document.createElement("div");
      submenu.className = "nav-submenu";
      submenu.setAttribute("aria-label", "Web 用户端管理");
      group.append(summary, submenu);
      const rbacButton = nav.querySelector('button[data-view="control-admins"]');
      if (rbacButton) rbacButton.insertAdjacentElement("afterend", group);
      else nav.appendChild(group);
    }
    const submenu = group.querySelector(".nav-submenu");
    for (const view of WEB_USER_NAV_ITEMS) {
      const item = nav.querySelector('button[data-view="' + view + '"]');
      if (item && item.parentElement !== submenu) submenu.appendChild(item);
    }
    group.addEventListener("click", (event) => {
      if (event.target.closest(".nav-submenu button[data-view]")) group.open = true;
    });
    window.addEventListener("hashchange", () => {
      const activeView = location.hash.replace(/^#/, "");
      const activeItem = Array.from(group.querySelectorAll(".nav-submenu button[data-view]"))
        .find((item) => item.dataset.view === activeView && !item.hidden);
      group.open = Boolean(activeItem);
    });
    return group;
  }

  buildWebUserNavigation();

  let adminSession = null;
  let sessionRefreshGeneration = 0;
  let invitationDisplayGeneration = 0;
  let roleCatalogue = {};

  function messageOf(value) {
    if (typeof value === "string") return value;
    try { return JSON.stringify(value); } catch (_) { return "Request failed"; }
  }

  async function sessionApi(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method: options.method || "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body)
    });
    const raw = await response.text();
    let data = {};
    try { data = raw ? JSON.parse(raw) : {}; } catch (_) { data = { detail: raw }; }
    if (!response.ok) throw new Error(messageOf(data.detail || data));
    return data;
  }

  function button(label, className, handler) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className || "action";
    node.textContent = label;
    node.addEventListener("click", handler);
    return node;
  }

  function cell(row, value) {
    const node = document.createElement("td");
    node.textContent = value == null ? "" : String(value);
    row.appendChild(node);
    return node;
  }

  function setStatus(id, value, bad = false) {
    const node = $(id);
    if (node) {
      node.textContent = value;
      node.className = bad ? "bad" : "muted";
    }
  }

  function roleCan(view) {
    const allowed = C_ROLES[view] || [];
    return !!(adminSession && adminSession.authenticated && allowed.includes(adminSession.role));
  }

  function renderSessionControls() {
    const authenticated = Boolean(adminSession && adminSession.authenticated);
    const loginButton = $("adminSessionOpenLogin");
    const logoutButton = $("adminSessionLogout");
    const state = $("adminSessionState");
    if (loginButton) loginButton.hidden = authenticated;
    if (logoutButton) logoutButton.hidden = !authenticated;
    if (state) {
      state.textContent = authenticated
        ? "已登录：" + adminSession.username + " · " + (adminSession.role || "unknown")
        : "未登录";
      state.className = authenticated ? "sessionIdentity ok" : "sessionIdentity muted";
    }
  }

  function applyNavigation() {
    // The existing Commerce module creates these nav buttons dynamically.
    // Adopt them into the same server-session RBAC visibility contract.
    for (const view of ["admin-users", "user-pricing", "user-payments"]) {
      document.querySelectorAll('.nav button[data-view="' + view + '"]').forEach((node) => {
        node.dataset.cview = "";
      });
    }
    document.querySelectorAll("[data-cview]").forEach((node) => {
      const allowed = roleCan(node.dataset.view);
      node.hidden = !allowed;
      // Author CSS sets nav buttons to display:flex, which overrides the
      // browser's user-agent [hidden] rule. Set display explicitly so a
      // denied route is actually absent from the visible navigation.
      node.style.display = allowed ? "" : "none";
      // The legacy inline bootstrap temporarily disables all navigation while
      // checking its own session. RBAC owns the final state.
      node.disabled = !allowed;
    });
    const root = adminSession && adminSession.authenticated && adminSession.role === "super_admin";
    document.querySelectorAll(".nav button:not([data-cview])").forEach((node) => {
      const visible = !(adminSession && adminSession.authenticated && !root);
      node.hidden = !visible;
      node.style.display = visible ? "" : "none";
      node.disabled = !root;
    });
    const webGroup = document.getElementById("web-user-nav-group");
    if (webGroup) {
      const visibleItems = Array.from(webGroup.querySelectorAll(".nav-submenu > button[data-view]"))
        .filter((node) => !node.hidden && node.style.display !== "none");
      webGroup.hidden = visibleItems.length === 0;
      if (visibleItems.some((node) => node.classList.contains("active"))) webGroup.open = true;
    }
    renderSessionControls();
    if ($("adminKey")) $("adminKey").disabled = !!(adminSession && adminSession.authenticated && !root);
    if ($("connect")) $("connect").hidden = !!(adminSession && adminSession.authenticated && !root);
    if ($("adminSessionRotatePassword")) {
      $("adminSessionRotatePassword").hidden = !(
        adminSession && adminSession.authenticated && adminSession.role !== "super_admin"
      );
    }
    const canWriteModels = adminSession && ["super_admin", "system_admin"].includes(adminSession.role);
    ["cEntSave"].forEach((id) => { if ($(id)) $(id).hidden = !canWriteModels; });
    const canManageUserRoles = adminSession && ["super_admin", "user_admin"].includes(adminSession.role);
    if ($("cUserRoleReload")) $("cUserRoleReload").disabled = !canManageUserRoles;
    const canManageSubscriptions = adminSession && ["super_admin", "billing_admin"].includes(adminSession.role);
    ["cPlanId", "cPlanName", "cPlanEnabled", "cPlanSave", "cPlanReload", "cSubUserId", "cSubPlan", "cSubExpiresAt", "cSubAssign", "cSubLoad", "cSubCancel"].forEach((id) => { if ($(id)) $(id).disabled = !canManageSubscriptions; });
    const canReconcileQuota = adminSession && ["super_admin", "system_admin", "api_key_admin"].includes(adminSession.role);
    if ($("cQuotaReconcile")) $("cQuotaReconcile").hidden = !canReconcileQuota;
    const canManageInvitations = adminSession && ["super_admin", "user_admin"].includes(adminSession.role);
    ["cInviteEmail", "cInviteTtl", "cInviteCreate"].forEach((id) => {
      if ($(id)) $(id).disabled = !canManageInvitations;
    });
    const canWriteKeys = adminSession && ["super_admin", "api_key_admin"].includes(adminSession.role);
    ["cKeyName", "cKeyExpiry", "cKeyQuota", "cKeyCreate"].forEach((id) => {
      if ($(id)) $(id).disabled = !canWriteKeys;
    });
    const canManageSharedWebChatKey = adminSession && adminSession.role === "super_admin";
    ["cClientApiKey", "cClientClearApiKey"].forEach((id) => {
      if ($(id)) {
        $(id).disabled = !canManageSharedWebChatKey;
        $(id).title = canManageSharedWebChatKey ? "" : "仅超级管理员可以配置 Web Chat 统一 API Key";
      }
    });
    ["cEntSubjectType", "cEntSubjectId", "cEntEffect", "cEntModels"].forEach((id) => {
      if ($(id)) $(id).disabled = !canWriteModels;
    });
  }

  async function refreshSession() {
    clearInvitationCode();
    const generation = ++sessionRefreshGeneration;
    try {
      const nextSession = await sessionApi("/api/admin/auth/session");
      if (generation !== sessionRefreshGeneration) return adminSession;
      adminSession = nextSession;
      renderSessionControls();
      if (typeof status === "function") {
        status(
          adminSession.authenticated ? "管理员会话已连接" : "请登录管理员账号",
          adminSession.authenticated ? "ok" : "muted"
        );
      }
      if (adminSession.authenticated && adminSession.role !== "super_admin") {
        // Prevent a stale root API key from a previous administrator session
        // from being reused by the legacy inline admin API helper.
        try { sessionStorage.removeItem("chat2api_admin_key"); } catch (_) {}
        if ($("adminKey")) $("adminKey").value = "";
      }
      applyNavigation();
      window.dispatchEvent(new CustomEvent("chat2api-admin-session-changed", { detail: adminSession }));
      const active = location.hash.replace(/^#/, "");
      if (adminSession.authenticated && adminSession.role !== "super_admin") {
        const defaults = {
          system_admin: "client-policy",
          user_admin: "admin-users",
          api_key_admin: "api-keys",
          billing_admin: "user-pricing",
          plugin_admin: "plugins",
          support: "model-entitlements",
        };
        show(roleCan(active) ? active : (defaults[adminSession.role] || "model-entitlements"));
      } else if (C_ROLES[active]) {
        if (!roleCan(active)) show("overview");
        else show(active);
      }
      return adminSession;
    } catch (error) {
      if (generation !== sessionRefreshGeneration) return adminSession;
      adminSession = null;
      renderSessionControls();
      applyNavigation();
      window.dispatchEvent(new CustomEvent("chat2api-admin-session-changed", {
        detail: { authenticated: false, role: null, username: "" },
      }));
      setStatus("adminSessionState", "会话检查失败：" + error.message, true);
      return null;
    }
  }

  // Keep the authoritative RBAC session synchronized with v17 authentication
  // events without requiring a page reload.
  window.addEventListener("chat2api-admin-auth-state-changed", () => {
    void refreshSession();
  });

  $("adminSessionOpenLogin")?.addEventListener("click", () => {
    const dialog = $("adminSessionLoginDialog");
    const error = $("adminSessionLoginError");
    if (error) error.textContent = "";
    if (dialog && !dialog.open) dialog.showModal();
    $("adminSessionUser")?.focus();
  });
  $("adminSessionLoginClose")?.addEventListener("click", () => $("adminSessionLoginDialog")?.close());
  $("adminSessionLoginCancel")?.addEventListener("click", () => $("adminSessionLoginDialog")?.close());
  $("adminSessionPassword")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("adminSessionLogin")?.click();
    }
  });

  $("adminSessionLogin")?.addEventListener("click", async () => {
    const username = $("adminSessionUser").value.trim();
    const password = $("adminSessionPassword").value;
    if (!username || !password) {
      setStatus("adminSessionLoginError", "请输入管理员用户名和密码", true);
      return;
    }
    const loginButton = $("adminSessionLogin");
    const errorNode = $("adminSessionLoginError");
    if (errorNode) errorNode.textContent = "";
    loginButton.disabled = true;
    try {
      await sessionApi("/api/admin/auth/login", {
        method: "POST",
        body: { username, password }
      });
      $("adminSessionPassword").value = "";
      await refreshSession();
      if (adminSession && adminSession.authenticated) {
        $("adminSessionLoginDialog")?.close();
        if (typeof status === "function") status("管理员 Session 已建立 · " + adminSession.role, "ok");
      } else if (errorNode) {
        errorNode.textContent = "登录状态尚未确认，请重试";
      }
    } catch (error) {
      setStatus("adminSessionLoginError", error.message, true);
    } finally {
      loginButton.disabled = false;
    }
  });

  $("adminSessionLogout")?.addEventListener("click", async () => {
    try {
      await sessionApi("/api/admin/auth/logout", { method: "POST", body: {} });
      adminSession = null;
      await refreshSession();
      if (typeof status === "function") status("管理员 Session 已退出", "ok");
    } catch (error) {
      setStatus("adminSessionState", "退出失败：" + error.message, true);
    }
  });

  $("adminSessionRotatePassword")?.addEventListener("click", () => {
    if (!adminSession || !adminSession.authenticated || adminSession.role === "super_admin") return;
    $("adminPasswordCurrent").value = "";
    $("adminPasswordNew").value = "";
    $("adminPasswordConfirm").value = "";
    setStatus("adminPasswordStatus", "请先输入当前密码和新密码");
    $("adminPasswordDialog").showModal();
  });
  $("adminPasswordClose")?.addEventListener("click", () => $("adminPasswordDialog").close());
  $("adminPasswordSave")?.addEventListener("click", async () => {
    const current_password = $("adminPasswordCurrent").value;
    const new_password = $("adminPasswordNew").value;
    const confirmation = $("adminPasswordConfirm").value;
    if (new_password.length < 12 || new_password.length > 256) {
      return setStatus("adminPasswordStatus", "新密码必须为 12-256 个字符", true);
    }
    if (new_password !== confirmation) {
      return setStatus("adminPasswordStatus", "两次输入的新密码不一致", true);
    }
    $("adminPasswordSave").disabled = true;
    try {
      await sessionApi("/api/admin/control/self/password", {
        method:"POST",
        body:{current_password, new_password}
      });
      $("adminPasswordDialog").close();
      $("adminPasswordCurrent").value = "";
      $("adminPasswordNew").value = "";
      $("adminPasswordConfirm").value = "";
      adminSession = null;
      await refreshSession();
      setStatus("adminSessionState", "密码已更新；请使用新密码重新登录");
    } catch (error) {
      setStatus("adminPasswordStatus", error.message, true);
    } finally {
      $("adminPasswordSave").disabled = false;
    }
  });

  const originalShow = show;
  show = function(view) {
    if (C_ROLES[view] && !roleCan(view)) {
      if (typeof status === "function") status("请先使用有权限的管理员 Session 登录", "bad");
      return;
    }
    originalShow(view);
    if (view !== "registration-invitations") clearInvitationCode();
    if (view === "control-admins") loadAdmins();
    if (view === "client-policy") loadClientPolicy();
    if (view === "registration-invitations") loadInvitations();
    if (view === "subscriptions") loadSubscriptions();
    if (view === "model-entitlements") loadEntitlements();
    if (view === "plugins") loadPlugins();
    if (view === "quota-ledger") loadQuotaReservations();
    if (view === "api-keys") loadManagedKeys();
  };

  async function loadAdmins() {
    try {
      const [roles, admins] = await Promise.all([
        sessionApi("/api/admin/control/roles"),
        sessionApi("/api/admin/control/admins")
      ]);
      roleCatalogue = roles.roles || {};
      const selector = $("cAdminRole");
      selector.replaceChildren();
      Object.keys(roleCatalogue).forEach((role) => {
        const option = document.createElement("option");
        option.value = role;
        option.textContent = role;
        selector.appendChild(option);
      });
      if (Object.prototype.hasOwnProperty.call(roleCatalogue, "support")) selector.value = "support";
      const body = $("cAdminRows");
      body.replaceChildren();
      (admins.data || []).forEach((item) => {
        const row = document.createElement("tr");
        cell(row, item.username);
        cell(row, item.role);
        cell(row, item.enabled ? "enabled" : "disabled");
        const actions = document.createElement("td");
        if (item.source !== "environment") {
          const select = document.createElement("select");
          Object.keys(roleCatalogue).forEach((role) => {
            const option = document.createElement("option");
            option.value = role;
            option.textContent = role;
            option.selected = role === item.role;
            select.appendChild(option);
          });
          actions.appendChild(select);
          actions.appendChild(button("保存角色", "action", async () => {
            try {
              await sessionApi("/api/admin/control/admins/" + encodeURIComponent(item.username), {
                method: "PATCH", body: { role: select.value }
              });
              await loadAdmins();
            } catch (error) { setStatus("cAdminStatus", error.message, true); }
          }));
          actions.appendChild(button(item.enabled ? "停用" : "启用", item.enabled ? "action danger" : "action good", async () => {
            try {
              await sessionApi("/api/admin/control/admins/" + encodeURIComponent(item.username), {
                method: "PATCH", body: { enabled: !item.enabled }
              });
              await loadAdmins();
            } catch (error) { setStatus("cAdminStatus", error.message, true); }
          }));
        } else {
          actions.textContent = "环境配置的 root 管理员";
        }
        row.appendChild(actions);
        body.appendChild(row);
      });
      setStatus("cAdminStatus", "已加载 " + (admins.data || []).length + " 个管理员身份");
    } catch (error) { setStatus("cAdminStatus", error.message, true); }
  }

  $("cAdminCreate")?.addEventListener("click", async () => {
    const createButton = $("cAdminCreate");
    // Do not leave a prior success visible while the next administrator is
    // still being persisted. Otherwise an E2E client (or human operator) can
    // mistake the previous account's result for this request and log out
    // before its server-side creation has completed.
    if (createButton.disabled) return;
    createButton.disabled = true;
    setStatus("cAdminStatus", "正在创建管理员…");
    try {
      await sessionApi("/api/admin/control/admins", {
        method: "POST",
        body: {
          username: $("cAdminName").value.trim(),
          password: $("cAdminPassword").value,
          role: $("cAdminRole").value
        }
      });
      $("cAdminPassword").value = "";
      await loadAdmins();
      setStatus("cAdminStatus", "管理员已创建；密码不会再次显示");
    } catch (error) {
      setStatus("cAdminStatus", error.message, true);
    } finally {
      createButton.disabled = false;
    }
  });
  $("cAdminReload")?.addEventListener("click", loadAdmins);

  async function loadClientPolicy() {
    try {
      const data = await sessionApi("/api/admin/control/client");
      $("cClientDomain").value = data.client_domain || "";
      $("cRegistrationMode").value = data.registration_mode;
      $("cWebChatEnabled").value = String(data.web_chat_enabled);
      $("cGuestEnabled").value = String(data.guest_enabled === true);
      $("cAnnouncement").value = data.announcement || "";
      $("cClientApiKey").value = "";
      $("cClientClearApiKey").checked = false;
      const apiKeyStatus = !data.api_key_configured
        ? "未配置；Web Chat 用户暂时无法发起请求"
        : (data.api_key_enabled
          ? "已配置：" + (data.api_key_name || "API Key") + " · " + (data.api_key_prefix || "已隐藏") + " · Mode 1 专用"
          : "已配置但当前不可用；请重新配置或启用该 Key");
      setStatus("cClientApiKeyStatus", apiKeyStatus, Boolean(data.api_key_configured && !data.api_key_enabled));
      setStatus("cClientStatus", "配置已读取 · policy v" + data.policy_version);
    } catch (error) { setStatus("cClientStatus", error.message, true); }
  }
  $("cClientSave")?.addEventListener("click", async () => {
    try {
      const body = {
        client_domain: $("cClientDomain").value.trim(),
        registration_mode: $("cRegistrationMode").value,
        web_chat_enabled: $("cWebChatEnabled").value === "true",
        guest_enabled: $("cGuestEnabled").value === "true",
        announcement: $("cAnnouncement").value
      };
      const apiKey = $("cClientApiKey").value.trim();
      if (apiKey) body.api_key = apiKey;
      if ($("cClientClearApiKey").checked) body.clear_api_key = true;
      await sessionApi("/api/admin/control/client", {method: "PUT", body});
      $("cClientApiKey").value = "";
      $("cClientClearApiKey").checked = false;
      await loadClientPolicy();
      setStatus("cClientStatus", "保存成功；服务端策略已更新");
    } catch (error) { setStatus("cClientStatus", error.message, true); }
  });
  $("cClientReload")?.addEventListener("click", loadClientPolicy);

  async function loadInvitations() {
    try {
      const result = await sessionApi("/api/admin/control/invitations?limit=100");
      const rows = Array.isArray(result.data) ? result.data : [];
      const body = $("cInviteRows");
      body.replaceChildren();
      const canManage = adminSession && ["super_admin", "user_admin"].includes(adminSession.role);
      rows.forEach((item) => {
        const row = document.createElement("tr");
        cell(row, item.email);
        cell(row, item.status);
        cell(row, item.created_by);
        cell(row, item.created_at ? new Date(Number(item.created_at) * 1000).toLocaleString() : "-");
        cell(row, item.expires_at ? new Date(Number(item.expires_at) * 1000).toLocaleString() : "-");
        const actions = cell(row, "");
        if (canManage && item.status === "issued") {
          actions.appendChild(button("撤销", "action danger", async () => {
            if (!confirm("确定撤销此邀请？")) return;
            try {
              await sessionApi("/api/admin/control/invitations/" + encodeURIComponent(item.invitation_id) + "/revoke", {method:"POST", body:{}});
              setStatus("cInviteStatus", "邀请已撤销");
              await loadInvitations();
            } catch (error) { setStatus("cInviteStatus", error.message, true); }
          }));
        }
        body.appendChild(row);
      });
      setStatus("cInviteStatus", "已读取 " + rows.length + " 条邀请记录；原始代码不会在列表中显示");
    } catch (error) { setStatus("cInviteStatus", error.message, true); }
  }

  function clearInvitationCode() {
    invitationDisplayGeneration++;
    $("cInviteCode").textContent = "";
    $("cInviteCodePanel").classList.add("hidden");
  }
  window.addEventListener("pagehide", clearInvitationCode);

  $("cInviteCreate")?.addEventListener("click", async () => {
    const email = $("cInviteEmail").value.trim();
    if (!email) return setStatus("cInviteStatus", "请填写受邀邮箱", true);
    clearInvitationCode();
    const generation = invitationDisplayGeneration;
    try {
      const result = await sessionApi("/api/admin/control/invitations", {
        method: "POST",
        body: {email, expires_in_seconds: Number($("cInviteTtl").value || 604800)}
      });
      if (generation !== invitationDisplayGeneration) return;
      $("cInviteCode").textContent = result.code || "";
      $("cInviteCodePanel").classList.remove("hidden");
      $("cInviteEmail").value = "";
      await loadInvitations();
      if (generation === invitationDisplayGeneration) {
        setStatus("cInviteStatus", "邀请已创建。请立即复制代码；离开此页面后不会再次显示。");
      }
    } catch (error) {
      if (generation === invitationDisplayGeneration) setStatus("cInviteStatus", error.message, true);
    }
  });
  $("cInviteReload")?.addEventListener("click", loadInvitations);
  $("cInviteCopy")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("cInviteCode").textContent || "");
      setStatus("cInviteStatus", "邀请代码已复制");
    } catch (_) { setStatus("cInviteStatus", "复制失败，请手动复制代码", true); }
  });

  async function loadEntitlements() {
    try {
      const result = await sessionApi("/api/admin/model-entitlements");
      const body = $("cEntRows");
      body.replaceChildren();
      (result.data || []).forEach((rule) => {
        const row = document.createElement("tr");
        cell(row, rule.subject_type + "/" + rule.subject_id);
        cell(row, rule.effect);
        cell(row, (rule.models || []).join(", "));
        cell(row, rule.enabled ? "enabled" : "disabled");
        cell(row, rule.updated_at);
        const actions = document.createElement("td");
        if (adminSession && ["super_admin", "system_admin"].includes(adminSession.role)) {
          actions.appendChild(button("删除", "action danger", async () => {
            if (!confirm("确认删除此模型权限规则？")) return;
            try {
              await sessionApi("/api/admin/model-entitlements/" + encodeURIComponent(rule.subject_type) + "/" + encodeURIComponent(rule.subject_id), { method: "DELETE" });
              await loadEntitlements();
            } catch (error) { setStatus("cEntStatus", error.message, true); }
          }));
        }
        row.appendChild(actions);
        body.appendChild(row);
      });
      setStatus("cEntStatus", "已加载 " + (result.data || []).length + " 条规则");
    } catch (error) { setStatus("cEntStatus", error.message, true); }
  }
  $("cEntSubjectType")?.addEventListener("change", () => {
    const placeholders = {
      global: "global 使用 default",
      tenant: "ten_...",
      tenant_role: "owner / admin / member / viewer",
      user_role: "user / trusted / ...",
      subscription_plan: "free / pro / ...",
      user: "usr_...",
      key: "key_..."
    };
    $("cEntSubjectId").placeholder = placeholders[$("cEntSubjectType").value] || "";
  });
  $("cEntSave")?.addEventListener("click", async () => {
    try {
      const subjectType = $("cEntSubjectType").value;
      const subjectId = subjectType === "global" ? "default" : $("cEntSubjectId").value.trim();
      const models = $("cEntModels").value.split(/[\n,，]+/).map((item) => item.trim()).filter(Boolean);
      await sessionApi("/api/admin/model-entitlements/" + encodeURIComponent(subjectType) + "/" + encodeURIComponent(subjectId), {
        method: "PUT",
        body: { effect: $("cEntEffect").value, models, enabled: true }
      });
      await loadEntitlements();
    } catch (error) { setStatus("cEntStatus", error.message, true); }
  });
  $("cEntReload")?.addEventListener("click", loadEntitlements);

  async function loadUserRoles() {
    try {
      const query = $("cUserRoleSearch")?.value.trim() || "";
      const result = await sessionApi("/api/admin/users?limit=200&offset=0" + (query ? "&q=" + encodeURIComponent(query) : ""));
      const body = $("cUserRoleRows");
      body.replaceChildren();
      (result.data || []).forEach((user) => {
        const row = document.createElement("tr");
        cell(row, user.user_id);
        cell(row, user.email);
        cell(row, user.display_name);
        const roleCell = document.createElement("td");
        const roleInput = document.createElement("input");
        roleInput.value = user.system_role || "user";
        roleInput.maxLength = 64;
        roleInput.setAttribute("aria-label", "End-user system role");
        roleCell.appendChild(roleInput);
        row.appendChild(roleCell);
        const actions = document.createElement("td");
        actions.appendChild(button("保存角色", "action good", async () => {
          try {
            const updated = await sessionApi("/api/admin/users/" + encodeURIComponent(user.user_id) + "/role", {
              method: "PUT", body: { system_role: roleInput.value.trim() }
            });
            roleInput.value = updated.user.system_role;
            setStatus("cUserRoleStatus", "已更新 " + user.user_id + " → " + updated.user.system_role);
          } catch (error) { setStatus("cUserRoleStatus", error.message, true); }
        }));
        row.appendChild(actions);
        body.appendChild(row);
      });
      setStatus("cUserRoleStatus", "已加载 " + (result.data || []).length + " 个用户");
    } catch (error) { setStatus("cUserRoleStatus", error.message, true); }
  }
  $("cUserRoleReload")?.addEventListener("click", loadUserRoles);
  $("cUserRoleSearch")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadUserRoles();
  });

  async function loadSubscriptions() {
    try {
      const result = await sessionApi("/api/admin/subscription-plans");
      const plans = result.data || [];
      const rows = $("cPlanRows");
      rows.replaceChildren();
      const selector = $("cSubPlan");
      const previous = selector.value;
      selector.replaceChildren();
      plans.forEach((plan) => {
        const option = document.createElement("option");
        option.value = plan.plan_id;
        option.textContent = plan.plan_id + " · " + plan.name + (plan.enabled ? "" : "（停用）");
        option.disabled = !plan.enabled;
        selector.appendChild(option);
        const row = document.createElement("tr");
        cell(row, plan.plan_id);
        cell(row, plan.name);
        cell(row, plan.enabled ? "enabled" : "disabled");
        cell(row, plan.updated_at);
        rows.appendChild(row);
      });
      if (plans.some((plan) => plan.plan_id === previous && plan.enabled)) selector.value = previous;
      setStatus("cPlanStatus", "已加载 " + plans.length + " 个套餐");
    } catch (error) { setStatus("cPlanStatus", error.message, true); }
  }

  $("cPlanSave")?.addEventListener("click", async () => {
    try {
      const planId = $("cPlanId").value.trim();
      const result = await sessionApi("/api/admin/subscription-plans/" + encodeURIComponent(planId), {
        method: "PUT",
        body: { name: $("cPlanName").value.trim(), enabled: $("cPlanEnabled").value === "true" }
      });
      setStatus("cPlanStatus", "已保存套餐 " + result.plan.plan_id);
      await loadSubscriptions();
    } catch (error) { setStatus("cPlanStatus", error.message, true); }
  });
  $("cPlanReload")?.addEventListener("click", loadSubscriptions);

  async function loadUserSubscription() {
    const userId = $("cSubUserId").value.trim();
    if (!userId) { setStatus("cSubStatus", "请填写 User ID", true); return; }
    try {
      const result = await sessionApi("/api/admin/users/" + encodeURIComponent(userId) + "/subscription");
      const current = result.current || {};
      $("cSubCurrent").textContent = "当前套餐：" + (current.plan_id || "-") + " · " +
        (current.name || "-") + " · " + (current.status || "-") +
        (current.expires_at ? " · 到期 " + current.expires_at : "");
      const body = $("cSubHistory");
      body.replaceChildren();
      (result.history || []).forEach((item) => {
        const row = document.createElement("tr");
        cell(row, item.subscription_id);
        cell(row, item.plan_id);
        cell(row, item.status);
        cell(row, item.starts_at);
        cell(row, item.expires_at || "永久");
        cell(row, item.assigned_by);
        body.appendChild(row);
      });
      setStatus("cSubStatus", "已加载订阅历史 " + (result.history || []).length + " 条");
    } catch (error) { setStatus("cSubStatus", error.message, true); }
  }
  $("cSubLoad")?.addEventListener("click", loadUserSubscription);
  $("cSubAssign")?.addEventListener("click", async () => {
    const userId = $("cSubUserId").value.trim();
    if (!userId) { setStatus("cSubStatus", "请填写 User ID", true); return; }
    try {
      const rawExpiry = $("cSubExpiresAt").value;
      const expiresAt = rawExpiry ? new Date(rawExpiry).toISOString() : null;
      const result = await sessionApi("/api/admin/users/" + encodeURIComponent(userId) + "/subscription", {
        method: "PUT",
        body: { plan_id: $("cSubPlan").value, expires_at: expiresAt }
      });
      setStatus("cSubStatus", "已分配 " + result.subscription.plan_id + " 套餐");
      await loadUserSubscription();
    } catch (error) { setStatus("cSubStatus", error.message, true); }
  });
  $("cSubCancel")?.addEventListener("click", async () => {
    const userId = $("cSubUserId").value.trim();
    if (!userId) { setStatus("cSubStatus", "请填写 User ID", true); return; }
    if (!confirm("确认取消该用户当前订阅并回退到 free 套餐？")) return;
    try {
      const result = await sessionApi("/api/admin/users/" + encodeURIComponent(userId) + "/subscription", {
        method: "DELETE"
      });
      setStatus("cSubStatus", result.cancelled ? "当前订阅已取消" : "没有活动订阅");
      await loadUserSubscription();
    } catch (error) { setStatus("cSubStatus", error.message, true); }
  });

  async function loadPlugins() {
    try {
      const result = await sessionApi("/api/admin/plugins");
      const body = $("cPluginRows");
      body.replaceChildren();
      (result.data || []).forEach((plugin) => {
        const row = document.createElement("tr");
        cell(row, plugin.id);
        cell(row, plugin.version);
        cell(row, plugin.sha256);
        cell(row, plugin.trusted ? "trusted" : "untrusted");
        cell(row, plugin.execution_enabled ? "enabled" : "disabled");
        const actionCell = document.createElement("td");
        actionCell.appendChild(button("详情", "action", async () => {
          try {
            const detail = await sessionApi("/api/admin/plugins/" + encodeURIComponent(plugin.id) + "/versions/" + encodeURIComponent(plugin.version));
            $("cPluginDetail").textContent = JSON.stringify(detail, null, 2);
          } catch (error) { setStatus("cPluginStatus", error.message, true); }
        }));
        row.appendChild(actionCell);
        body.appendChild(row);
      });
      setStatus("cPluginStatus", "插件执行状态固定为关闭");
    } catch (error) { setStatus("cPluginStatus", error.message, true); }
  }
  $("cPluginRegister")?.addEventListener("click", async () => {
    try {
      const manifest = JSON.parse($("cPluginManifest").value);
      const result = await sessionApi("/api/admin/plugins", { method: "POST", body: manifest });
      $("cPluginDetail").textContent = JSON.stringify(result, null, 2);
      await loadPlugins();
    } catch (error) { setStatus("cPluginStatus", error.message, true); }
  });
  $("cPluginReload")?.addEventListener("click", loadPlugins);

  async function loadManagedKeys() {
    try {
      const result = await sessionApi("/api/admin/keys");
      const body = $("cKeyRows");
      body.replaceChildren();
      const canWrite = adminSession && ["super_admin", "api_key_admin"].includes(adminSession.role);
      (result.data || []).forEach((item) => {
        const row = document.createElement("tr");
        cell(row, item.key_id);
        cell(row, item.name);
        cell(row, item.enabled ? "enabled" : (item.revoked_at ? "revoked" : "disabled"));
        cell(row, item.tokens_used == null ? "-" : item.tokens_used);
        cell(row, item.token_quota == null ? "unlimited" : item.token_quota);
        const actions = document.createElement("td");
        if (canWrite && !item.revoked_at && item.key_id !== "master") {
          actions.appendChild(button(item.enabled ? "停用" : "启用", item.enabled ? "action danger" : "action good", async () => {
            try {
              await sessionApi("/api/admin/keys/" + encodeURIComponent(item.key_id), {
                method: "PATCH", body: { enabled: !item.enabled }
              });
              await loadManagedKeys();
            } catch (error) { setStatus("cKeyStatus", error.message, true); }
          }));
          actions.appendChild(button("编辑名称/权限", "action", async () => {
            const name = prompt("API Key 名称", item.name || "");
            if (name === null) return;
            const scopes = prompt("权限范围（chat, models, files, images, audio）", (item.scopes || []).join(", "));
            if (scopes === null) return;
            try {
              await sessionApi("/api/admin/keys/" + encodeURIComponent(item.key_id) + "/settings", {
                method: "PATCH",
                body: { name: name.trim(), scopes: scopes.split(",").map(value => value.trim()).filter(Boolean) }
              });
              await loadManagedKeys();
            } catch (error) { setStatus("cKeyStatus", error.message, true); }
          }));
          actions.appendChild(button("调整额度", "action", async () => {
            const value = prompt("输入新的 Token 额度；留空表示不限", item.token_quota == null ? "" : String(item.token_quota));
            if (value === null) return;
            const quota = value.trim() === "" ? null : Number(value);
            if (quota !== null && (!Number.isSafeInteger(quota) || quota < 0)) {
              setStatus("cKeyStatus", "Token 额度必须是非负整数", true);
              return;
            }
            try {
              await sessionApi("/api/admin/keys/" + encodeURIComponent(item.key_id), {
                method: "PATCH", body: { token_quota: quota }
              });
              await loadManagedKeys();
            } catch (error) { setStatus("cKeyStatus", error.message, true); }
          }));
          actions.appendChild(button("永久撤销", "action danger", async () => {
            if (!confirm("永久撤销此 API Key？此操作不可恢复。")) return;
            try {
              await sessionApi("/api/admin/keys/" + encodeURIComponent(item.key_id), { method: "DELETE" });
              await loadManagedKeys();
            } catch (error) { setStatus("cKeyStatus", error.message, true); }
          }));
        } else if (!canWrite) {
          actions.textContent = "只读";
        }
        row.appendChild(actions);
        body.appendChild(row);
      });
      setStatus("cKeyStatus", "已加载 " + (result.data || []).length + " 个 API Key；历史 Token 不可读取");
    } catch (error) { setStatus("cKeyStatus", error.message, true); }
  }
  $("cKeyReload")?.addEventListener("click", loadManagedKeys);
  $("cKeyCreate")?.addEventListener("click", async () => {
    try {
      const name = $("cKeyName").value.trim();
      const expiryText = $("cKeyExpiry").value.trim();
      const quotaText = $("cKeyQuota").value.trim();
      const result = await sessionApi("/api/admin/keys", {
        method: "POST",
        body: {
          name,
          expires_in_days: expiryText ? Number(expiryText) : null,
          token_quota: quotaText ? Number(quotaText) : null
        }
      });
      $("cKeySecret").textContent = "完整 Token（仅此次创建响应可见，请立即保存）：\n" + result.token;
      $("cKeySecret").classList.remove("hidden");
      setStatus("cKeyStatus", "Key 已创建；完整 Token 不会再次显示");
      await loadManagedKeys();
    } catch (error) { setStatus("cKeyStatus", error.message, true); }
  });

  async function loadQuotaReservations() {
    try {
      const params = new URLSearchParams();
      if ($("cQuotaState").value) params.set("state", $("cQuotaState").value);
      if ($("cQuotaKeyId").value.trim()) params.set("key_id", $("cQuotaKeyId").value.trim());
      const query = params.toString();
      const result = await sessionApi("/api/admin/quota-reservations" + (query ? "?" + query : ""));
      const body = $("cQuotaRows");
      body.replaceChildren();
      (result.data || []).forEach((item) => {
        const row = document.createElement("tr");
        cell(row, item.request_id);
        cell(row, item.key_id);
        cell(row, item.reserved_tokens);
        cell(row, item.actual_tokens == null ? "-" : item.actual_tokens);
        cell(row, item.state);
        cell(row, new Date(item.created_at * 1000).toLocaleString());
        cell(row, new Date(item.expires_at * 1000).toLocaleString());
        body.appendChild(row);
      });
      const state = result.runtime_integration_enabled
        ? "请求生命周期预留已接入"
        : "仅安装了预留账本 API；请求准入 / 完成回调尚未接入，不能视为已强制执行配额";
      setStatus("cQuotaStatus", state + " · " + (result.data || []).length + " 条记录 · 当前结果集预留 " + result.active_reserved_tokens_in_result + " tokens");
    } catch (error) { setStatus("cQuotaStatus", error.message, true); }
  }
  $("cQuotaReload")?.addEventListener("click", loadQuotaReservations);
  $("cQuotaState")?.addEventListener("change", loadQuotaReservations);
  $("cQuotaReconcile")?.addEventListener("click", async () => {
    if (!confirm("仅根据 ApiKeyStore 已有 request_id 账本恢复 committing 状态；继续？")) return;
    try {
      const result = await sessionApi("/api/admin/quota-reservations/reconcile", { method: "POST", body: {} });
      setStatus("cQuotaStatus", "恢复完成 · " + result.settled + " 条提交状态已对账");
      await loadQuotaReservations();
    } catch (error) { setStatus("cQuotaStatus", error.message, true); }
  });


  window.__CHAT2API_CONTROL_PLANE_ADMIN_V1_READY__ = true;
  refreshSession();
})();
