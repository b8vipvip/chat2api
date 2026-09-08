(() => {
  "use strict";
  if (window.__chat2apiPaymentsV106) return;
  window.__chat2apiPaymentsV106 = true;
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[ch]);
  const money = (cents) => `¥${(Number(cents || 0) / 100).toFixed(2)}`;
  let methods = [];

  async function api(path, options = {}) {
    const response = await fetch(path, {credentials:"same-origin", ...options, headers:{"Content-Type":"application/json", ...(options.headers || {})}});
    let payload = {};
    try { payload = await response.json(); } catch {}
    if (!response.ok) throw new Error(payload.detail || payload?.error?.message || `HTTP ${response.status}`);
    return payload;
  }

  async function refreshMethods() {
    const select = $("rechargeChannel");
    if (!select) return;
    const payload = await api("/api/user/payment-channels-v106");
    methods = payload.methods || [];
    select.innerHTML = methods.length
      ? methods.map((row) => `<option value="${esc(row.id)}">${esc(row.name)}${row.id === "usdt" ? " · TRC20" : ""}</option>`).join("")
      : `<option value="">暂无支付方式</option>`;
    const button = $("recharge");
    if (button) button.disabled = !methods.length;
    const notice = $("payNotice");
    if (notice) notice.textContent = methods.length ? `当前可用：${methods.map((row) => row.name).join("、")}` : "在线充值暂未启用，请联系服务管理员。";
  }

  function channelName(channel) {
    return ({alipay:"支付宝", wechat:"微信支付", paypal:"PayPal", usdt:"USDT (TRC20)"})[channel] || channel || "-";
  }

  async function rechargeV106() {
    const amount = Math.round(Number($("rechargeAmount")?.value || 0) * 100);
    const channel = $("rechargeChannel")?.value || "";
    if (!amount) return alert("请输入充值金额");
    if (!channel) return alert("当前没有可用支付方式");
    const payload = await api("/api/user/billing/recharge-v106", {method:"POST", body:JSON.stringify({amount_cents:amount, channel})});
    const checkout = payload.checkout || {};
    const order = payload.order || {};
    const parts = [`<div class="notice">订单已创建：${esc(money(order.amount_cents))} · ${esc(channelName(channel))}</div>`];
    if (channel === "paypal" && checkout.provider_amount) parts.push(`<p>PayPal 应付：<b>${esc(checkout.provider_amount)} ${esc(checkout.provider_currency || "USD")}</b></p>`);
    if (channel === "usdt") {
      parts.push(`<p><b>仅使用 ${esc(checkout.network || "TRON (TRC20)")} 网络。</b> 请勿通过 ERC20、BEP20 等其他网络转账。</p>`);
      parts.push(`<p>应付：<b>${esc(checkout.provider_amount)} USDT</b></p>`);
      parts.push(`<div class="codebox" style="word-break:break-all">${esc(checkout.address || "")}</div>`);
      parts.push(`<p><button class="action" id="copyUsdtAddress">复制收款地址</button>${checkout.address_link ? ` <a class="action" href="${esc(checkout.address_link)}" target="_blank" rel="noopener">打开地址</a>` : ""}</p>`);
      if (checkout.qr_image_url) parts.push(`<p><img src="${esc(checkout.qr_image_url)}" alt="USDT TRC20 收款二维码" style="max-width:260px;border-radius:12px"></p>`);
      parts.push(`<label class="field">转账完成后填写 TRON 交易哈希（TXID）<input id="usdtTxid" maxlength="64" placeholder="64 位交易哈希"></label><button class="action good" id="submitUsdtTxid">提交交易哈希等待确认</button><div class="muted" style="margin-top:8px">USDT 使用管理员配置的欧意 TRON (TRC20) 收款地址；提交 TXID 后由管理员核验到账再入账。</div>`);
    } else {
      if (checkout.qr_image_url) parts.push(`<p><img src="${esc(checkout.qr_image_url)}" alt="支付二维码" style="max-width:260px;border-radius:12px"></p>`);
      if (checkout.pay_url) parts.push(`<p><a class="action primary" href="${esc(checkout.pay_url)}" target="_blank" rel="noopener">前往支付</a></p>`);
      if (checkout.qr_payload && !checkout.qr_image_url) parts.push(`<div class="codebox">${esc(checkout.qr_payload)}</div>`);
    }
    const box = $("checkout");
    box.innerHTML = parts.join("");
    box.classList.remove("hidden");
    $("copyUsdtAddress")?.addEventListener("click", () => navigator.clipboard?.writeText(checkout.address || ""));
    $("submitUsdtTxid")?.addEventListener("click", async () => {
      const txid = $("usdtTxid")?.value.trim() || "";
      try {
        await api(`/api/user/payments/v106/usdt/${encodeURIComponent(order.order_id)}/txid`, {method:"POST", body:JSON.stringify({txid})});
        alert("交易哈希已提交，管理员确认到账后余额会更新。");
        await refreshOrders();
      } catch (error) { alert(error.message); }
    });
    await refreshOrders();
  }

  async function refreshOrders() {
    const body = $("ordersBody");
    if (!body) return;
    const payload = await api("/api/user/billing");
    const orders = payload.orders || [];
    body.innerHTML = orders.length ? orders.map((row) => `<tr><td>${esc(row.created_at ? new Date(row.created_at).toLocaleString("zh-CN", {hour12:false}) : "-")}</td><td>${esc(money(row.amount_cents))}</td><td>${esc(channelName(row.channel))}</td><td>${esc(({paid:"已支付",reviewing:"待核验",rejected:"未通过",pending:"待支付"})[row.status] || row.status || "-")}</td></tr>`).join("") : `<tr><td colspan="4" class="muted">暂无充值记录。</td></tr>`;
  }

  function installRechargeOverride() {
    const button = $("recharge");
    if (!button || button.dataset.paymentV106 === "1") return;
    button.dataset.paymentV106 = "1";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      rechargeV106().catch((error) => alert(error.message));
    }, true);
  }

  function scheduleRefresh() {
    setTimeout(() => { installRechargeOverride(); refreshMethods().catch(() => {}); refreshOrders().catch(() => {}); }, 80);
  }

  document.querySelector('[data-view="billing"]')?.addEventListener("click", scheduleRefresh, true);
  $("refresh")?.addEventListener("click", () => { if ((location.hash || "") === "#billing") scheduleRefresh(); }, true);
  window.addEventListener("hashchange", () => { if ((location.hash || "") === "#billing") scheduleRefresh(); });
  installRechargeOverride();
  if ((location.hash || "") === "#billing") scheduleRefresh();
})();
