(() => {
  const init = () => {
    const section = document.getElementById("view-linux-workers");
    const tbody = document.getElementById("linuxWorkerRows");
    if (!section || !tbody) {
      setTimeout(init, 120);
      return;
    }
    if (globalThis.__CHAT2API_LINUX_WORKER_STABLE_TABLE_V22_19__) return;
    globalThis.__CHAT2API_LINUX_WORKER_STABLE_TABLE_V22_19__ = true;

    const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
    const request = async (path, options = {}) => {
      const {headers = {}, ...rest} = options;
      const response = await fetch(path, {
        credentials:"same-origin",
        cache:"no-store",
        ...rest,
        headers:{"Content-Type":"application/json", ...headers},
      });
      let payload = {};
      try { payload = await response.json(); } catch (_) {}
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
      return payload;
    };

    const style = document.createElement("style");
    style.textContent = `
      #view-linux-workers table{min-width:1080px;width:100%;border-collapse:separate;border-spacing:0}
      #view-linux-workers th,#view-linux-workers td{padding:10px 11px;vertical-align:middle;border-bottom:1px solid rgba(71,85,105,.32)}
      #view-linux-workers th{white-space:nowrap;color:#a9b7ca;font-size:12px}
      #view-linux-workers th:nth-child(9),#view-linux-workers td:nth-child(9),
      #view-linux-workers th:nth-child(10),#view-linux-workers td:nth-child(10){display:none!important}
      #view-linux-workers tbody tr:hover td{background:rgba(30,41,59,.22)}
      #view-linux-workers .lw-pill{display:inline-flex;align-items:center;min-height:22px;padding:2px 8px;border:1px solid #334155;border-radius:999px;background:#101a2b;font-size:12px;white-space:nowrap}
      #view-linux-workers .lw-pill.good{border-color:#166534;background:rgba(20,83,45,.22)}
      #view-linux-workers .lw-pill.warn{border-color:#854d0e;background:rgba(113,63,18,.24)}
      #view-linux-workers .lw-pill.bad{border-color:#991b1b;background:rgba(127,29,29,.22)}
      #view-linux-workers .lw-muted{color:#7f8da3;font-size:12px}
      #view-linux-workers .lw-actions{display:flex;flex-wrap:wrap;gap:6px;min-width:300px}
      #linuxWorkerPairingDialogV2219 input{box-sizing:border-box;width:100%;padding:10px 11px;border:1px solid #334155;border-radius:8px;background:#0b1322;color:#e5e7eb}
    `;
    document.head.appendChild(style);

    const table = tbody.closest("table");
    const header = table?.querySelector("thead tr");
    const headers = ["名称","状态","安装进度","安装命令","系统","网络","代理","ChatGPT","Chrome Bridge","备用窗口","最后更新","操作"];
    if (header && header.children.length >= 12) {
      Array.from(header.children).forEach((cell, index) => { if (headers[index]) cell.textContent = headers[index]; });
    }

    const pairingDialog = document.createElement("dialog");
    pairingDialog.id = "linuxWorkerPairingDialogV2219";
    pairingDialog.style.cssText = "width:min(560px,calc(100vw - 32px));max-width:none;border:1px solid #334155;border-radius:12px;background:#0f172a;color:#e5e7eb;padding:0;box-shadow:0 24px 80px rgba(0,0,0,.6)";
    pairingDialog.innerHTML = `<div style="padding:20px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
        <div><div style="font-size:18px;font-weight:700">Worker 配对码</div><div id="linuxWorkerPairingNameV2219" style="margin-top:4px;color:#94a3b8"></div></div>
        <button class="action" id="closeLinuxWorkerPairingV2219" type="button">关闭</button>
      </div>
      <div id="linuxWorkerPairingCurrentV2219" style="margin-top:14px;padding:12px;border:1px solid #334155;border-radius:9px;background:#020617;line-height:1.65;color:#cbd5e1">未设置配对码</div>
      <div style="margin-top:14px;color:#94a3b8;font-size:12px;line-height:1.6">粘贴已有扩展配对码并保存。中心只在校验时使用明文，Worker 记录只保留安全的配对引用；ChatGPT 登录且扩展在线后自动完成绑定。</div>
      <input id="linuxWorkerPairingCodeV2219" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴配对码" style="margin-top:12px">
      <div id="linuxWorkerPairingResultV2219" style="min-height:22px;margin-top:10px;color:#94a3b8"></div>
      <div style="display:flex;justify-content:flex-end;gap:9px;margin-top:10px"><button class="action danger" id="clearLinuxWorkerPairingV2219" type="button">解除配置</button><button class="action good" id="saveLinuxWorkerPairingV2219" type="button">保存配对码</button></div>
    </div>`;
    document.body.appendChild(pairingDialog);

    let rows = [];
    let selectedWorkerId = "";
    let refreshing = false;

    const bridge = row => {
      const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
      return meta.bridge && typeof meta.bridge === "object" ? meta.bridge : {};
    };
    const pairingMeta = row => {
      const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
      return meta.worker_pairing && typeof meta.worker_pairing === "object" ? meta.worker_pairing : {};
    };
    const parseTime = value => {
      if (!value) return null;
      const date = new Date(value);
      return Number.isFinite(date.getTime()) ? date : null;
    };
    const beijingShort = value => {
      const date = parseTime(value);
      if (!date) return "-";
      const p = new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false})
        .formatToParts(date).reduce((acc, item) => (acc[item.type] = item.value, acc), {});
      return `${p.month}-${p.day} ${p.hour}:${p.minute}`;
    };
    const beijingFull = value => {
      const date = parseTime(value);
      if (!date) return "";
      return new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(date) + " 北京时间";
    };
    const stageNames = {
      waiting:"等待安装",
      created:"已生成安装命令",
      "system-check":"系统环境检查",
      cleanup:"清理安装残留",
      packages:"安装基础依赖",
      "worker-bundle":"更新 Worker 组件",
      python:"安装 Python 运行环境",
      xray:"安装 Xray Core",
      enrollment:"注册 Worker 身份",
      systemd:"启动 Worker 服务",
      health:"检查 Worker 健康状态",
      complete:"安装完成",
      failed:"安装失败",
      error:"安装错误",
      disabled:"已停用",
    };
    const stageName = value => stageNames[String(value || "").toLowerCase()] || String(value || "-");
    const isStale = row => {
      if (!row?.worker_id || !row.last_seen_at) return false;
      const date = parseTime(row.last_seen_at);
      return Boolean(date && Date.now() - date.getTime() > 45000);
    };
    const statusText = row => {
      if (!row) return ["未知","warn"];
      if (row.revoked_at) return ["已禁用","bad"];
      if (row.worker_id && isStale(row)) return ["离线","bad"];
      const state = String(row.worker_id ? row.status : row.install_state || row.status || "").toLowerCase();
      const map = {
        pending:["待安装","warn"], installing:["安装中","warn"], enrolling:["注册中","warn"], installed:["已安装","good"],
        ready:["运行正常","good"], waiting_proxy:["待配置代理","warn"], proxy_checking:["检测代理","warn"], waiting_login:["待登录","warn"],
        login_checking:["检测登录","warn"], degraded:["运行异常","bad"], offline:["离线","bad"], error:["错误","bad"],
        failed:["安装失败","bad"], disabled:["已停用","bad"], legacy:["已安装","good"], enrolled:["已注册","good"],
      };
      return map[state] || [state ? `状态：${state}` : "未知","warn"];
    };
    const systemText = row => {
      const os = String(row?.os_version || "").trim();
      const platform = String(row?.platform || "").toLowerCase();
      if (/ubuntu/i.test(os) || platform === "linux") return "Ubuntu";
      return os ? os.split(/\s+/)[0] : "-";
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
      const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
      const summary = meta.proxy_summary && typeof meta.proxy_summary === "object" ? meta.proxy_summary : {};
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
    const progressHtml = row => {
      if (!row || row.record_type !== "installation") return "-";
      const state = String(row.install_state || "").toLowerCase();
      const summary = state === "installed" ? "完成" : state === "failed" ? "失败" : state === "pending" ? "待安装" : state === "disabled" ? "已停用" : "安装中";
      const history = Array.isArray(row.install_history) ? row.install_history.slice(-12).reverse() : [];
      if (!history.length) return `<span class="lw-muted">${summary}</span>`;
      const details = history.map(item => {
        const at = beijingShort(item.at);
        const stage = stageName(item.stage);
        const message = String(item.message || "").trim();
        return `<div>${at ? `<code>${esc(at)}</code> ` : ""}${esc(stage)}${message ? ` · ${esc(message)}` : ""}</div>`;
      }).join("");
      return `<details><summary>${summary}</summary><div style="min-width:300px;max-width:560px;white-space:normal;line-height:1.55;margin-top:6px">${details}</div></details>`;
    };
    const pairingTitle = row => {
      const meta = pairingMeta(row);
      if (!meta.pairing_id) return "未配置配对码";
      const state = ({saved:"已保存",waiting_login:"等待登录",waiting_extension:"等待扩展",bound:"已绑定",error:"绑定失败"})[String(meta.status || "saved")] || String(meta.status || "已保存");
      return `${state}${meta.name ? ` · ${meta.name}` : ""}${meta.prefix ? ` · ${meta.prefix}…` : ""}`;
    };
    const setCell = (cell, html, title = "") => {
      if (!cell) return;
      if (cell.innerHTML !== html) cell.innerHTML = html;
      cell.title = title;
    };

    const paint = () => {
      const domRows = Array.from(tbody.querySelectorAll(":scope > tr"));
      if (domRows.length === 1 && domRows[0].children.length === 1) {
        domRows[0].children[0].colSpan = 10;
        return;
      }
      domRows.forEach((tr, index) => {
        const row = rows[index];
        if (!row) return;
        const cells = Array.from(tr.children);
        if (cells.length < 12) return;
        const [status, tone] = statusText(row);
        setCell(cells[1], `<span class="lw-pill ${tone}">${esc(status)}</span>`);
        setCell(cells[2], progressHtml(row));
        setCell(cells[4], esc(systemText(row)));
        const network = networkText(row);
        setCell(cells[5], `<span class="lw-pill">${esc(network)}</span>`);
        const proxy = proxyText(row);
        setCell(cells[6], `<span class="lw-pill ${proxy.startsWith("已连接") ? "good" : proxy === "连接异常" ? "bad" : "warn"}">${esc(proxy)}</span>`);
        const chat = chatgptText(row);
        setCell(cells[7], `<span class="lw-pill ${chat === "已登录" ? "good" : "warn"}">${chat}</span>`);
        const updated = row.last_seen_at || row.install_updated_at || row.updated_at || row.created_at || row.install_created_at;
        setCell(cells[10], `<span class="lw-muted">${esc(beijingShort(updated))}</span>`, beijingFull(updated));
        cells[11].classList.add("lw-actions");
        if (row.worker_id && !cells[11].querySelector("[data-worker-pairing-v2219]")) {
          const pairing = document.createElement("button");
          pairing.className = "action";
          pairing.type = "button";
          pairing.textContent = "配对码";
          pairing.dataset.workerPairingV2219 = String(row.worker_id);
          pairing.dataset.workerName = String(row.name || row.hostname || row.worker_id);
          pairing.title = pairingTitle(row);
          cells[11].appendChild(pairing);
        }
        const pairing = cells[11].querySelector("[data-worker-pairing-v2219]");
        if (pairing) pairing.title = pairingTitle(row);
        if (row.worker_id && !cells[11].querySelector("[data-worker-delete-v2219]")) {
          const remove = document.createElement("button");
          remove.className = "action danger";
          remove.type = "button";
          remove.textContent = "删除 Worker";
          remove.dataset.workerDeleteV2219 = String(row.worker_id);
          remove.dataset.workerName = String(row.name || row.hostname || row.worker_id);
          remove.title = "彻底删除中心保存的 Worker 身份和关联安装记录";
          cells[11].appendChild(remove);
        }
      });
    };

    const refreshRows = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const payload = await request("/api/admin/linux-worker-installations");
        rows = Array.isArray(payload.data) ? payload.data : [];
        paint();
      } catch (_) {
      } finally {
        refreshing = false;
      }
    };

    new MutationObserver(() => paint()).observe(tbody, {childList:true,subtree:false});

    const currentPairingRow = () => rows.find(row => String(row.worker_id || "") === selectedWorkerId) || null;
    const renderPairing = row => {
      const target = document.getElementById("linuxWorkerPairingCurrentV2219");
      const meta = pairingMeta(row);
      if (!meta.pairing_id) {
        target.innerHTML = "<b>当前：</b>未设置配对码";
        return;
      }
      const state = ({saved:"已保存，等待自动绑定",waiting_login:"等待 ChatGPT 登录",waiting_extension:"等待扩展连接",bound:"已绑定到本 Worker 扩展",error:"绑定失败"})[String(meta.status || "saved")] || String(meta.status || "已保存");
      target.innerHTML = `<div><b>当前：</b>${esc(meta.name || "配对码")}</div><div><b>前缀：</b>${esc(meta.prefix || "-")}…</div><div><b>状态：</b>${esc(state)}</div>${meta.last_error ? `<div style="color:#fca5a5"><b>原因：</b>${esc(meta.last_error)}</div>` : ""}`;
    };
    const openPairing = async (workerId, workerName) => {
      selectedWorkerId = workerId;
      document.getElementById("linuxWorkerPairingNameV2219").textContent = workerName || workerId;
      document.getElementById("linuxWorkerPairingCodeV2219").value = "";
      document.getElementById("linuxWorkerPairingResultV2219").textContent = "";
      await refreshRows();
      renderPairing(currentPairingRow());
      pairingDialog.showModal();
      document.getElementById("linuxWorkerPairingCodeV2219").focus();
    };
    const closePairing = () => {
      selectedWorkerId = "";
      document.getElementById("linuxWorkerPairingCodeV2219").value = "";
      if (pairingDialog.open) pairingDialog.close();
    };
    document.getElementById("closeLinuxWorkerPairingV2219").onclick = closePairing;
    pairingDialog.addEventListener("cancel", event => { event.preventDefault(); closePairing(); });
    document.getElementById("saveLinuxWorkerPairingV2219").onclick = async () => {
      if (!selectedWorkerId) return;
      const input = document.getElementById("linuxWorkerPairingCodeV2219");
      const result = document.getElementById("linuxWorkerPairingResultV2219");
      const code = input.value.trim();
      if (!code) { result.textContent = "请先粘贴配对码。"; return; }
      result.textContent = "正在校验并保存…";
      try {
        const payload = await request(`/api/admin/linux-workers/${encodeURIComponent(selectedWorkerId)}/pairing-code`, {method:"PUT",body:JSON.stringify({pairing_code:code})});
        input.value = "";
        result.textContent = payload?.pairing?.status === "bound" ? "保存成功，已自动绑定。" : "保存成功；登录和扩展就绪后会自动绑定。";
        await refreshRows();
        renderPairing(currentPairingRow());
      } catch (error) { result.textContent = error.message; }
    };
    document.getElementById("clearLinuxWorkerPairingV2219").onclick = async () => {
      if (!selectedWorkerId || !confirm("确定解除此 Worker 的配对码配置？")) return;
      const result = document.getElementById("linuxWorkerPairingResultV2219");
      result.textContent = "正在解除…";
      try {
        await request(`/api/admin/linux-workers/${encodeURIComponent(selectedWorkerId)}/pairing-code`, {method:"DELETE"});
        result.textContent = "已解除配对码配置。";
        await refreshRows();
        renderPairing(currentPairingRow());
      } catch (error) { result.textContent = error.message; }
    };

    tbody.addEventListener("click", event => {
      const pairing = event.target.closest?.("[data-worker-pairing-v2219]");
      if (pairing) {
        event.preventDefault();
        event.stopImmediatePropagation();
        openPairing(pairing.dataset.workerPairingV2219, pairing.dataset.workerName || pairing.dataset.workerPairingV2219).catch(error => alert(error.message));
        return;
      }
      const remove = event.target.closest?.("[data-worker-delete-v2219]");
      if (remove) {
        event.preventDefault();
        event.stopImmediatePropagation();
        const workerId = String(remove.dataset.workerDeleteV2219 || "");
        const name = String(remove.dataset.workerName || workerId);
        if (!workerId || !confirm(`确定彻底删除 Worker「${name}」？\n\n这会删除中心保存的 Worker 身份和关联安装记录；目标服务器若仍在运行，将因旧凭据失效而无法重新连接。`)) return;
        remove.disabled = true;
        remove.textContent = "删除中…";
        request(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/record`, {method:"DELETE"})
          .then(() => {
            rows = rows.filter(row => String(row.worker_id || "") !== workerId);
            document.getElementById("refreshLinuxWorkers")?.click();
            setTimeout(refreshRows, 120);
          })
          .catch(error => { alert(error.message); remove.disabled = false; remove.textContent = "删除 Worker"; });
      }
    }, true);

    document.getElementById("refreshLinuxWorkers")?.addEventListener("click", () => setTimeout(refreshRows, 80));
    setInterval(() => { if (section.classList.contains("active") && !pairingDialog.open) refreshRows(); }, 1500);
    refreshRows();
  };

  init();
})();
