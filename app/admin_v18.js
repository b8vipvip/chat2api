(() => {
  const VERSION = "0.18.0";

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  // The old console used “已连接” to mean that the administrator master key had
  // authenticated. Administrator auth is now an account session, so keep only the
  // useful server version in the header.
  const baseStatusV18 = status;
  status = (text, className = "muted") => {
    const value = String(text ?? "");
    const match = value.match(/^已连接\s*·\s*(v[0-9.]+)$/i);
    baseStatusV18(match ? match[1] : value, className);
  };

  const pairingBody = document.getElementById("pairingBody");
  const deviceBody = document.getElementById("extensionDeviceBody");
  const pairingTable = pairingBody?.closest("table");
  const deviceTable = deviceBody?.closest("table");
  const extensionView = document.getElementById("view-extensions");

  if (extensionView) {
    const intro = extensionView.querySelector(".panel .muted");
    if (intro) {
      intro.textContent = "配对码用于首次绑定扩展设备。配对成功后服务端记录最后配对的扩展 ID，扩展以后使用自己的设备凭据自动上线；配对状态与当前在线状态分开显示。";
    }
    const secretTitle = document.querySelector("#pairingSecret b");
    if (secretTitle) secretTitle.textContent = "新配对码：";
  }

  if (pairingTable) {
    pairingTable.querySelector("thead").innerHTML = `
      <tr>
        <th>名称</th>
        <th>配对码</th>
        <th>扩展 ID</th>
        <th>设备标识</th>
        <th>配对状态</th>
        <th>最后配对</th>
        <th>操作</th>
      </tr>`;
  }

  if (deviceTable) {
    deviceTable.querySelector("thead").innerHTML = `
      <tr>
        <th>扩展 ID</th>
        <th>设备标识</th>
        <th>版本</th>
        <th>状态</th>
        <th>绑定 API Key 数</th>
        <th>最后在线</th>
        <th>操作</th>
      </tr>`;
  }

  function pairingPill(value) {
    const paired = value === "paired";
    return `<span class="pill ${paired ? "ok" : "warn"}">${paired ? "已配对" : "未配对"}</span>`;
  }

  function onlinePill(value) {
    const online = value === "online";
    return `<span class="pill ${online ? "ok" : "bad"}">${online ? "在线" : "离线"}</span>`;
  }

  async function renderExtensionsV18() {
    try {
      const data = await api("/api/admin/extensions");
      if (pairingBody) {
        pairingBody.innerHTML = (data.pairing_codes || []).map(row => {
          const pairingState = row.pairing_status || (row.bound_client_id ? "paired" : "unpaired");
          return `<tr>
            <td>${esc(row.name)}</td>
            <td><code>${esc(row.prefix || "-")}</code></td>
            <td><code>${esc(row.bound_client_id || "-")}</code></td>
            <td><code>${esc(row.bound_device_id || "-")}</code></td>
            <td>${pairingPill(pairingState)}</td>
            <td>${fmtTime(row.last_paired_at)}</td>
            <td><div class="rowactions">
              <button class="action" onclick="copyManagedPairing('${esc(row.pairing_id)}')">复制</button>
              <button class="action" onclick="togglePairingV18('${esc(row.pairing_id)}',${!row.enabled})">${row.enabled ? "停用" : "启用"}</button>
              <button class="action danger" onclick="deletePairingV18('${esc(row.pairing_id)}')">删除</button>
            </div></td>
          </tr>`;
        }).join("") || '<tr><td colspan="7">暂无配对码，请先创建。</td></tr>';
      }

      if (deviceBody) {
        deviceBody.innerHTML = (data.clients || []).map(row => {
          const currentStatus = row.status || (row.online ? "online" : "offline");
          const connectionAction = row.connection_enabled
            ? `<button class="action danger" onclick="disconnectExtensionV18('${esc(row.client_id)}')">断开连接</button>`
            : `<button class="action good" onclick="enableExtensionV18('${esc(row.client_id)}')">允许连接</button>`;
          return `<tr>
            <td><code>${esc(row.client_id)}</code></td>
            <td><code>${esc(row.device_id || row.metadata?.device_id || "旧版设备未上报")}</code></td>
            <td>${esc(row.version || "-")}</td>
            <td>${onlinePill(currentStatus)}</td>
            <td>${esc(row.bound_api_keys ?? 0)}</td>
            <td>${fmtTime(row.last_seen_at)}</td>
            <td><div class="rowactions">
              ${connectionAction}
              <button class="action danger" onclick="deleteExtensionHistoryV18('${esc(row.client_id)}',${row.online ? "true" : "false"})">删除记录</button>
            </div></td>
          </tr>`;
        }).join("") || '<tr><td colspan="7">暂无扩展历史记录。</td></tr>';
      }
    } catch (error) {
      status("扩展管理加载失败：" + String(error?.message || error), "bad");
    }
  }

  window.copyManagedPairing = async pairingId => {
    try {
      const data = await api(`/api/admin/pairing-codes/${encodeURIComponent(pairingId)}/secret`);
      await navigator.clipboard.writeText(data.code || "");
      if (data.rotated) {
        status("旧配对码原文不可恢复，已自动轮换为新配对码并复制。已绑定扩展的自动连接不受影响。", "ok");
        await renderExtensionsV18();
      } else {
        status("配对码已复制", "ok");
      }
    } catch (error) {
      status(String(error?.message || error), "bad");
    }
  };

  window.togglePairingV18 = async (pairingId, enabled) => {
    try {
      await api(`/api/admin/pairing-codes/${encodeURIComponent(pairingId)}`, {
        method: "PATCH",
        body: {enabled},
      });
      await renderExtensionsV18();
    } catch (error) {
      status(String(error?.message || error), "bad");
    }
  };

  window.deletePairingV18 = async pairingId => {
    if (!confirm("确定删除这个配对码？删除后该码不能再用于重新配对；已经保存 client_id/clientToken 的扩展不会因此被断开。")) return;
    try {
      await api(`/api/admin/pairing-codes/${encodeURIComponent(pairingId)}`, {method: "DELETE"});
      status("配对码已删除", "ok");
      await renderExtensionsV18();
    } catch (error) {
      status(String(error?.message || error), "bad");
    }
  };

  window.disconnectExtensionV18 = async clientId => {
    if (!confirm("断开该扩展并禁止它自动接入？之后可点击“允许连接”恢复。")) return;
    try {
      await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/disconnect`, {method: "POST"});
      await renderExtensionsV18();
    } catch (error) {
      status(String(error?.message || error), "bad");
    }
  };

  window.enableExtensionV18 = async clientId => {
    try {
      await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/enable`, {method: "POST"});
      await renderExtensionsV18();
    } catch (error) {
      status(String(error?.message || error), "bad");
    }
  };

  window.deleteExtensionHistoryV18 = async (clientId, online) => {
    const warning = online
      ? "该扩展当前在线。删除记录会立即断开它并删除服务端设备凭据、忙碌状态和 API Key 粘性路由；以后必须重新配对才能上线。确定继续？"
      : "删除这条扩展历史记录后，服务端将删除它的设备凭据和指向它的 API Key 粘性路由；以后必须重新配对才能上线。确定继续？";
    if (!confirm(warning)) return;
    try {
      await api(`/api/admin/extensions/${encodeURIComponent(clientId)}`, {method: "DELETE"});
      status("扩展历史记录已删除", "ok");
      await renderExtensionsV18();
    } catch (error) {
      status(String(error?.message || error), "bad");
    }
  };

  const createPairingButton = document.getElementById("createPairing");
  if (createPairingButton) {
    createPairingButton.onclick = async () => {
      try {
        const name = document.getElementById("pairingName").value.trim() || "Chrome 扩展";
        const data = await api("/api/admin/pairing-codes", {method: "POST", body: {name}});
        document.getElementById("pairingCodeValue").textContent = data.code || "";
        document.getElementById("pairingSecret").classList.remove("hidden");
        status("配对码已创建；之后也可从列表再次复制。", "ok");
        await renderExtensionsV18();
      } catch (error) {
        status(String(error?.message || error), "bad");
      }
    };
  }

  const copyPairingCodeButton = document.getElementById("copyPairingCode");
  if (copyPairingCodeButton) {
    copyPairingCodeButton.onclick = async () => {
      await navigator.clipboard.writeText(document.getElementById("pairingCodeValue").textContent || "");
      status("配对码已复制", "ok");
    };
  }

  const showV17 = show;
  show = async viewName => {
    await showV17(viewName);
    if (viewName === "extensions") {
      status(`v${VERSION}`, "muted");
      await renderExtensionsV18();
    }
  };
  document.querySelectorAll(".nav button").forEach(button => {
    button.onclick = () => show(button.dataset.view);
  });

  if ((location.hash || "").slice(1) === "extensions") {
    status(`v${VERSION}`, "muted");
    renderExtensionsV18();
  }
})();
