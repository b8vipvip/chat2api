(() => {
  const VERSION = "0.21.3";
  const SESSION_MARKER = "__chat2api_admin_session__";
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  function currentView() {
    return (location.hash || "#overview").slice(1) || "overview";
  }

  function installFallbackAuth() {
    if (document.getElementById("adminLoginGate")) return;
    if (window.__chat2apiAdminFallbackInstalled) return;
    window.__chat2apiAdminFallbackInstalled = true;

    const state = window.__chat2apiAdminFallbackState = { loggedIn: false };
    const fetchImpl = window.fetch.bind(window);

    globalThis.key = () => state.loggedIn ? SESSION_MARKER : "";
    globalThis.headers = (credential = globalThis.key()) => {
      const value = String(credential || "");
      if (!value || value === SESSION_MARKER) return {};
      return { Authorization: "Bearer " + value };
    };
    globalThis.api = async (path, opt = {}) => {
      const credential = opt.key === undefined ? globalThis.key() : opt.key;
      const h = globalThis.headers(credential);
      if (opt.body !== undefined) h["Content-Type"] = "application/json";
      const response = await fetchImpl(path, {
        method: opt.method || "GET",
        headers: h,
        body: opt.body === undefined ? undefined : JSON.stringify(opt.body),
        cache: "no-store",
        credentials: "same-origin",
      });
      const text = await response.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { detail: text }; }
      if (!response.ok) throw new Error(data.detail || `${response.status} ${text}`);
      return data;
    };

    const auth = document.querySelector(".auth");
    if (auth) {
      auth.innerHTML = `
        <span id="adminIdentity" class="muted" style="align-self:center;white-space:nowrap"></span>
        <button class="action" id="adminLogout" style="display:none">退出登录</button>
        <button class="action" id="refresh">刷新</button>`;
      document.getElementById("refresh")?.addEventListener("click", () => {
        if (typeof globalThis.show === "function") globalThis.show(currentView());
      });
    }

    const gate = document.createElement("div");
    gate.id = "adminLoginGate";
    gate.style.cssText = "position:fixed;inset:0;z-index:100;background:rgba(5,10,18,.92);display:flex;align-items:center;justify-content:center;padding:20px";
    gate.innerHTML = `
      <div class="panel" style="width:min(440px,100%);padding:24px;box-shadow:0 28px 90px rgba(0,0,0,.5)">
        <div class="muted" style="font-size:12px;letter-spacing:.12em;text-transform:uppercase">chat2api administrator</div>
        <h2 style="margin:7px 0 5px">管理员登录</h2>
        <p class="muted" style="margin:0 0 16px">控制台使用独立管理员账号密码登录，不再使用 CHAT2API_API_KEY 作为管理员凭据。</p>
        <label class="muted">账号</label>
        <input id="adminUsername" autocomplete="username" style="width:100%;margin:5px 0 12px" value="admin">
        <label class="muted">密码</label>
        <input id="adminPassword" type="password" autocomplete="current-password" style="width:100%;margin:5px 0 14px">
        <button class="action good" id="adminLoginButton" style="width:100%">登录控制台</button>
        <div id="adminLoginError" class="bad" style="margin-top:10px"></div>
      </div>`;
    document.body.appendChild(gate);

    function setAuthenticated(value, username = "") {
      state.loggedIn = Boolean(value);
      gate.style.display = state.loggedIn ? "none" : "flex";
      const identity = document.getElementById("adminIdentity");
      if (identity) identity.textContent = state.loggedIn ? `管理员 · ${username || "admin"}` : "";
      const logout = document.getElementById("adminLogout");
      if (logout) logout.style.display = state.loggedIn ? "inline-block" : "none";
      if (!state.loggedIn && typeof globalThis.status === "function") globalThis.status("请登录管理员账号", "muted");
    }

    async function checkSession() {
      try {
        const response = await fetchImpl("/api/admin/auth/session", { cache: "no-store", credentials: "same-origin" });
        const data = await response.json();
        setAuthenticated(Boolean(data.authenticated), data.username || "");
        if (data.authenticated && typeof globalThis.show === "function") await globalThis.show(currentView());
      } catch (_) {
        setAuthenticated(false);
      }
    }

    document.getElementById("adminLoginButton")?.addEventListener("click", async () => {
      const errorBox = document.getElementById("adminLoginError");
      if (errorBox) errorBox.textContent = "";
      try {
        const response = await fetchImpl("/api/admin/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: document.getElementById("adminUsername")?.value.trim() || "admin",
            password: document.getElementById("adminPassword")?.value || "",
          }),
          credentials: "same-origin",
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "登录失败");
        const password = document.getElementById("adminPassword");
        if (password) password.value = "";
        setAuthenticated(true, data.username || "admin");
        if (typeof globalThis.show === "function") await globalThis.show(currentView());
      } catch (error) {
        if (errorBox) errorBox.textContent = String(error?.message || error);
      }
    });
    document.getElementById("adminPassword")?.addEventListener("keydown", event => {
      if (event.key === "Enter") document.getElementById("adminLoginButton")?.click();
    });
    document.getElementById("adminLogout")?.addEventListener("click", async () => {
      await fetchImpl("/api/admin/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
      setAuthenticated(false);
    });

    checkSession();
  }

  installFallbackAuth();
  window.__chat2apiAdminBundleReady = true;
})();
