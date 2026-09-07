(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[ch]);
  const money = (cents) => `¥${(Number(cents || 0) / 100).toFixed(2)}`;
  const usd = (micros) => `$${(Number(micros || 0) / 1_000_000).toFixed(6)}`;
  const num = (value) => Number(value || 0).toLocaleString("zh-CN");
  const time = (value) => value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "-";
  const state = { user: null, keys: [], models: [], billing: null };
  const titles = {
    dashboard:["数据看板","账户 API 使用概览"], keys:["API 密钥","创建和管理访问密钥"], requests:["请求记录","当前账户的 API 请求"],
    profile:["账户资料","管理账户基础资料"], billing:["费用中心","余额、充值和估算费用"], models:["模型广场","可用模型与当前价格"],
    playground:["测试场","快速验证 API 请求"], docs:["开发文档","OpenAI-compatible API 接入说明"]
  };

  async function api(path, options = {}) {
    const init = { credentials: "same-origin", ...options };
    init.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    const response = await fetch(path, init);
    let payload = null;
    try { payload = await response.json(); } catch { payload = {}; }
    if (response.status === 401) { showAuth(); throw new Error(payload.detail || "登录已失效"); }
    if (!response.ok) throw new Error(payload.detail || payload?.error?.message || `HTTP ${response.status}`);
    return payload;
  }

  function showAuth(message = "") {
    $("authShell").classList.remove("hidden");
    $("appShell").classList.add("hidden");
    $("authError").textContent = message;
  }
  function showApp(user) {
    state.user = user;
    $("authShell").classList.add("hidden");
    $("appShell").classList.remove("hidden");
    $("topUser").textContent = user.display_name || user.email;
    $("profileEmail").value = user.email || "";
    $("profileName").value = user.display_name || "";
    $("profileId").value = user.user_id || "";
    navigate((location.hash || "#dashboard").slice(1));
  }

  async function loadMe() {
    const response = await fetch("/api/user/me", {credentials:"same-origin"});
    if (!response.ok) return showAuth();
    const payload = await response.json();
    showApp(payload.user);
  }

  function navigate(view) {
    if (!titles[view]) view = "dashboard";
    document.querySelectorAll(".nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === `view-${view}`));
    $("pageTitle").textContent = titles[view][0];
    $("pageHint").textContent = titles[view][1];
    if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
    refreshView(view).catch((error) => console.warn(error));
  }

  async function loadKeys() {
    const payload = await api("/api/user/keys");
    state.keys = payload.data || [];
    $("keysBody").innerHTML = state.keys.length ? state.keys.map((row) => {
      const active = row.enabled && !row.revoked_at && !row.expired;
      return `<tr><td>${esc(row.name)}</td><td><code>${esc(row.prefix)}</code></td><td><span class="pill">${active ? "可用" : "已停用"}</span></td><td>${esc(time(row.created_at))}</td><td>${esc(time(row.last_used_at))}</td><td><button class="action" data-reveal="${esc(row.key_id)}">查看</button> <button class="action danger" data-revoke="${esc(row.key_id)}">撤销</button></td></tr>`;
    }).join("") : `<tr><td colspan="6" class="muted">还没有 API Key。</td></tr>`;
    document.querySelectorAll("[data-reveal]").forEach((button) => button.onclick = () => revealKey(button.dataset.reveal));
    document.querySelectorAll("[data-revoke]").forEach((button) => button.onclick = () => revokeKey(button.dataset.revoke));
    syncPlayOptions();
  }

  async function createKey() {
    const name = $("keyName").value.trim() || "API Key";
    const days = Number($("keyDays").value || 0);
    const payload = await api("/api/user/keys", {method:"POST", body:JSON.stringify({name, expires_in_days: days || null})});
    $("newToken").textContent = payload.token || "";
    $("newSecret").classList.remove("hidden");
    $("keyName").value = "";
    $("keyDays").value = "";
    await loadKeys();
  }
  async function revealKey(keyId) {
    const payload = await api(`/api/user/keys/${encodeURIComponent(keyId)}/secret`);
    $("newToken").textContent = payload.token || "";
    $("newSecret").classList.remove("hidden");
    await navigator.clipboard?.writeText(payload.token || "").catch(() => {});
  }
  async function revokeKey(keyId) {
    if (!confirm("确定撤销这个 API Key？撤销后无法继续调用。")) return;
    await api(`/api/user/keys/${encodeURIComponent(keyId)}`, {method:"DELETE"});
    await loadKeys();
  }

  async function loadRequests() {
    const params = new URLSearchParams({limit:"100"});
    if ($("requestStatus").value) params.set("status", $("requestStatus").value);
    if ($("requestModel").value.trim()) params.set("model", $("requestModel").value.trim());
    const payload = await api(`/api/user/requests?${params}`);
    const rows = payload.data || [];
    $("requestsBody").innerHTML = rows.length ? rows.map((row) => `<tr><td>${esc(time(row.recorded_at))}</td><td><code>${esc(row.request_id)}</code></td><td><span class="pill">${esc(row.status)}</span></td><td>${esc(row.model || "-")}</td><td>${esc(num(row.usage?.total_tokens))}</td><td>${row.latency?.total_ms == null ? "-" : `${esc(Math.round(row.latency.total_ms))} ms`}</td><td title="USD ${esc(usd(row.cost?.usd_micros))}">${esc(money(row.cost?.cny_cents))}</td></tr>`).join("") : `<tr><td colspan="7" class="muted">暂无请求记录。</td></tr>`;
  }

  async function loadModels() {
    const payload = await api("/api/user/models");
    state.models = payload.data || [];
    $("modelsGrid").innerHTML = state.models.length ? state.models.map((row) => `<article class="model"><h3>${esc(row.name || row.model_id)}</h3><code>${esc(row.model_id)}</code><div class="price"><div><span class="muted">输入</span><br><b>$${Number(row.input_usd_per_million || 0).toFixed(3)}</b></div><div><span class="muted">缓存输入</span><br><b>$${Number(row.cached_input_usd_per_million || 0).toFixed(3)}</b></div><div><span class="muted">输出</span><br><b>$${Number(row.output_usd_per_million || 0).toFixed(3)}</b></div></div><div class="muted" style="margin-top:9px">每 1M Token · 管理员可调整</div></article>`).join("") : `<div class="notice">管理员尚未开放模型。</div>`;
    syncPlayOptions();
  }

  function syncPlayOptions() {
    const availableKeys = state.keys.filter((row) => row.enabled && !row.revoked_at && !row.expired);
    $("playKey").innerHTML = availableKeys.length ? availableKeys.map((row) => `<option value="${esc(row.key_id)}">${esc(row.name)} · ${esc(row.prefix)}</option>`).join("") : `<option value="">请先创建 API Key</option>`;
    $("playModel").innerHTML = state.models.length ? state.models.map((row) => `<option value="${esc(row.model_id)}">${esc(row.name || row.model_id)}</option>`).join("") : `<option value="">暂无模型</option>`;
  }

  async function loadBilling() {
    const payload = await api("/api/user/billing");
    state.billing = payload;
    $("balance").textContent = money(payload.balance_cents);
    $("billingSpend").textContent = money(payload.estimated_spend_cents);
    $("billingState").textContent = payload.billing_enabled ? "已启用" : "统计中";
    $("fxRate").textContent = Number(payload.usd_cny_rate || 0).toFixed(4);
    const payment = payload.payment || {};
    $("payNotice").textContent = payment.enabled ? "在线充值已启用。到账后余额会自动更新。" : "在线充值暂未启用，请联系服务管理员。";
    $("recharge").disabled = !payment.enabled;
    const channels = [];
    if (payment.alipay) channels.push(`<option value="alipay">支付宝</option>`);
    if (payment.wechat) channels.push(`<option value="wechat">微信支付</option>`);
    $("rechargeChannel").innerHTML = channels.join("") || `<option value="">暂无支付方式</option>`;
    const orders = payload.orders || [];
    $("ordersBody").innerHTML = orders.length ? orders.map((row) => `<tr><td>${esc(time(row.created_at))}</td><td>${esc(money(row.amount_cents))}</td><td>${row.channel === "alipay" ? "支付宝" : "微信支付"}</td><td>${row.status === "paid" ? "已支付" : "待支付"}</td></tr>`).join("") : `<tr><td colspan="4" class="muted">暂无充值记录。</td></tr>`;
  }

  async function recharge() {
    const yuan = Number($("rechargeAmount").value || 0);
    const amount_cents = Math.round(yuan * 100);
    if (!amount_cents) return alert("请输入充值金额");
    const channel = $("rechargeChannel").value;
    const payload = await api("/api/user/billing/recharge", {method:"POST", body:JSON.stringify({amount_cents, channel})});
    const checkout = payload.checkout || {};
    const parts = [`<div class="notice">订单已创建：${esc(money(payload.order?.amount_cents))}</div>`];
    if (checkout.qr_image_url) parts.push(`<p><img src="${esc(checkout.qr_image_url)}" alt="支付二维码" style="max-width:260px;border-radius:12px"></p>`);
    if (checkout.pay_url) parts.push(`<p><a class="action primary" href="${esc(checkout.pay_url)}" target="_blank" rel="noopener">前往支付</a></p>`);
    if (checkout.qr_payload && !checkout.qr_image_url) parts.push(`<div class="codebox">${esc(checkout.qr_payload)}</div>`);
    $("checkout").innerHTML = parts.join("");
    $("checkout").classList.remove("hidden");
    await loadBilling();
  }

  async function loadDashboard() {
    const payload = await api("/api/user/dashboard");
    $("dRequests").textContent = num(payload.requests);
    $("dSuccess").textContent = `${Number(payload.success_rate || 0).toFixed(1)}%`;
    $("dTokens").textContent = num(payload.estimated_tokens);
    $("dSpend").textContent = money(payload.estimated_spend_cents);
    const days = (payload.by_day || []).slice(-14);
    const max = Math.max(1, ...days.map((row) => Number(row.requests || 0)));
    $("dayChart").innerHTML = days.length ? days.map((row) => `<div class="bar" title="${esc(row.day)} · ${esc(row.requests)}" style="height:${Math.max(5, Number(row.requests || 0) / max * 145)}px"><span>${esc(String(row.day).slice(5))}</span></div>`).join("") : `<div class="muted">暂无趋势数据</div>`;
    $("modelStats").innerHTML = (payload.by_model || []).length ? payload.by_model.slice(0,12).map((row) => `<p><code>${esc(row.model)}</code> · ${esc(num(row.requests))} 次</p>`).join("") : "暂无数据";
  }

  async function runPlayground() {
    const key_id = $("playKey").value;
    const model = $("playModel").value;
    const prompt = $("playPrompt").value.trim();
    if (!key_id || !model || !prompt) return alert("请选择 API Key、模型并输入测试内容");
    $("playResponse").textContent = "请求中…";
    try {
      const payload = await api("/api/user/playground", {method:"POST", body:JSON.stringify({key_id, model, prompt})});
      $("playResponse").textContent = JSON.stringify(payload.response, null, 2);
    } catch (error) {
      $("playResponse").textContent = `测试失败：${error.message}`;
    }
  }

  async function saveProfile() {
    const payload = await api("/api/user/profile", {method:"PATCH", body:JSON.stringify({display_name:$("profileName").value.trim()})});
    showApp(payload.user);
  }

  function loadDocs() {
    const base = location.origin;
    $("docBase").textContent = base;
    $("docChat").textContent = `curl ${base}/v1/chat/completions \\\n  -H "Authorization: Bearer YOUR_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model": "gpt-5.6-sol",\n    "messages": [{"role":"user","content":"Hello"}],\n    "stream": false\n  }'`;
    $("docFiles").textContent = `POST ${base}/v1/files\nAuthorization: Bearer YOUR_API_KEY\nContent-Type: application/json\n\n{\n  "filename": "example.pdf",\n  "content_base64": "..."\n}`;
    $("docImages").textContent = `POST ${base}/v1/images/generations\nAuthorization: Bearer YOUR_API_KEY\nContent-Type: application/json\n\n{\n  "model": "gpt-image",\n  "prompt": "your prompt",\n  "n": 1\n}`;
  }

  async function refreshView(view) {
    if (view === "dashboard") return loadDashboard();
    if (view === "keys") return loadKeys();
    if (view === "requests") return loadRequests();
    if (view === "billing") return loadBilling();
    if (view === "models") return loadModels();
    if (view === "playground") { await Promise.all([loadKeys(), loadModels()]); return; }
    if (view === "docs") return loadDocs();
  }

  $("loginTab").onclick = () => { $("loginTab").classList.add("active"); $("registerTab").classList.remove("active"); $("loginForm").classList.remove("hidden"); $("registerForm").classList.add("hidden"); $("authError").textContent=""; };
  $("registerTab").onclick = () => { $("registerTab").classList.add("active"); $("loginTab").classList.remove("active"); $("registerForm").classList.remove("hidden"); $("loginForm").classList.add("hidden"); $("authError").textContent=""; };
  $("loginForm").onsubmit = async (event) => { event.preventDefault(); try { const payload = await api("/api/user/login", {method:"POST", body:JSON.stringify({email:$("loginEmail").value,password:$("loginPassword").value})}); showApp(payload.user); } catch(error) { $("authError").textContent=error.message; } };
  $("registerForm").onsubmit = async (event) => { event.preventDefault(); try { const payload = await api("/api/user/register", {method:"POST", body:JSON.stringify({display_name:$("registerName").value,email:$("registerEmail").value,password:$("registerPassword").value})}); showApp(payload.user); } catch(error) { $("authError").textContent=error.message; } };
  $("nav").onclick = (event) => { const button = event.target.closest("button[data-view]"); if (button) navigate(button.dataset.view); };
  $("createKey").onclick = () => createKey().catch((error) => alert(error.message));
  $("copyToken").onclick = () => navigator.clipboard.writeText($("newToken").textContent || "").catch(() => {});
  $("filterRequests").onclick = () => loadRequests().catch((error) => alert(error.message));
  $("saveProfile").onclick = () => saveProfile().catch((error) => alert(error.message));
  $("recharge").onclick = () => recharge().catch((error) => alert(error.message));
  $("runTest").onclick = runPlayground;
  $("refresh").onclick = () => refreshView((location.hash || "#dashboard").slice(1)).catch((error) => alert(error.message));
  $("logout").onclick = async () => { await fetch("/api/user/logout", {method:"POST", credentials:"same-origin"}); state.user=null; showAuth(); };
  window.addEventListener("hashchange", () => state.user && navigate(location.hash.slice(1)));
  loadDocs();
  loadMe().catch(() => showAuth());
})();
