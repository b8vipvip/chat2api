(() => {
  const section = document.getElementById("view-linux-workers");
  const tbody = document.getElementById("linuxWorkerRows");
  if (!section || !tbody || globalThis.__CHAT2API_LINUX_WORKER_PAIRING_UI_V22_18__) return;
  globalThis.__CHAT2API_LINUX_WORKER_PAIRING_UI_V22_18__ = true;

  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;"," ":" ",'"':"&quot;","'":"&#39;"})[char] || char);
  const request = async (path, options = {}) => {
    const {headers = {}, ...rest} = options;
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...rest,
      headers: {"Content-Type":"application/json", ...headers},
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
    return payload;
  };

  const style = document.createElement("style");
  style.textContent = `
    #view-linux-workers .table-wrap{border:1px solid #263244;border-radius:12px;overflow:auto;background:#07101f}
    #view-linux-workers table{min-width:1120px;width:100%;border-collapse:separate;border-spacing:0}
    #view-linux-workers th{position:sticky;top:0;z-index:1;background:#0d1727;color:#a9b7ca;font-size:12px;white-space:nowrap}
    #view-linux-workers th,#view-linux-workers td{padding:10px 11px;vertical-align:middle;border-bottom:1px solid rgba(71,85,105,.32)}
    #view-linux-workers tbody tr:hover td{background:rgba(30,41,59,.22)}
    #view-linux-workers .lw-pill{display:inline-flex;align-items:center;min-height:22px;padding:2px 8px;border:1px solid #334155;border-radius:999px;background:#101a2b;font-size:12px;white-space:nowrap}
    #view-linux-workers .lw-pill.good{border-color:#166534;background:rgba(20,83,45,.22)}
    #view-linux-workers .lw-pill.warn{border-color:#854d0e;background:rgba(113,63,18,.24)}
    #view-linux-workers .lw-pill.bad{border-color:#991b1b;background:rgba(127,29,29,.22)}
    #view-linux-workers .lw-muted{color:#7f8da3;font-size:12px}
    #view-linux-workers .lw-system{max-width:190px;line-height:1.35}
    #view-linux-workers .lw-actions{display:flex;flex-wrap:wrap;gap:6px;min-width:260px}
    #linuxWorkerPairingDialog input{box-sizing:border-box;width:100%;padding:10px 11px;border:1px solid #334155;border-radius:8px;background:#0b1322;color:#e5e7eb}
  `;
  document.head.appendChild(style);

  const table = tbody.closest("table");
  const headerRow = table?.querySelector("thead tr");
  if (headerRow) {
    headerRow.innerHTML = ["名称","状态","安装进度","安装命令","系统","网络","代理","ChatGPT","最后更新","操作"]
      .map(name => `<th>${name}</th>`).join("");
  }

  const pairingDialog = document.createElement("dialog");
  pairingDialog.id = "linuxWorkerPairingDialog";
  pairingDialog.style.cssText = "width:min(560px,calc(100vw - 32px));max-width:none;border:1px solid #334155;border-radius:12px;background:#0f172a;color:#e5e7eb;padding:0;box-shadow:0 24px 80px rgba(0,0,0,.6)";
  pairingDialog.innerHTML = `<div style="padding:20px">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
      <div><div style="font-size:18px;font-weight:700">Worker 配对码</div><div id="linuxWorkerPairingName" style="margin-top:4px;color:#94a3b8"></div></div>
      <button class="action" id="closeLinuxWorkerPairing" type="button">关闭</button>
    </div>
    <div id="linuxWorkerPairingCurrent" style="margin-top:14px;padding:12px;border:1px solid #334155;border-radius:9px;background:#020617;line-height:1.65;color:#cbd5e1">未设置配对码</div>
    <div style="margin-top:14px;color:#94a3b8;font-size:12px;line-height:1.6">粘贴中心后台已有的 Chrome 扩展配对码并保存。这里只保存安全的配对记录 ID，不会把明文配对码写入 Worker 配置。ChatGPT 登录成功后会自动把该配对记录关联到此 Worker 的扩展。</div>
    <input id="linuxWorkerPairingCode" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴配对码" style="margin-top:12px">
    <div id="linuxWorkerPairingResult" style="min-height:22px;margin-top:10px;color:#94a3b8"></div>
    <div style="display:flex;justify-content:flex-end;gap:9px;margin-top:10px"><button class="action danger" id="clearLinuxWorkerPairing" type="button">解除配置</button><button class="action good" id="saveLinuxWorkerPairing" type="button">保存配对码</button></div>
  </div>`;
  document.body.appendChild(pairingDialog);

  let rows = [];
  let rowsFetchedAt = 0;
  let proxyCatalog = [];
  let catalogFetchedAt = 0;
  let decorating = false;
  let selectedPairingWorkerId = "";
  let activeProxyWorkerId = "";
  let lastProxyLabelPersist = "";

  const pairingMeta = row => {
    const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    return meta.worker_pairing && typeof meta.worker_pairing === "object" ? meta.worker_pairing : {};
  };
  const bridge = row => {
    const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    return meta.bridge && typeof meta.bridge === "object" ? meta.bridge : {};
  };

  const fetchRows = async force => {
    if (!force && rows.length && Date.now() - rowsFetchedAt < 650) return rows;
    const payload = await request("/api/admin/linux-worker-installations");
    rows = Array.isArray(payload.data) ? payload.data : [];
    rowsFetchedAt = Date.now();
    return rows;
  };
  const fetchCatalog = async force => {
    if (!force && Date.now() - catalogFetchedAt < 30000) return proxyCatalog;
    try {
      const payload = await request("/api/admin/linux-worker-proxies");
      proxyCatalog = Array.isArray(payload.data) ? payload.data : [];
      catalogFetchedAt = Date.now();
    } catch (_) {}
    return proxyCatalog;
  };

  const parseTime = value => {
    if (!value) return null;
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date : null;
  };
  const beijingShort = value => {
    const date = parseTime(value);
    if (!date) return "-";
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone:"Asia/Shanghai", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false,
    }).formatToParts(date).reduce((acc, item) => (acc[item.type] = item.value, acc), {});
    return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  };
  const beijingFull = value => {
    const date = parseTime(value);
    if (!date) return "";
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone:"Asia/Shanghai", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false,
    }).format(date) + " 北京时间";
  };

  const isStale = row => {
    if (!row?.worker_id || !row.last_seen_at) return false;
    const date = parseTime(row.last_seen_at);
    return Boolean(date && Date.now() - date.getTime() > 45000);
  };
  const statusText = row => {
    if (!row) return ["未知", "warn"];
    if (row.worker_id && isStale(row)) return ["离线", "bad"];
    const state = String(row.worker_id ? row.status : row.install_state || row.status || "").toLowerCase();
    const map = {
      pending:["待安装","warn"], installing:["安装中","warn"], enrolling:["注册中","warn"],
      installed:["已安装","good"], ready:["运行正常","good"], waiting_proxy:["待配置代理","warn"],
      proxy_checking:["检测代理","warn"], waiting_login:["待登录","warn"], login_checking:["检测登录","warn"],
      degraded:["运行异常","bad"], offline:["离线","bad"], error:["错误","bad"], failed:["安装失败","bad"],
      disabled:["已停用","bad"], legacy:["已安装","good"], enrolled:["已注册","good"],
    };
    return map[state] || [state ? `状态：${state}` : "未知", "warn"];
  };
  const installProgressText = row => {
    if (!row || row.record_type !== "installation") return "-";
    const state = String(row.install_state || "").toLowerCase();
    if (state === "pending") return "等待执行安装命令";
    if (state === "installed") return "安装完成";
    if (state === "failed") return row.install_message || "安装失败";
    return row.install_message || row.install_stage || "安装中";
  };
  const systemText = row => {
    const os = String(row?.os_version || "").trim();
    const arch = String(row?.arch || "").trim();
    return [os, arch].filter(Boolean).join(" · ") || "-";
  };
  const countryText = code => ({US:"美国",JP:"日本",SG:"新加坡",KR:"韩国",GB:"英国",DE:"德国",FR:"法国",CA:"加拿大",AU:"澳大利亚",HK:"中国香港",TW:"中国台湾",CN:"中国大陆"})[String(code || "").toUpperCase()] || String(code || "").toUpperCase();
  const networkText = row => {
    const b = bridge(row);
    const state = String(b.network_probe_status || row?.network_status || "unknown").toLowerCase();
    const country = countryText(b.network_country_code || "");
    if (state === "external") return country ? `外网（${country}）` : "外网";
    if (state === "china-mainland") return "中国大陆网络";
    if (state === "offline") return "网络离线";
    if (state === "error" || state === "failed") return "检测失败";
    if (["ready","online","connected","reachable"].includes(state)) return "已联网";
    return "未检测";
  };
  const proxyText = row => {
    const status = String(row?.proxy_status || "").toLowerCase();
    const metadata = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    const summary = metadata.proxy_summary && typeof metadata.proxy_summary === "object" ? metadata.proxy_summary : {};
    if (["connected","ready"].includes(status)) {
      const name = String(summary.name || "").trim() || String(summary.server || "").trim() || String(summary.protocol || "代理").toUpperCase();
      return `已连接（${name}）`;
    }
    if (["error","failed","offline"].includes(status)) return "连接异常";
    if (["checking","testing"].includes(status)) return "检测中";
    return "未配置";
  };
  const chatgptText = row => {
    const b = bridge(row);
    const logged = String(row?.chatgpt_status || "").toLowerCase() === "ready" || (String(b.login_state || "").toLowerCase() === "ready" && b.composer_ready === true);
    return logged ? "已登录" : "未登录";
  };
  const pairingTitle = row => {
    const meta = pairingMeta(row);
    if (!meta.pairing_id) return "未配置配对码";
    const state = String(meta.status || "saved");
    const status = ({saved:"已保存",waiting_login:"等待 ChatGPT 登录",waiting_extension:"等待扩展",bound:"已绑定",error:"绑定失败"})[state] || state;
    return `${status}${meta.name ? ` · ${meta.name}` : ""}${meta.prefix ? ` · ${meta.prefix}…` : ""}`;
  };

  const setCell = (cell, html, title = "") => {
    if (!cell) return;
    if (cell.innerHTML !== html) cell.innerHTML = html;
    cell.title = title;
  };

  const decorate = async force => {
    if (decorating) return;
    decorating = true;
    try {
      await Promise.all([fetchRows(Boolean(force)), fetchCatalog(false)]);
      if (headerRow && headerRow.children.length !== 10) {
        headerRow.innerHTML = ["名称","状态","安装进度","安装命令","系统","网络","代理","ChatGPT","最后更新","操作"].map(name => `<th>${name}</th>`).join("");
      }
      const domRows = Array.from(tbody.querySelectorAll(":scope > tr"));
      if (domRows.length === 1 && domRows[0].children.length === 1) {
        domRows[0].children[0].colSpan = 10;
        return;
      }
      domRows.forEach((tr, index) => {
        const row = rows[index];
        if (!row) return;
        let cells = Array.from(tr.children);
        if (cells.length >= 12) {
          cells[9]?.remove();
          cells[8]?.remove();
          cells = Array.from(tr.children);
        }
        if (cells.length !== 10) return;

        const [label, tone] = statusText(row);
        setCell(cells[1], `<span class="lw-pill ${tone}">${esc(label)}</span>`);
        if (row.record_type === "installation" && !cells[2].querySelector("details")) setCell(cells[2], `<span class="lw-muted">${esc(installProgressText(row))}</span>`);
        cells[4].classList.add("lw-system");
        setCell(cells[4], esc(systemText(row)));
        setCell(cells[5], `<span class="lw-pill">${esc(networkText(row))}</span>`);
        const proxy = proxyText(row);
        setCell(cells[6], `<span class="lw-pill ${proxy.startsWith("已连接") ? "good" : proxy === "连接异常" ? "bad" : "warn"}">${esc(proxy)}</span>`);
        const chat = chatgptText(row);
        setCell(cells[7], `<span class="lw-pill ${chat === "已登录" ? "good" : "warn"}">${chat}</span>`);
        const updated = row.last_seen_at || row.install_updated_at || row.updated_at || row.created_at || row.install_created_at;
        setCell(cells[8], `<span class="lw-muted">${esc(beijingShort(updated))}</span>`, beijingFull(updated));
        cells[9].classList.add("lw-actions");
        if (row.worker_id && !cells[9].querySelector("[data-worker-pairing]")) {
          const button = document.createElement("button");
          button.className = "action";
          button.type = "button";
          button.textContent = "配对码";
          button.dataset.workerPairing = String(row.worker_id);
          button.dataset.workerName = String(row.name || row.hostname || row.worker_id);
          button.title = pairingTitle(row);
          cells[9].appendChild(button);
        } else if (row.worker_id) {
          const button = cells[9].querySelector("[data-worker-pairing]");
          if (button) button.title = pairingTitle(row);
        }
      });
    } catch (_) {
      // The original Worker UI remains usable if this presentation layer cannot refresh.
    } finally {
      decorating = false;
    }
  };

  const observer = new MutationObserver(() => {
    const first = tbody.querySelector(":scope > tr");
    if (first && first.children.length === 10 && (!rows.length || !first.querySelector("[data-login],[data-proxy],[data-copy-install]"))) return;
    setTimeout(() => decorate(false), 0);
  });
  observer.observe(tbody, {childList:true, subtree:false});

  const selectedRow = () => rows.find(row => String(row.worker_id || "") === selectedPairingWorkerId) || null;
  const renderPairingCurrent = row => {
    const node = document.getElementById("linuxWorkerPairingCurrent");
    const meta = pairingMeta(row);
    if (!meta.pairing_id) {
      node.innerHTML = "<b>当前：</b>未设置配对码";
      return;
    }
    const statusMap = {saved:"已保存，等待自动绑定",waiting_login:"等待 ChatGPT 登录",waiting_extension:"等待扩展连接",bound:"已绑定到本 Worker 扩展",error:"绑定失败"};
    const status = statusMap[String(meta.status || "saved")] || String(meta.status || "已保存");
    node.innerHTML = `<div><b>当前：</b>${esc(meta.name || "配对码")}</div><div><b>前缀：</b>${esc(meta.prefix || "-")}…</div><div><b>状态：</b>${esc(status)}</div>${meta.last_error ? `<div style="color:#fca5a5"><b>原因：</b>${esc(meta.last_error)}</div>` : ""}`;
  };
  const openPairing = async (workerId, workerName) => {
    selectedPairingWorkerId = workerId;
    document.getElementById("linuxWorkerPairingName").textContent = workerName || workerId;
    document.getElementById("linuxWorkerPairingCode").value = "";
    document.getElementById("linuxWorkerPairingResult").textContent = "";
    await fetchRows(true).catch(() => {});
    renderPairingCurrent(selectedRow());
    if (!pairingDialog.open) pairingDialog.showModal();
    document.getElementById("linuxWorkerPairingCode").focus();
  };
  const closePairing = () => {
    selectedPairingWorkerId = "";
    document.getElementById("linuxWorkerPairingCode").value = "";
    if (pairingDialog.open) pairingDialog.close();
  };

  document.getElementById("closeLinuxWorkerPairing").onclick = closePairing;
  pairingDialog.addEventListener("cancel", event => { event.preventDefault(); closePairing(); });
  document.getElementById("saveLinuxWorkerPairing").onclick = async () => {
    if (!selectedPairingWorkerId) return;
    const input = document.getElementById("linuxWorkerPairingCode");
    const result = document.getElementById("linuxWorkerPairingResult");
    const code = input.value.trim();
    if (!code) { result.textContent = "请先粘贴配对码。"; return; }
    result.textContent = "正在校验并保存…";
    try {
      const payload = await request(`/api/admin/linux-workers/${encodeURIComponent(selectedPairingWorkerId)}/pairing-code`, {method:"PUT", body:JSON.stringify({pairing_code:code})});
      input.value = "";
      result.textContent = payload?.pairing?.status === "bound" ? "保存成功，已自动绑定到当前 Worker 扩展。" : "保存成功；ChatGPT 登录并确认扩展在线后会自动绑定。";
      await fetchRows(true);
      renderPairingCurrent(selectedRow());
      await decorate(true);
    } catch (error) { result.textContent = error.message; }
  };
  document.getElementById("clearLinuxWorkerPairing").onclick = async () => {
    if (!selectedPairingWorkerId || !confirm("确定解除此 Worker 的配对码配置？")) return;
    const result = document.getElementById("linuxWorkerPairingResult");
    result.textContent = "正在解除…";
    try {
      await request(`/api/admin/linux-workers/${encodeURIComponent(selectedPairingWorkerId)}/pairing-code`, {method:"DELETE"});
      result.textContent = "已解除配对码配置。";
      await fetchRows(true);
      renderPairingCurrent(selectedRow());
      await decorate(true);
    } catch (error) { result.textContent = error.message; }
  };

  tbody.addEventListener("click", event => {
    const pairingButton = event.target.closest?.("[data-worker-pairing]");
    if (pairingButton) {
      event.preventDefault();
      event.stopImmediatePropagation();
      openPairing(pairingButton.dataset.workerPairing, pairingButton.dataset.workerName || pairingButton.dataset.workerPairing).catch(() => {});
      return;
    }
    const proxyButton = event.target.closest?.("[data-proxy]");
    if (proxyButton) activeProxyWorkerId = String(proxyButton.dataset.proxy || "");
  }, true);

  const proxyResult = document.getElementById("linuxProxyResult");
  if (proxyResult) {
    new MutationObserver(async () => {
      if (!activeProxyWorkerId || !String(proxyResult.textContent || "").includes("应用成功")) return;
      const select = document.getElementById("linuxSavedProxySelect");
      const option = select?.selectedOptions?.[0];
      const name = String(option?.textContent || "").split(" · ")[0].trim();
      if (!name || name === "暂无已保存代理") return;
      const key = `${activeProxyWorkerId}:${name}:${proxyResult.textContent}`;
      if (key === lastProxyLabelPersist) return;
      lastProxyLabelPersist = key;
      try {
        await request(`/api/admin/linux-workers/${encodeURIComponent(activeProxyWorkerId)}/proxy-label`, {method:"POST", body:JSON.stringify({name})});
        await fetchRows(true);
        await decorate(true);
      } catch (_) {}
    }).observe(proxyResult, {childList:true, characterData:true, subtree:true});
  }

  const refresh = document.getElementById("refreshLinuxWorkers");
  refresh?.addEventListener("click", () => setTimeout(() => decorate(true), 80));
  setInterval(() => {
    if (section.classList.contains("active") && !pairingDialog.open) decorate(false);
  }, 1200);
  decorate(true);
})();
