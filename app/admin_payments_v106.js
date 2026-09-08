(() => {
  "use strict";
  if (window.__chat2apiAdminPaymentsV106) return;
  window.__chat2apiAdminPaymentsV106 = true;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[ch]);
  const keyHeader = () => {
    const value = String(document.getElementById("adminKey")?.value || "").trim();
    return value ? {"x-api-key": value} : {};
  };
  async function api(path, options = {}) {
    const response = await fetch(path, {credentials:"same-origin", ...options, headers:{"Content-Type":"application/json", ...keyHeader(), ...(options.headers || {})}});
    let payload = {};
    try { payload = await response.json(); } catch {}
    if (!response.ok) throw new Error(payload.detail || payload?.error?.message || `HTTP ${response.status}`);
    return payload;
  }

  const button = document.querySelector('.nav button[data-view="user-payments"]');
  const view = document.getElementById("view-user-payments");
  if (!button || !view) return;

  view.innerHTML = `
    <div class="panel">
      <h2>支付配置</h2>
      <div class="muted">支付接口分为官方/独立支付与聚合支付。用户端只展示已启用且配置完整的支付方式；密钥只加密保存，不会在读取接口中返回明文。</div>
      <label style="display:block;margin-top:14px"><span class="muted">公网 Origin（支付回调）</span><br><input id="payV106Origin" placeholder="https://api.example.com"></label>

      <div class="panels" style="margin-top:14px">
        <div class="panel">
          <h3>官方支付 · PayPal</h3>
          <div class="muted">使用 PayPal 官方 Orders v2 API。只需启用 PayPal 总开关；用户端会显示 PayPal。</div>
          <div style="display:grid;gap:10px;margin-top:10px">
            <label><span class="muted">PayPal 总开关</span><br><select id="payV106PaypalEnabled"><option value="false">关闭</option><option value="true">启用</option></select></label>
            <label><span class="muted">环境</span><br><select id="payV106PaypalSandbox"><option value="false">正式环境</option><option value="true">Sandbox 测试环境</option></select></label>
            <label><span class="muted">Client ID</span><br><input id="payV106PaypalClientId" autocomplete="off"></label>
            <label><span class="muted">Client Secret</span><br><input id="payV106PaypalSecret" type="password" autocomplete="new-password" placeholder="留空保持现有 Secret"></label>
          </div>
          <div class="toolbar"><button class="action" id="payV106TestPaypal">测试 PayPal API</button><span id="payV106PaypalStatus" class="muted"></span></div>
        </div>

        <div class="panel">
          <h3>USDT 支付 · 欧意收款</h3>
          <div class="muted">固定网络：TRON (TRC20)。用户提交 TXID 后由管理员核验到账再入账，避免仅凭浏览器回调自动加余额。</div>
          <div style="display:grid;gap:10px;margin-top:10px">
            <label><span class="muted">USDT 总开关</span><br><select id="payV106UsdtEnabled"><option value="false">关闭</option><option value="true">启用</option></select></label>
            <label><span class="muted">网络</span><br><input value="TRON (TRC20)" disabled></label>
            <label><span class="muted">欧意 USDT-TRC20 收款地址</span><br><input id="payV106UsdtAddress" autocomplete="off" placeholder="T...（TRON 地址）"></label>
            <label><span class="muted">收款二维码图片 URL（可选）</span><br><input id="payV106UsdtQr" placeholder="https://..."></label>
            <label><span class="muted">地址链接（可选）</span><br><input id="payV106UsdtLink" placeholder="https://..."></label>
          </div>
          <div id="payV106UsdtStatus" class="muted" style="margin-top:10px"></div>
        </div>
      </div>

      <div class="panel" style="margin-top:14px">
        <h3>聚合支付 · ZPAY</h3>
        <div class="muted">ZPAY 属于聚合支付。必须分别勾选要向用户开放的支付宝、微信支付；未勾选的方式不会出现在用户费用中心。</div>
        <div class="panels" style="margin-top:10px">
          <div class="panel">
            <label><span class="muted">ZPAY 总开关</span><br><select id="payV106ZpayEnabled"><option value="false">关闭</option><option value="true">启用</option></select></label>
            <label><span class="muted">PID</span><br><input id="payV106ZpayPid" autocomplete="off"></label>
            <label><span class="muted">商户密钥</span><br><input id="payV106ZpayKey" type="password" autocomplete="new-password" placeholder="留空保持现有密钥"></label>
          </div>
          <div class="panel">
            <label style="display:flex;gap:8px;align-items:center"><input id="payV106AlipayEnabled" type="checkbox" style="width:auto"> 启用支付宝</label>
            <label><span class="muted">支付宝渠道 ID（可选）</span><br><input id="payV106AlipayCid" placeholder="多个 ID 用英文逗号分隔"></label>
            <label style="display:flex;gap:8px;align-items:center"><input id="payV106WechatEnabled" type="checkbox" style="width:auto"> 启用微信支付</label>
            <label><span class="muted">微信渠道 ID（可选）</span><br><input id="payV106WechatCid" placeholder="多个 ID 用英文逗号分隔"></label>
          </div>
        </div>
        <div class="toolbar"><button class="action" id="payV106TestZpay">测试 ZPAY API</button><span id="payV106ZpayStatus" class="muted"></span></div>
      </div>

      <div class="toolbar" style="margin-top:14px"><button class="action good" id="payV106Save">保存支付配置</button><span id="payV106Status" class="muted"></span></div>

      <div class="panel" style="margin-top:14px">
        <h3>USDT 待核验订单</h3>
        <div class="muted">确认前请在欧意账户或 TRON 区块浏览器核对收款地址、TRC20 网络、USDT 金额和 TXID。</div>
        <div class="scroll"><table><thead><tr><th>时间</th><th>订单</th><th>人民币</th><th>应付 USDT</th><th>TXID</th><th>状态</th><th>操作</th></tr></thead><tbody id="payV106UsdtOrders"></tbody></table></div>
      </div>
    </div>`;

  function setStatus(id, text) { const node = document.getElementById(id); if (node) node.textContent = text; }
  function val(id) { return document.getElementById(id)?.value || ""; }
  function checked(id) { return Boolean(document.getElementById(id)?.checked); }

  async function load() {
    const payload = await api("/api/admin/payment-channels-v106");
    const z = payload.aggregate?.zpay || {};
    const p = payload.direct?.paypal || {};
    const u = payload.direct?.usdt || {};
    document.getElementById("payV106Origin").value = payload.public_origin || "";
    document.getElementById("payV106ZpayEnabled").value = String(Boolean(z.enabled));
    document.getElementById("payV106ZpayPid").value = z.pid || "";
    document.getElementById("payV106ZpayKey").value = "";
    document.getElementById("payV106ZpayKey").placeholder = z.key_configured ? "已保存密钥；留空保持不变" : "请输入 ZPAY 商户密钥";
    document.getElementById("payV106AlipayEnabled").checked = Boolean(z.alipay_enabled);
    document.getElementById("payV106WechatEnabled").checked = Boolean(z.wechat_enabled);
    document.getElementById("payV106AlipayCid").value = z.alipay_cid || "";
    document.getElementById("payV106WechatCid").value = z.wechat_cid || "";
    setStatus("payV106ZpayStatus", z.configured ? "ZPAY 商户凭据已配置" : "ZPAY 尚未配置完整");

    document.getElementById("payV106PaypalEnabled").value = String(Boolean(p.enabled));
    document.getElementById("payV106PaypalSandbox").value = String(Boolean(p.sandbox));
    document.getElementById("payV106PaypalClientId").value = p.client_id || "";
    document.getElementById("payV106PaypalSecret").value = "";
    document.getElementById("payV106PaypalSecret").placeholder = p.secret_configured ? "已保存 Secret；留空保持不变" : "请输入 Client Secret";
    setStatus("payV106PaypalStatus", p.configured ? "PayPal 凭据已配置" : "PayPal 尚未配置完整");

    document.getElementById("payV106UsdtEnabled").value = String(Boolean(u.enabled));
    document.getElementById("payV106UsdtAddress").value = u.address || "";
    document.getElementById("payV106UsdtQr").value = u.qr_image_url || "";
    document.getElementById("payV106UsdtLink").value = u.address_link || "";
    setStatus("payV106UsdtStatus", u.configured ? "USDT TRC20 收款地址已配置" : "USDT 收款地址尚未配置");
    setStatus("payV106Status", `已加载 · ${payload.updated_at || ""}`);
    await loadUsdtOrders();
  }

  async function save() {
    const body = {
      public_origin: val("payV106Origin").trim(),
      aggregate: {zpay: {
        enabled: val("payV106ZpayEnabled") === "true",
        pid: val("payV106ZpayPid").trim(),
        key: val("payV106ZpayKey") || null,
        alipay_enabled: checked("payV106AlipayEnabled"),
        wechat_enabled: checked("payV106WechatEnabled"),
        alipay_cid: val("payV106AlipayCid").trim(),
        wechat_cid: val("payV106WechatCid").trim(),
      }},
      direct: {
        paypal: {
          enabled: val("payV106PaypalEnabled") === "true",
          sandbox: val("payV106PaypalSandbox") === "true",
          client_id: val("payV106PaypalClientId").trim(),
          client_secret: val("payV106PaypalSecret") || null,
        },
        usdt: {
          enabled: val("payV106UsdtEnabled") === "true",
          address: val("payV106UsdtAddress").trim(),
          qr_image_url: val("payV106UsdtQr").trim(),
          address_link: val("payV106UsdtLink").trim(),
        },
      },
    };
    const payload = await api("/api/admin/payment-channels-v106", {method:"PUT", body:JSON.stringify(body)});
    setStatus("payV106Status", `保存成功 · ${payload.updated_at || ""}`);
    await load();
  }

  async function test(provider) {
    const id = provider === "paypal" ? "payV106PaypalStatus" : "payV106ZpayStatus";
    setStatus(id, "正在测试 API…");
    const payload = await api(`/api/admin/payment-channels-v106/test?provider=${encodeURIComponent(provider)}`, {method:"POST", body:"{}"});
    setStatus(id, `测试成功 · ${payload.message || "API 可达"}`);
  }

  async function loadUsdtOrders() {
    const payload = await api("/api/admin/payment-channels-v106/usdt-orders");
    const rows = payload.data || [];
    const body = document.getElementById("payV106UsdtOrders");
    body.innerHTML = rows.length ? rows.map((row) => `<tr><td>${esc(row.created_at || "-")}</td><td><code>${esc(row.order_id)}</code></td><td>¥${(Number(row.amount_cents || 0)/100).toFixed(2)}</td><td>${esc(row.provider_amount || "-")}</td><td><code>${esc(row.txid || "未提交")}</code></td><td>${esc(row.status || "-")}</td><td>${row.txid ? `<button class="action good" data-usdt-confirm="${esc(row.order_id)}">确认到账</button> <button class="action danger" data-usdt-reject="${esc(row.order_id)}">拒绝</button>` : "等待 TXID"}</td></tr>`).join("") : `<tr><td colspan="7" class="muted">暂无待核验 USDT 订单。</td></tr>`;
    body.querySelectorAll("[data-usdt-confirm]").forEach((node) => node.onclick = () => review(node.dataset.usdtConfirm, "confirm"));
    body.querySelectorAll("[data-usdt-reject]").forEach((node) => node.onclick = () => review(node.dataset.usdtReject, "reject"));
  }

  async function review(orderId, action) {
    if (action === "confirm" && !confirm("确认已核对欧意/链上到账、TRC20 网络、金额和 TXID？确认后会给用户余额入账。")) return;
    await api(`/api/admin/payment-channels-v106/usdt-orders/${encodeURIComponent(orderId)}/${action}`, {method:"POST", body:"{}"});
    await loadUsdtOrders();
  }

  function activateV106(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    document.querySelectorAll(".nav button").forEach((node) => node.classList.toggle("active", node === button));
    document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node === view));
    const title = document.getElementById("pageTitle");
    if (title) title.textContent = "支付配置";
    load().catch((error) => setStatus("payV106Status", `错误：${error.message}`));
  }

  button.addEventListener("click", activateV106, true);
  document.getElementById("payV106Save").onclick = () => save().catch((error) => setStatus("payV106Status", `错误：${error.message}`));
  document.getElementById("payV106TestZpay").onclick = () => test("zpay").catch((error) => setStatus("payV106ZpayStatus", `错误：${error.message}`));
  document.getElementById("payV106TestPaypal").onclick = () => test("paypal").catch((error) => setStatus("payV106PaypalStatus", `错误：${error.message}`));
})();
