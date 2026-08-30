(() => {
  const KEY = "__CHAT2API_LINUX_WORKER_PROXY_HEALTH_V55__";
  if (globalThis[KEY]) return;

  const state = {
    version: 55,
    rows: [],
    rowsFetchedAt: 0,
    healthByWorker: new Map(),
    healthInflight: new Map(),
    renderScheduled: false,
    refreshingRows: false,
  };
  globalThis[KEY] = state;

  const HEALTH_TTL_MS = 60000;
  const HEALTH_RETRY_MS = 20000;
  const ROW_TTL_MS = 1200;

  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  })[char]);

  const configuredProxy = row => {
    const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    const summary = meta.proxy_summary && typeof meta.proxy_summary === "object" ? meta.proxy_summary : {};
    return Boolean(String(summary.protocol || "").trim());
  };

  const proxyName = row => {
    const meta = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    const summary = meta.proxy_summary && typeof meta.proxy_summary === "object" ? meta.proxy_summary : {};
    return String(summary.name || summary.server || summary.protocol || "").trim();
  };

  const probe = (result, name) => {
    const probes = Array.isArray(result?.probes) ? result.probes : [];
    return probes.find(item => String(item?.name || "") === name) || null;
  };

  const parseHealth = result => {
    const network = probe(result, "network_access");
    const gptProbes = ["chatgpt_home", "conversation_route", "sentinel_route"]
      .map(name => probe(result, name));
    const gptKnown = gptProbes.every(Boolean);
    const gptReady = gptKnown
      ? gptProbes.every(item => item?.ok === true)
      : Boolean(result?.generation_backend_ready ?? result?.ok);
    const networkReady = network ? network.ok === true : null;
    const latencySource = network || probe(result, "chatgpt_home");
    const rawLatency = Number(latencySource?.total_s || 0);
    return {
      checkedAt: Date.now(),
      networkReady,
      gptReady,
      latencyMs: Number.isFinite(rawLatency) && rawLatency > 0
        ? Math.max(1, Math.round(rawLatency * 1000))
        : 0,
      error: String(result?.error || ""),
    };
  };

  const pill = (text, tone) => `<span class="lw-pill ${tone}">${esc(text)}</span>`;

  const healthHtml = row => {
    if (!configuredProxy(row)) return '<span class="lw-muted">未配置</span>';
    const workerId = String(row?.worker_id || "");
    const health = state.healthByWorker.get(workerId);
    const name = proxyName(row);
    if (!health) {
      return `<div style="display:flex;flex-wrap:wrap;gap:5px">${pill("已配置","good")}${pill("网络检测中","warn")}${pill("GPT检测中","warn")}${pill("延迟 --","warn")}</div>${name ? `<div class="lw-muted" style="margin-top:4px">${esc(name)}</div>` : ""}`;
    }
    const networkText = health.networkReady === true ? "网络正常" : health.networkReady === false ? "网络异常" : "网络检测中";
    const networkTone = health.networkReady === true ? "good" : health.networkReady === false ? "bad" : "warn";
    const gptText = health.gptReady === true ? "GPT正常" : health.gptReady === false ? "GPT异常" : "GPT检测中";
    const gptTone = health.gptReady === true ? "good" : health.gptReady === false ? "bad" : "warn";
    const latencyText = health.latencyMs > 0 ? `延迟 ${health.latencyMs} ms` : "延迟 --";
    const latencyTone = health.latencyMs > 0 ? "good" : "warn";
    const title = health.error ? ` title="${esc(health.error)}"` : "";
    return `<div style="display:flex;flex-wrap:wrap;gap:5px"${title}>${pill("已配置","good")}${pill(networkText,networkTone)}${pill(gptText,gptTone)}${pill(latencyText,latencyTone)}</div>${name ? `<div class="lw-muted" style="margin-top:4px">${esc(name)}</div>` : ""}`;
  };

  const loadRows = async force => {
    if (state.refreshingRows) return state.rows;
    if (!force && state.rows.length && Date.now() - state.rowsFetchedAt < ROW_TTL_MS) return state.rows;
    state.refreshingRows = true;
    try {
      const response = await fetch("/api/admin/linux-worker-installations", {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.ok) {
        const payload = await response.json();
        state.rows = Array.isArray(payload.data) ? payload.data : [];
        state.rowsFetchedAt = Date.now();
      }
    } catch (_) {
    } finally {
      state.refreshingRows = false;
    }
    return state.rows;
  };

  const requestHealth = row => {
    const workerId = String(row?.worker_id || "");
    if (!workerId || !configuredProxy(row) || state.healthInflight.has(workerId)) return;
    const previous = state.healthByWorker.get(workerId);
    const age = previous ? Date.now() - Number(previous.checkedAt || 0) : Infinity;
    const ttl = previous?.error ? HEALTH_RETRY_MS : HEALTH_TTL_MS;
    if (age < ttl) return;

    const task = (async () => {
      try {
        const response = await fetch(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/commands`, {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({command:"test_proxy",arguments:{},wait:true,timeout_seconds:35}),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
        state.healthByWorker.set(workerId, parseHealth(payload.result || {}));
      } catch (error) {
        state.healthByWorker.set(workerId, {
          checkedAt: Date.now(),
          networkReady: false,
          gptReady: false,
          latencyMs: 0,
          error: String(error?.message || error),
        });
      } finally {
        state.healthInflight.delete(workerId);
        scheduleRender(true);
      }
    })();
    state.healthInflight.set(workerId, task);
  };

  const paint = async force => {
    const section = document.getElementById("view-linux-workers");
    const tbody = document.getElementById("linuxWorkerRows");
    if (!section || !tbody) return;
    await loadRows(Boolean(force));
    const domRows = Array.from(tbody.querySelectorAll(":scope > tr"));
    domRows.forEach((tr, index) => {
      const row = state.rows[index];
      const proxyCell = tr.children[6];
      if (!row || !proxyCell || row.record_type === "installation") return;
      const html = healthHtml(row);
      proxyCell.dataset.chat2apiProxyHealthOwner = "v55";
      if (proxyCell.innerHTML !== html) proxyCell.innerHTML = html;
      requestHealth(row);
    });
  };

  function scheduleRender(force = false) {
    if (state.renderScheduled) return;
    state.renderScheduled = true;
    requestAnimationFrame(() => {
      state.renderScheduled = false;
      paint(force).catch(() => {});
    });
  }

  const init = () => {
    const section = document.getElementById("view-linux-workers");
    const tbody = document.getElementById("linuxWorkerRows");
    if (!section || !tbody) {
      setTimeout(init, 120);
      return;
    }

    new MutationObserver(mutations => {
      if (!mutations.length) return;
      scheduleRender(false);
    }).observe(tbody, {childList:true,subtree:true,characterData:true});

    setInterval(() => {
      if (section.classList.contains("active")) scheduleRender(false);
    }, 1200);

    scheduleRender(true);
  };

  init();
})();
