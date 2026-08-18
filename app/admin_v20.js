(() => {
  const VERSION = "0.20.0";
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  const view = document.getElementById("view-extensions");
  const body = document.getElementById("extensionDeviceBody");
  const table = body?.closest("table");
  const panel = table?.closest(".panel");

  function applyLabels() {
    if (!view) return;
    const title = view.querySelector("h2");
    if (title) title.textContent = "扩展配对与扩展管理";
    const intro = view.querySelector(".panel .muted");
    if (intro) {
      intro.textContent = "配对成功后扩展会自动识别当前 ChatGPT 账户类型并持续上报。Free 账户用于 gpt-5.5-mini 默认模型；付费账户继续提供可选择模型与推理强度的路由。";
    }
    if (panel) {
      const heading = panel.querySelector("h3");
      if (heading) heading.textContent = "扩展列表";
    }
    if (table) {
      const head = table.querySelector("thead");
      if (head) {
        head.innerHTML = `
          <tr>
            <th>扩展 ID</th>
            <th>设备标识</th>
            <th>版本</th>
            <th>账户类型</th>
            <th>状态</th>
            <th>绑定 API Key 数</th>
            <th>最后在线</th>
            <th>操作</th>
          </tr>`;
      }
    }
  }

  function accountType(row) {
    const value = String(row?.account_type || row?.metadata?.account_type || "unknown").toLowerCase();
    return ["free", "paid"].includes(value) ? value : "unknown";
  }

  function accountPill(row) {
    const value = accountType(row);
    const strategy = String(row?.metadata?.account_detection_strategy || "");
    const confidence = String(row?.metadata?.account_detection_confidence || "");
    const title = esc([strategy, confidence].filter(Boolean).join(" · "));
    if (value === "free") return `<span class="pill warn" title="${title}">Free</span>`;
    if (value === "paid") return `<span class="pill ok" title="${title}">付费</span>`;
    return `<span class="pill" title="${title}">未识别</span>`;
  }

  function onlinePillV20(row) {
    if (!row.connection_enabled) return '<span class="pill">已禁用</span>';
    if (row.online && row.busy) return '<span class="pill warn">忙碌</span>';
    if (row.online) return '<span class="pill ok">在线</span>';
    return '<span class="pill bad">离线</span>';
  }

  async function renderExtensionListV20() {
    if (!body) return;
    applyLabels();
    try {
      const data = await api("/api/admin/extensions");
      body.innerHTML = (data.clients || []).map(row => {
        const connectionAction = row.connection_enabled
          ? `<button class="action danger" onclick="disconnectExtensionV18('${esc(row.client_id)}')">断开</button>`
          : `<button class="action good" onclick="enableExtensionV18('${esc(row.client_id)}')">连接</button>`;
        return `<tr>
          <td><code>${esc(row.client_id)}</code></td>
          <td><code>${esc(row.device_id || row.metadata?.device_id || "旧版设备未上报")}</code></td>
          <td>${esc(row.version || "-")}</td>
          <td>${accountPill(row)}</td>
          <td>${onlinePillV20(row)}</td>
          <td>${esc(row.bound_api_keys ?? 0)}</td>
          <td>${fmtTime(row.last_seen_at)}</td>
          <td><div class="rowactions">
            ${connectionAction}
            <button class="action danger" onclick="deleteExtensionHistoryV18('${esc(row.client_id)}',${row.online ? "true" : "false"})">删除</button>
          </div></td>
        </tr>`;
      }).join("") || '<tr><td colspan="8">暂无扩展历史记录。</td></tr>';
    } catch (error) {
      status("扩展列表加载失败：" + String(error?.message || error), "bad");
    }
  }

  applyLabels();

  for (const name of ["disconnectExtensionV18", "enableExtensionV18", "deleteExtensionHistoryV18"]) {
    const base = window[name];
    if (typeof base !== "function") continue;
    window[name] = async (...args) => {
      await base(...args);
      await renderExtensionListV20();
    };
  }

  const baseShow = show;
  show = async viewName => {
    await baseShow(viewName);
    if (viewName === "extensions") {
      applyLabels();
      await renderExtensionListV20();
      status(`v${VERSION}`, "muted");
    }
  };

  document.querySelectorAll(".nav button").forEach(button => {
    button.onclick = () => show(button.dataset.view);
  });

  if ((location.hash || "").slice(1) === "extensions") {
    renderExtensionListV20().catch(() => {});
  }
})();
