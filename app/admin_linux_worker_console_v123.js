(() => {
  const KEY = "__CHAT2API_LINUX_WORKER_CONSOLE_V123__";
  if (globalThis[KEY]) return;

  const state = {
    revision: 123,
    rows: [],
    proxies: [],
    pairings: [],
    lastSignature: "",
    refreshing: false,
    health: new Map(),
    selectedProxyWorker: "",
  };
  globalThis[KEY] = state;

  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const api = async (path, options = {}) => {
    const {headers = {}, body, ...rest} = options;
    const response = await fetch(path, {
      credentials:"same-origin",
      cache:"no-store",
      ...rest,
      headers:{"Content-Type":"application/json", ...headers},
      ...(body === undefined ? {} : {body: typeof body === "string" ? body : JSON.stringify(body)}),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
    return payload;
  };

  const beijingShort = value => {
    if (!value) return "-";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "-";
    const p = new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false})
      .formatToParts(date).reduce((acc, item) => (acc[item.type] = item.value, acc), {});
    return `${p.month}-${p.day} ${p.hour}:${p.minute}`;
  };
  const bridge = row => {
    const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    return meta.bridge && typeof meta.bridge === "object" ? meta.bridge : {};
  };
  const proxySummary = row => {
    const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    return meta.proxy_summary && typeof meta.proxy_summary === "object" ? meta.proxy_summary : {};
  };
  const primaryWorker = row => Array.isArray(row?.device_workers) && row.device_workers.length ? row.device_workers[0] : row;
  const countryText = code => ({US:"美国",JP:"日本",SG:"新加坡",KR:"韩国",GB:"英国",DE:"德国",FR:"法国",CA:"加拿大",AU:"澳大利亚",HK:"中国香港",TW:"中国台湾",CN:"中国大陆"})[String(code || "").toUpperCase()] || String(code || "").toUpperCase();
  const networkText = row => {
    const b = bridge(row);
    const status = String(b.network_probe_status || row?.network_status || "unknown").toLowerCase();
    const country = countryText(b.network_country_code || "");
    if (status === "external") return country ? `外网（${country}）` : "外网";
    if (status === "china-mainland") return "中国大陆网络";
    if (status === "offline") return "网络离线";
    if (["error","failed"].includes(status)) return "检测失败";
    if (["ready","online","connected","reachable"].includes(status)) return "已联网";
    return "未检测";
  };
  const proxyName = row => {
    const p = proxySummary(row);
    return String(p.name || p.server || p.protocol || "代理").trim();
  };
  const proxyConfigured = row => {
    const p = proxySummary(row);
    return ["connected","ready"].includes(String(row?.proxy_status || "").toLowerCase()) || Boolean(p.name || p.server || p.protocol);
  };
  const statusView = row => {
    const worker = primaryWorker(row);
    if (!worker?.worker_id) {
      const install = String(row?.install_state || "pending").toLowerCase();
      if (install === "failed") return ["安装失败","bad"];
      if (install === "installing" || install === "enrolling") return ["安装中","warn"];
      return ["待安装","warn"];
    }
    if (worker.revoked_at) return ["已禁用","bad"];
    const last = worker.last_seen_at ? new Date(worker.last_seen_at).getTime() : 0;
    if (last && Date.now() - last > 45000) return ["离线","bad"];
    const value = String(worker.status || "").toLowerCase();
    const map = {
      ready:["运行正常","good"], waiting_proxy:["待配置代理","warn"], proxy_checking:["检测代理","warn"],
      waiting_login:["待登录","warn"], login_checking:["检测登录","warn"], degraded:["运行异常","bad"],
      offline:["离线","bad"], error:["错误","bad"], enrolling:["注册中","warn"],
    };
    return map[value] || [value || "未知","warn"];
  };
  const chatgptText = row => {
    const b = bridge(row);
    return String(row?.chatgpt_status || "").toLowerCase() === "ready" || (String(b.login_state || "").toLowerCase() === "ready" && b.composer_ready === true)
      ? "已登录" : "未登录";
  };
  const systemText = row => {
    const value = String(row?.os_version || "").trim();
    if (/ubuntu/i.test(value) || String(row?.platform || "").toLowerCase() === "linux") return "Ubuntu";
    return value ? value.split(/\s+/)[0] : "-";
  };
  const progressText = row => {
    const stateValue = String(row?.install_state || "").toLowerCase();
    if (stateValue === "installed") return "完成";
    if (stateValue === "failed") return "失败";
    if (stateValue === "installing" || stateValue === "enrolling") return row.install_message || "安装中";
    if (stateValue === "disabled") return "已停用";
    return row.install_message || "-";
  };

  const parseHealth = result => {
    const probes = Array.isArray(result?.probes) ? result.probes : [];
    const byName = name => probes.find(item => String(item?.name || "") === name) || null;
    const network = byName("network_access");
    const chat = ["chatgpt_home","conversation_route","sentinel_route"].map(byName);
    const known = chat.every(Boolean);
    const networkReady = network ? network.ok === true : Boolean(result?.ok);
    const chatReady = known ? chat.every(item => item?.ok === true) : Boolean(result?.generation_backend_ready ?? result?.ok);
    const latency = Number(network?.total_s || byName("chatgpt_home")?.total_s || 0);
    return {
      networkReady,
      chatReady,
      latencyMs:Number.isFinite(latency) && latency > 0 ? Math.round(latency * 1000) : 0,
      error:String(result?.error || ""),
    };
  };

  function init() {
    const section = document.getElementById("view-linux-workers");
    const legacyBody = document.getElementById("linuxWorkerRows");
    const toolbar = section?.querySelector(".toolbar");
    if (!section || !legacyBody || !toolbar) { setTimeout(init, 100); return; }
    if (section.dataset.consoleV123 === "1") return;
    section.dataset.consoleV123 = "1";

    const style = document.createElement("style");
    style.textContent = `
      #linuxDeviceTableV123{min-width:1120px;width:100%;border-collapse:separate;border-spacing:0}
      #linuxDeviceTableV123 th,#linuxDeviceTableV123 td{padding:10px 11px;vertical-align:middle;border-bottom:1px solid rgba(71,85,105,.32)}
      #linuxDeviceTableV123 th{white-space:nowrap;color:#a9b7ca;font-size:12px;text-align:left}
      #linuxDeviceTableV123 .lw-pill{display:inline-flex;align-items:center;min-height:22px;padding:2px 8px;border:1px solid #334155;border-radius:999px;background:#101a2b;font-size:12px;white-space:nowrap}
      #linuxDeviceTableV123 .lw-pill.good{border-color:#166534;background:rgba(20,83,45,.22)}
      #linuxDeviceTableV123 .lw-pill.warn{border-color:#854d0e;background:rgba(113,63,18,.24)}
      #linuxDeviceTableV123 .lw-pill.bad{border-color:#991b1b;background:rgba(127,29,29,.22)}
      #linuxDeviceTableV123 .lw-muted{color:#94a3b8;font-size:12px;line-height:1.35}
      #linuxDeviceTableV123 .lw-proxy-main{display:flex;align-items:center;gap:5px;min-height:24px;white-space:nowrap}
      #linuxDeviceTableV123 .lw-proxy-sub{margin-top:2px;color:#94a3b8;font-size:12px;line-height:16px;white-space:nowrap}
      #linuxDeviceTableV123 .lw-proxy-gear{display:inline-grid;place-items:center;width:24px;height:24px;padding:0;border:1px solid #334155;border-radius:6px;background:#17233a;color:#cbd5e1;cursor:pointer}
      #linuxDeviceTableV123 .lw-actions{display:flex;gap:6px;flex-wrap:wrap;min-width:330px}
      #linuxProxyQuickV123{position:fixed;z-index:10050;width:330px;border:1px solid #334155;border-radius:10px;background:#0b1322;color:#e5e7eb;padding:12px;box-shadow:0 18px 60px rgba(0,0,0,.65)}
      #linuxProxyQuickV123 select{width:100%;box-sizing:border-box;padding:8px;border:1px solid #334155;border-radius:7px;background:#020617;color:#e5e7eb}
      #linuxDeviceSetupV123 select{width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid #334155;border-radius:8px;background:#0b1322;color:#e5e7eb}
    `;
    document.head.appendChild(style);

    // The compatibility table may still be repainted by historical patches, but
    // only this v123 table is visible. This removes competing DOM owners and the
    // visible flicker without breaking their delegated buttons/dialogs.
    const legacyTable = legacyBody.closest("table");
    if (legacyTable) legacyTable.style.display = "none";

    const wrap = document.createElement("div");
    wrap.style.cssText = "overflow:auto;margin-top:6px";
    wrap.innerHTML = `<table id="linuxDeviceTableV123"><thead><tr>
      <th>设备名称</th><th>状态</th><th>安装进度</th><th>安装命令</th><th>系统</th><th>代理</th><th>ChatGPT</th><th>最后更新</th><th>操作</th>
    </tr></thead><tbody id="linuxDeviceRowsV123"></tbody></table>`;
    legacyTable?.parentNode?.insertBefore(wrap, legacyTable.nextSibling);

    const nameInput = document.getElementById("linuxWorkerName");
    if (nameInput) nameInput.style.display = "none";
    const createOld = document.getElementById("createLinuxWorker");
    let createButton = createOld;
    if (createOld) {
      createButton = createOld.cloneNode(true);
      createButton.textContent = "新增设备";
      createOld.replaceWith(createButton);
    }
    const proxyManager = document.getElementById("manageLinuxProxies");
    if (proxyManager) proxyManager.textContent = "代理管理";

    const pairingTable = document.getElementById("pairingBody")?.closest("table");
    const renamePairingHeader = () => {
      const first = pairingTable?.querySelector("thead th:first-child");
      if (first) first.textContent = "设备名称";
    };
    renamePairingHeader();
    const baseShow = globalThis.show;
    if (typeof baseShow === "function" && !baseShow.__chat2apiLinuxV123) {
      const wrapped = async (...args) => {
        const result = await baseShow(...args);
        if (String(args[0] || "") === "extensions") setTimeout(renamePairingHeader, 0);
        return result;
      };
      wrapped.__chat2apiLinuxV123 = true;
      globalThis.show = wrapped;
    }

    const setupDialog = document.createElement("dialog");
    setupDialog.id = "linuxDeviceSetupV123";
    setupDialog.style.cssText = "width:min(580px,calc(100vw - 28px));max-width:none;border:1px solid #334155;border-radius:12px;background:#0f172a;color:#e5e7eb;padding:0;box-shadow:0 24px 90px rgba(0,0,0,.65)";
    setupDialog.innerHTML = `<div style="padding:20px">
      <div style="display:flex;justify-content:space-between;gap:12px"><div><div style="font-size:18px;font-weight:700">新增 Linux 设备</div><div class="lw-muted" style="margin-top:5px">先选择配对码和代理，再生成安装命令。设备名称直接使用配对码的设备名称。</div></div><button class="action" id="closeLinuxDeviceSetupV123">关闭</button></div>
      <label style="display:block;margin-top:16px">配对码 / 设备名称<select id="linuxDevicePairingV123" style="margin-top:6px"></select></label>
      <label style="display:block;margin-top:13px">代理<select id="linuxDeviceProxyV123" style="margin-top:6px"></select></label>
      <div id="linuxDeviceSetupResultV123" class="lw-muted" style="min-height:22px;margin-top:12px"></div>
      <div id="linuxDeviceSetupCommandV123" style="display:none;margin-top:10px;padding:11px;border:1px solid #334155;border-radius:8px;background:#020617"><code id="linuxDeviceSetupCommandTextV123" style="word-break:break-all;white-space:normal;line-height:1.55"></code><div style="margin-top:8px"><button class="action" id="copyLinuxDeviceSetupV123">复制命令</button></div></div>
      <div style="display:flex;justify-content:flex-end;margin-top:14px"><button class="action good" id="createLinuxDeviceSetupV123">生成安装命令</button></div>
    </div>`;
    document.body.appendChild(setupDialog);

    const quick = document.createElement("div");
    quick.id = "linuxProxyQuickV123";
    quick.hidden = true;
    quick.innerHTML = `<div style="font-weight:700">代理设置</div><select id="linuxProxyQuickSelectV123" style="margin-top:9px"></select><div style="display:flex;gap:8px;margin-top:9px"><button class="action good" id="linuxProxyQuickApplyV123">应用</button><button class="action" id="linuxProxyQuickTestV123">测试</button></div><div id="linuxProxyQuickResultV123" class="lw-muted" style="margin-top:8px;min-height:18px"></div>`;
    document.body.appendChild(quick);

    const visibleBody = document.getElementById("linuxDeviceRowsV123");
    const setupPairing = document.getElementById("linuxDevicePairingV123");
    const setupProxy = document.getElementById("linuxDeviceProxyV123");
    const quickSelect = document.getElementById("linuxProxyQuickSelectV123");
    const quickResult = document.getElementById("linuxProxyQuickResultV123");

    const loadOptions = async () => {
      const options = await api("/api/admin/linux-device-setup-options");
      state.pairings = Array.isArray(options.pairing_codes) ? options.pairing_codes : [];
      const catalog = await api("/api/admin/linux-worker-proxies");
      state.proxies = Array.isArray(catalog.data) ? catalog.data : [];
      const available = state.pairings.filter(item => item.enabled && !item.paired);
      setupPairing.innerHTML = available.length
        ? available.map(item => `<option value="${esc(item.pairing_id)}">${esc(item.device_name || "未命名设备")} · ${esc(item.prefix || "")}</option>`).join("")
        : '<option value="">没有可用于新设备的未配对配对码</option>';
      setupProxy.innerHTML = state.proxies.length
        ? state.proxies.map(item => `<option value="${esc(item.proxy_id)}">${esc(item.name || item.proxy_id)}</option>`).join("")
        : '<option value="">代理管理中还没有代理</option>';
      quickSelect.innerHTML = state.proxies.map(item => `<option value="${esc(item.proxy_id)}">${esc(item.name || item.proxy_id)}</option>`).join("");
    };

    const relayLegacyAction = (rowIndex, label) => {
      const tr = legacyBody.querySelectorAll(":scope > tr")[rowIndex];
      if (!tr) return false;
      const button = [...tr.querySelectorAll("button")].find(item => String(item.textContent || "").trim() === label || String(item.textContent || "").trim().startsWith(label + " "));
      if (!button || button.disabled) return false;
      button.click();
      return true;
    };

    const proxyView = row => {
      const worker = primaryWorker(row);
      if (!worker?.worker_id) return {main:"-",sub:"等待设备安装",tone:"warn"};
      const configured = proxyConfigured(worker);
      const name = configured ? proxyName(worker) : "直连";
      const network = networkText(worker);
      const health = state.health.get(String(worker.worker_id));
      if (!configured) return {main:`${name} · ${network}`,sub:"代理未配置",tone:"warn"};
      if (!health) return {main:`${name} · ${network}`,sub:"点击 ⚙ 可切换代理或测试当前代理",tone:"warn"};
      const n = health.networkReady ? "网络正常" : "网络异常";
      const g = health.chatReady ? "GPT正常" : "GPT异常";
      const l = health.latencyMs ? `${health.latencyMs} ms` : "--";
      return {main:`${name} · ${network}`,sub:`${n} · ${g} · 延迟 ${l}`,tone:health.networkReady && health.chatReady ? "good" : "bad"};
    };

    const render = () => {
      const signature = JSON.stringify(state.rows.map(row => {
        const worker = primaryWorker(row) || {};
        const p = proxySummary(worker);
        const b = bridge(worker);
        return [row.device_name,row.install_state,row.install_message,worker.worker_id,worker.status,worker.proxy_status,p.name,p.server,b.network_probe_status,b.network_country_code,worker.chatgpt_status,worker.last_seen_at,row.install_updated_at];
      }));
      if (signature === state.lastSignature && visibleBody.children.length) return;
      state.lastSignature = signature;
      visibleBody.innerHTML = state.rows.map((row, index) => {
        const worker = primaryWorker(row);
        const [status, tone] = statusView(row);
        const proxy = proxyView(row);
        const workerId = String(worker?.worker_id || "");
        const chat = workerId ? chatgptText(worker) : "-";
        const updated = worker?.last_seen_at || row.install_updated_at || row.updated_at || row.install_created_at;
        const installButton = row.install_command && !workerId ? `<button class="action" data-v123-copy-command="${index}">复制</button>` : "-";
        let actions = "";
        if (workerId) {
          const disabled = Boolean(worker.revoked_at) || String(worker.status || "") === "offline";
          actions = `<button class="action" data-v123-relay="更新" data-row="${index}">更新</button><button class="action" data-v123-relay="初始化" data-row="${index}">初始化</button><button class="action" data-v123-rename="${index}">改名</button><button class="action" data-v123-relay="登录" data-row="${index}">登录</button><button class="action ${disabled ? "good" : "danger"}" data-v123-relay="${disabled ? "启用" : "禁用"}" data-row="${index}">${disabled ? "启用" : "禁用"}</button><button class="action" data-v123-relay="诊断日志" data-row="${index}">诊断日志</button><button class="action danger" data-v123-relay="删除 Worker" data-row="${index}">删除 Worker</button>`;
        } else if (row.install_id) {
          actions = `<button class="action danger" data-v123-delete-command="${esc(row.install_id)}">删除命令</button>`;
        }
        return `<tr>
          <td><b>${esc(row.device_name || row.name || "Linux 设备")}</b></td>
          <td><span class="lw-pill ${tone}">${esc(status)}</span></td>
          <td class="lw-muted">${esc(progressText(row))}</td>
          <td>${installButton}</td>
          <td>${esc(systemText(worker || row))}</td>
          <td><div class="lw-proxy-main"><span class="lw-pill ${proxy.tone}">${esc(proxy.main)}</span>${workerId ? `<button class="lw-proxy-gear" data-v123-proxy-worker="${esc(workerId)}" data-row="${index}" title="代理设置">⚙</button>` : ""}</div><div class="lw-proxy-sub">${esc(proxy.sub)}</div></td>
          <td>${esc(chat)}</td>
          <td class="lw-muted">${esc(beijingShort(updated))}</td>
          <td><div class="lw-actions">${actions}</div></td>
        </tr>`;
      }).join("") || '<tr><td colspan="9" class="lw-muted">暂无 Linux 设备。</td></tr>';
    };

    const refresh = async force => {
      if (state.refreshing) return;
      state.refreshing = true;
      try {
        const payload = await api("/api/admin/linux-worker-installations");
        state.rows = Array.isArray(payload.data) ? payload.data : [];
        if (force) state.lastSignature = "";
        render();
      } catch (_) {
      } finally { state.refreshing = false; }
    };

    createButton?.addEventListener("click", async event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      document.getElementById("linuxDeviceSetupResultV123").textContent = "正在读取配对码和代理…";
      document.getElementById("linuxDeviceSetupCommandV123").style.display = "none";
      try {
        await loadOptions();
        document.getElementById("linuxDeviceSetupResultV123").textContent = "";
        setupDialog.showModal();
      } catch (error) { alert(error.message); }
    }, true);
    document.getElementById("closeLinuxDeviceSetupV123").onclick = () => setupDialog.close();
    document.getElementById("createLinuxDeviceSetupV123").onclick = async () => {
      const result = document.getElementById("linuxDeviceSetupResultV123");
      if (!setupPairing.value) { result.textContent = "请先在 Worker管理 创建一个未配对的配对码。"; return; }
      if (!setupProxy.value) { result.textContent = "请先在代理管理中添加代理。"; return; }
      result.textContent = "正在生成安装命令…";
      try {
        const payload = await api("/api/admin/linux-device-installations", {method:"POST",body:{pairing_id:setupPairing.value,proxy_id:setupProxy.value}});
        const text = document.getElementById("linuxDeviceSetupCommandTextV123");
        text.textContent = payload.install_command || "";
        document.getElementById("linuxDeviceSetupCommandV123").style.display = "block";
        result.textContent = `设备「${payload.device_name}」已预留，安装后会自动应用代理「${payload.proxy_name}」。`;
        await refresh(true);
      } catch (error) { result.textContent = error.message; }
    };
    document.getElementById("copyLinuxDeviceSetupV123").onclick = async () => {
      const text = document.getElementById("linuxDeviceSetupCommandTextV123").textContent || "";
      if (text) await navigator.clipboard.writeText(text);
      document.getElementById("linuxDeviceSetupResultV123").textContent = "安装命令已复制";
    };

    visibleBody.addEventListener("click", async event => {
      const copy = event.target.closest?.("[data-v123-copy-command]");
      if (copy) {
        const row = state.rows[Number(copy.dataset.v123CopyCommand)];
        if (row?.install_command) await navigator.clipboard.writeText(row.install_command);
        return;
      }
      const del = event.target.closest?.("[data-v123-delete-command]");
      if (del) {
        if (!confirm("确定删除这条未执行的安装命令？")) return;
        try { await api(`/api/admin/linux-worker-installations/${encodeURIComponent(del.dataset.v123DeleteCommand)}`, {method:"DELETE"}); await refresh(true); } catch (error) { alert(error.message); }
        return;
      }
      const rename = event.target.closest?.("[data-v123-rename]");
      if (rename) {
        const row = state.rows[Number(rename.dataset.v123Rename)];
        const pairingId = String(row?.device_pairing_id || "");
        if (!pairingId) { alert("此设备还没有关联配对码，不能从设备列表改名。"); return; }
        const name = prompt("设备名称", String(row.device_name || row.name || ""));
        if (!name?.trim()) return;
        try { await api(`/api/admin/pairing-codes/${encodeURIComponent(pairingId)}/name`, {method:"PATCH",body:{name:name.trim()}}); await refresh(true); } catch (error) { alert(error.message); }
        return;
      }
      const relay = event.target.closest?.("[data-v123-relay]");
      if (relay) {
        if (!relayLegacyAction(Number(relay.dataset.row), String(relay.dataset.v123Relay || ""))) alert(`当前操作「${relay.dataset.v123Relay}」暂不可用，请刷新后重试。`);
        return;
      }
      const gear = event.target.closest?.("[data-v123-proxy-worker]");
      if (gear) {
        event.stopPropagation();
        state.selectedProxyWorker = String(gear.dataset.v123ProxyWorker || "");
        try { await loadOptions(); } catch (error) { alert(error.message); return; }
        const row = state.rows[Number(gear.dataset.row)];
        const current = proxyName(primaryWorker(row));
        const matching = state.proxies.find(item => String(item.name || "") === current);
        if (matching) quickSelect.value = matching.proxy_id;
        const rect = gear.getBoundingClientRect();
        quick.style.left = `${Math.min(window.innerWidth - 345, Math.max(8, rect.left))}px`;
        quick.style.top = `${Math.min(window.innerHeight - 180, rect.bottom + 6)}px`;
        quickResult.textContent = "";
        quick.hidden = false;
      }
    }, true);

    document.getElementById("linuxProxyQuickApplyV123").onclick = async () => {
      const workerId = state.selectedProxyWorker;
      const proxy = state.proxies.find(item => String(item.proxy_id || "") === quickSelect.value);
      if (!workerId || !proxy) return;
      quickResult.textContent = `正在应用 ${proxy.name}…`;
      try {
        await api(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/proxy`, {method:"POST",body:{share_link:proxy.share_link}});
        await api(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/proxy-label`, {method:"POST",body:{name:proxy.name}}).catch(() => null);
        state.health.delete(workerId);
        quickResult.textContent = "代理已应用，Chrome 已按新代理重启。";
        await refresh(true);
      } catch (error) { quickResult.textContent = error.message; }
    };
    document.getElementById("linuxProxyQuickTestV123").onclick = async () => {
      const workerId = state.selectedProxyWorker;
      if (!workerId) return;
      quickResult.textContent = "正在测试当前代理网络与 ChatGPT 链路…";
      try {
        const payload = await api(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/commands`, {method:"POST",body:{command:"test_proxy",arguments:{},wait:true,timeout_seconds:35}});
        const health = parseHealth(payload.result || {});
        state.health.set(workerId, health);
        state.lastSignature = "";
        render();
        quickResult.textContent = health.networkReady && health.chatReady ? `测试通过${health.latencyMs ? ` · ${health.latencyMs} ms` : ""}` : (health.error || "测试未通过");
      } catch (error) { quickResult.textContent = error.message; }
    };
    document.addEventListener("click", event => {
      if (!quick.hidden && !quick.contains(event.target) && !event.target.closest?.("[data-v123-proxy-worker]")) quick.hidden = true;
    });

    document.getElementById("refreshLinuxWorkers")?.addEventListener("click", () => setTimeout(() => refresh(true), 80));
    setInterval(() => {
      if (section.classList.contains("active") && !setupDialog.open && quick.hidden) refresh(false);
    }, 2000);
    refresh(true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once:true});
  else init();
})();
