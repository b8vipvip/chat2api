(() => {
  "use strict";
  const nav = document.querySelector(".nav");
  const content = document.querySelector(".content");
  if (!nav || !content || document.getElementById("view-user-pricing")) return;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[ch]);
  const keyHeader = () => {
    const value = String(document.getElementById("adminKey")?.value || "").trim();
    return value ? {"x-api-key": value} : {};
  };
  async function adminApi(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {"Content-Type":"application/json", ...keyHeader(), ...(options.headers || {})},
    });
    let payload = {};
    try { payload = await response.json(); } catch {}
    if (!response.ok) throw new Error(payload.detail || payload?.error?.message || `HTTP ${response.status}`);
    return payload;
  }

  const pricingButton = document.createElement("button");
  pricingButton.dataset.view = "user-pricing";
  pricingButton.textContent = "价格配置";
  nav.appendChild(pricingButton);
  const paymentButton = document.createElement("button");
  paymentButton.dataset.view = "user-payments";
  paymentButton.textContent = "支付配置";
  nav.appendChild(paymentButton);

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
        <div class="muted">当前接入 ZPAY，用于用户费用中心的支付宝 / 微信充值。商户密钥加密保存，读取配置时不会返回明文。</div>
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
    </section>`);

  function activate(view) {
    document.querySelectorAll(".nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
    const title = document.getElementById("pageTitle");
    if (title) title.textContent = view === "user-pricing" ? "价格配置" : "支付配置";
    if (view === "user-pricing") loadPricing().catch(showPricingError);
    if (view === "user-payments") loadPayments().catch(showPaymentError);
  }
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
})();
