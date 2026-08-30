(() => {
  const init = () => {
    const section = document.getElementById("view-linux-workers");
    const tbody = document.getElementById("linuxWorkerRows");
    if (!section || !tbody) {
      setTimeout(init, 120);
      return;
    }
    if (globalThis.__CHAT2API_LINUX_WORKER_NETWORK_HEALTH_V56__) return;
    globalThis.__CHAT2API_LINUX_WORKER_NETWORK_HEALTH_V56__ = true;
    globalThis.__CHAT2API_LINUX_WORKER_PROXY_HEALTH_V55__ = "retired-by-v56";

    const style = document.createElement("style");
    style.dataset.chat2apiLinuxWorkerNetworkV56 = "1";
    style.textContent = `
      #view-linux-workers th:nth-child(7),
      #view-linux-workers td:nth-child(7){display:none!important}
      #view-linux-workers th:nth-child(6){min-width:260px}
      #view-linux-workers td:nth-child(6){position:relative;min-width:260px;color:transparent!important}
      #view-linux-workers td:nth-child(6)>*{visibility:hidden!important}
      #view-linux-workers td:nth-child(6)::before{
        content:attr(data-chat2api-network-main);
        display:block;
        min-height:22px;
        box-sizing:border-box;
        width:max-content;
        max-width:100%;
        padding:2px 8px;
        border:1px solid #334155;
        border-radius:999px;
        background:#101a2b;
        color:#dbeafe;
        font-size:12px;
        line-height:17px;
        white-space:nowrap;
        overflow:hidden;
        text-overflow:ellipsis;
      }
      #view-linux-workers td:nth-child(6)::after{
        content:attr(data-chat2api-network-sub);
        display:block;
        margin-top:5px;
        color:#94a3b8;
        font-size:12px;
        line-height:17px;
        white-space:nowrap;
      }
      #view-linux-workers td:nth-child(6)[data-chat2api-network-tone="good"]::before{border-color:#166534;background:rgba(20,83,45,.22);color:#bbf7d0}
      #view-linux-workers td:nth-child(6)[data-chat2api-network-tone="warn"]::before{border-color:#854d0e;background:rgba(113,63,18,.24);color:#fde68a}
      #view-linux-workers td:nth-child(6)[data-chat2api-network-tone="bad"]::before{border-color:#991b1b;background:rgba(127,29,29,.22);color:#fecaca}
    `;
    document.head.appendChild(style);

    const table = tbody.closest("table");
    const header = table?.querySelector("thead tr");
    if (header?.children?.[5]) header.children[5].textContent = "网络";

    const HEALTH_TTL_MS = 60000;
    const HEALTH_RETRY_MS = 20000;
    const ROW_REFRESH_MS = 3000;
    const healthByWorker = new Map();
    const healthInflight = new Map();
    let rows = [];
    let rowsInflight = null;

    const bridge = row => {
      const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
      return meta.bridge && typeof meta.bridge === "object" ? meta.bridge : {};
    };
    const proxySummary = row => {
      const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
      return meta.proxy_summary && typeof meta.proxy_summary === "object" ? meta.proxy_summary : {};
    };
    const configuredProxy = row => {
      const summary = proxySummary(row);
      const status = String(row?.proxy_status || "").toLowerCase();
      return Boolean(String(summary.protocol || summary.server || summary.name || "").trim()) || ["connected","ready","checking","testing"].includes(status);
    };
    const proxyName = row => {
      const summary = proxySummary(row);
      return String(summary.name || summary.server || summary.protocol || "代理").trim();
    };
    const countryText = code => ({US:"美国",JP:"日本",SG:"新加坡",KR:"韩国",GB:"英国",DE:"德国",FR:"法国",CA:"加拿大",AU:"澳大利亚",HK:"中国香港",TW:"中国台湾",CN:"中国大陆"})[String(code || "").toUpperCase()] || String(code || "").toUpperCase();
    const reportedNetwork = row => {
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
    const parseProbe = (result, name) => {
      const probes = Array.isArray(result?.probes) ? result.probes : [];
      return probes.find(item => String(item?.name || "") === name) || null;
    };
    const parseHealth = result => {
      const network = parseProbe(result, "network_access");
      const chatgpt = ["chatgpt_home","conversation_route","sentinel_route"].map(name => parseProbe(result, name));
      const chatgptKnown = chatgpt.every(Boolean);
      const chatgptReady = chatgptKnown ? chatgpt.every(item => item?.ok === true) : Boolean(result?.generation_backend_ready ?? result?.ok);
      const networkReady = network ? network.ok === true : null;
      const rawLatency = Number(network?.total_s || parseProbe(result, "chatgpt_home")?.total_s || 0);
      return {
        checkedAt:Date.now(),
        networkReady,
        chatgptReady,
        latencyMs:Number.isFinite(rawLatency) && rawLatency > 0 ? Math.max(1, Math.round(rawLatency * 1000)) : 0,
        error:String(result?.error || ""),
      };
    };

    const networkView = row => {
      if (!row?.worker_id) return {main:"-",sub:"",tone:"warn",title:""};
      const base = reportedNetwork(row);
      if (!configuredProxy(row)) {
        return {main:`直连 · ${base}`,sub:"代理未配置",tone:base.includes("离线") || base.includes("失败") ? "bad" : "warn",title:"当前 Worker 未配置代理节点"};
      }
      const name = proxyName(row);
      const health = healthByWorker.get(String(row.worker_id));
      if (!health) return {main:`${name} · ${base}`,sub:"网络检测中 · GPT检测中 · 延迟 --",tone:"warn",title:"正在通过当前代理节点执行实际网络与 ChatGPT 生成链路检测"};
      const networkText = health.networkReady === true ? "网络正常" : health.networkReady === false ? "网络异常" : "网络检测中";
      const gptText = health.chatgptReady === true ? "GPT正常" : health.chatgptReady === false ? "GPT异常" : "GPT检测中";
      const latencyText = health.latencyMs > 0 ? `${health.latencyMs} ms` : "--";
      const tone = health.networkReady === false || health.chatgptReady === false ? "bad" : health.networkReady === true && health.chatgptReady === true ? "good" : "warn";
      const title = health.error || `节点 ${name}；${networkText}；${gptText}；延迟 ${latencyText}`;
      return {main:`${name} · ${base}`,sub:`${networkText} · ${gptText} · 延迟 ${latencyText}`,tone,title};
    };

    const syncDom = () => {
      const domRows = Array.from(tbody.querySelectorAll(":scope > tr"));
      domRows.forEach((tr, index) => {
        const row = rows[index];
        const cell = tr.children?.[5];
        if (!row || !cell) return;
        const view = networkView(row);
        cell.dataset.chat2apiNetworkMain = view.main;
        cell.dataset.chat2apiNetworkSub = view.sub;
        cell.dataset.chat2apiNetworkTone = view.tone;
        cell.dataset.chat2apiNetworkOwner = "v56";
        cell.title = view.title;
      });
    };

    const requestProxyHealth = async row => {
      const workerId = String(row?.worker_id || "");
      if (!workerId || !configuredProxy(row) || healthInflight.has(workerId)) return;
      const previous = healthByWorker.get(workerId);
      const age = previous ? Date.now() - Number(previous.checkedAt || 0) : Infinity;
      const ttl = previous?.error || previous?.networkReady === false || previous?.chatgptReady === false ? HEALTH_RETRY_MS : HEALTH_TTL_MS;
      if (age < ttl) return;
      const task = (async () => {
        try {
          const response = await fetch(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/commands`, {
            method:"POST",
            credentials:"same-origin",
            cache:"no-store",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({command:"test_proxy",arguments:{},wait:true,timeout_seconds:35}),
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
          healthByWorker.set(workerId, parseHealth(payload.result || {}));
        } catch (error) {
          healthByWorker.set(workerId, {checkedAt:Date.now(),networkReady:false,chatgptReady:false,latencyMs:0,error:String(error?.message || error)});
        } finally {
          healthInflight.delete(workerId);
          syncDom();
        }
      })();
      healthInflight.set(workerId, task);
    };

    const refreshRows = async () => {
      if (rowsInflight) return rowsInflight;
      rowsInflight = (async () => {
        try {
          const response = await fetch("/api/admin/linux-worker-installations", {credentials:"same-origin",cache:"no-store"});
          if (!response.ok) return;
          const payload = await response.json();
          rows = Array.isArray(payload.data) ? payload.data : [];
          syncDom();
          rows.forEach(row => requestProxyHealth(row));
        } catch (_) {
        } finally {
          rowsInflight = null;
        }
      })();
      return rowsInflight;
    };

    new MutationObserver(() => syncDom()).observe(tbody, {childList:true,subtree:false});
    setInterval(() => { if (section.classList.contains("active")) refreshRows(); }, ROW_REFRESH_MS);
    refreshRows();
  };

  init();
})();
