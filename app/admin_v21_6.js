(() => {
  const VERSION = "0.21.6";
  const POLL_MS = 1500;
  const STATUS_COLUMNS = [
    ["platform", "平台"],
    ["network", "网络"],
    ["chatgpt", "ChatGPT"],
  ];
  let pollInFlight = false;

  function extensionViewActive() {
    return document.getElementById("view-extensions")?.classList.contains("active");
  }

  function columnCell(tr, key, fallbackIndex) {
    return tr?.querySelector(`td[data-chat2api-column-key="${key}"]`) || tr?.cells?.[fallbackIndex] || null;
  }

  function text(value, fallback = "-") {
    const normalized = String(value ?? "").trim();
    return normalized || fallback;
  }

  function metadata(row) {
    return row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
  }

  function effectiveVersion(row) {
    return text(metadata(row).extension_version || row?.version);
  }

  function platformState(row) {
    const meta = metadata(row);
    const os = text(meta.platform_os, "unknown").toLowerCase();
    const arch = text(meta.platform_arch, "unknown").toLowerCase();
    const labels = {linux: "Linux", win: "Windows", mac: "macOS", cros: "ChromeOS", android: "Android"};
    return {
      label: `${labels[os] || os} · ${arch}`,
      detail: meta.platform_supported_desktop === false
        ? "当前平台未被 Chrome Bridge 声明为受支持桌面平台"
        : "Chrome runtime platform metadata",
    };
  }

  function networkState(row) {
    const meta = metadata(row);
    const status = text(meta.network_probe_status, "unknown");
    const country = text(meta.network_country_code, "");
    if (status === "external") return {label: `外网${country ? ` · ${country}` : ""}`, level: "ok", detail: "允许主动 warm/prewarm"};
    if (status === "china-mainland") return {
      label: `中国大陆${country ? ` · ${country}` : ""}`,
      level: "warn",
      detail: "只阻止主动 warm/prewarm；真实 API 请求的按需兜底仍保留",
    };
    if (status === "offline") return {label: "浏览器离线", level: "bad", detail: text(meta.network_probe_error, "浏览器报告离线")};
    if (status === "error") return {label: "探测失败", level: "warn", detail: text(meta.network_probe_error, "网络区域探测失败")};
    return {label: "未知", level: "warn", detail: "尚无可用网络区域证据"};
  }

  function loginState(row) {
    const meta = metadata(row);
    const state = text(meta.chatgpt_login_state, "unknown");
    const composer = meta.chatgpt_login_composer_ready === true;
    if (state === "ready" && composer) return {label: "已登录 · Composer", level: "ok", detail: "ChatGPT Composer 已被动确认可用"};
    if (state === "login_required") return {label: "需要登录", level: "bad", detail: "需要在可见窗口中人工完成登录/CAPTCHA/2FA"};
    if (state === "checking") return {label: "检测中", level: "warn", detail: text(meta.chatgpt_login_strategy, "正在检查 ChatGPT 登录状态")};
    if (state === "ready") return {label: "已登录 · Composer 未确认", level: "warn", detail: "登录已识别，但 Composer 尚未确认可用"};
    return {label: "未知", level: "warn", detail: text(meta.chatgpt_login_strategy, "尚无足够被动登录证据")};
  }

  function healthState(row) {
    if (row?.connection_enabled === false) return {label: "已禁用", level: "warn", detail: "管理员已禁止该扩展连接"};
    if (row?.online !== true) return {label: "离线", level: "bad", detail: "扩展 WebSocket 当前不在线"};

    const platform = platformState(row);
    const network = networkState(row);
    const login = loginState(row);
    const meta = metadata(row);

    if (login.level === "bad") return {label: "需要人工登录", level: "bad", detail: login.detail};
    if (network.level === "bad") return {label: "网络离线", level: "bad", detail: network.detail};
    if (meta.platform_supported_desktop === false) return {label: "平台不支持", level: "bad", detail: platform.detail};
    if (login.level === "ok" && network.level === "ok") return {label: "就绪", level: "ok", detail: "扩展在线，外网可用，ChatGPT Composer 已确认"};
    if (network.label.startsWith("中国大陆")) return {label: "可调用 · 不主动预热", level: "warn", detail: network.detail};
    if (login.label === "检测中") return {label: "登录检测中", level: "warn", detail: login.detail};
    if (network.label === "探测失败") return {label: "网络探测异常", level: "warn", detail: network.detail};
    return {label: "部分就绪", level: "warn", detail: `${network.label}；${login.label}`};
  }

  function statusClass(level) {
    return level === "ok" ? "ok" : level === "bad" ? "bad" : "warnText";
  }

  function removeLegacyHealthColumn() {
    const table = document.querySelector("#view-extensions #extensionDeviceBody")?.closest("table");
    if (!table) return;
    for (const node of table.querySelectorAll('[data-chat2api-health-column="health"], th[data-chat2api-column-key="health"], [data-chat2api-health-cell="health"], td[data-chat2api-column-key="health"]')) {
      node.remove();
    }
  }

  function patchHeader() {
    const table = document.querySelector("#view-extensions #extensionDeviceBody")?.closest("table");
    const row = table?.querySelector("thead tr");
    if (!row) return;
    removeLegacyHealthColumn();
    for (const [key, label] of STATUS_COLUMNS) {
      let th = row.querySelector(`th[data-chat2api-health-column="${key}"]`);
      if (!th) {
        th = document.createElement("th");
        th.dataset.chat2apiHealthColumn = key;
        th.textContent = label;
        row.appendChild(th);
      }
      th.dataset.chat2apiColumnKey = key;
    }
  }

  function ensureCell(tr, key) {
    let cell = tr.querySelector(`td[data-chat2api-health-cell="${key}"]`);
    if (cell) {
      cell.dataset.chat2apiColumnKey = key;
      return cell;
    }
    cell = document.createElement("td");
    cell.dataset.chat2apiHealthCell = key;
    cell.dataset.chat2apiColumnKey = key;
    tr.appendChild(cell);
    return cell;
  }

  function renderState(cell, state) {
    cell.textContent = state.label;
    cell.classList.remove("ok", "bad", "warnText");
    cell.classList.add(statusClass(state.level));
    cell.title = state.detail || state.label;
  }

  function ensureSummary() {
    const body = document.getElementById("extensionDeviceBody");
    const panel = body?.closest(".panel");
    const scroll = body?.closest(".scroll");
    if (!panel || !scroll) return null;
    let node = document.getElementById("extensionHealthSummary");
    if (node) return node;
    node = document.createElement("div");
    node.id = "extensionHealthSummary";
    node.className = "toolbar";
    node.style.margin = "8px 0 12px";
    node.innerHTML = '<span class="muted">运行状态中心：等待扩展状态...</span>';
    panel.insertBefore(node, scroll);
    return node;
  }

  function renderSummary(rows) {
    const node = ensureSummary();
    if (!node) return;
    const counts = {ok: 0, warn: 0, bad: 0};
    for (const row of rows) counts[healthState(row).level] += 1;
    const total = rows.length;
    node.textContent = `运行状态中心：共 ${total} · 就绪 ${counts.ok} · 需关注 ${counts.warn} · 故障/离线 ${counts.bad}`;
    node.classList.remove("ok", "bad", "warnText", "muted");
    node.classList.add(counts.bad ? "bad" : counts.warn ? "warnText" : total ? "ok" : "muted");
    node.title = "健康结论来自扩展现有 WebSocket、platform v26、network v26 与 login readiness v27 元数据；宿主机 systemd watchdog 仍以本机 journal/state 为权威。";
  }

  function renderRows(rows) {
    removeLegacyHealthColumn();
    const byClient = new Map(rows.map(row => [String(row.client_id || ""), row]));
    const domRows = document.querySelectorAll("#extensionDeviceBody tr");
    for (const tr of domRows) {
      if (!tr.cells || !tr.cells.length) continue;
      const clientId = columnCell(tr, "client_id", 0)?.textContent?.trim() || "";
      const row = byClient.get(clientId);
      if (!row) continue;

      const versionCell = columnCell(tr, "version", 2);
      if (versionCell) {
        versionCell.textContent = effectiveVersion(row);
        versionCell.title = "当前在线扩展上报的 manifest 版本优先；离线时回退到注册版本";
      }

      const platform = platformState(row);
      const network = networkState(row);
      const login = loginState(row);
      renderState(ensureCell(tr, "platform"), {...platform, level: metadata(row).platform_supported_desktop === false ? "bad" : "ok"});
      renderState(ensureCell(tr, "network"), network);
      renderState(ensureCell(tr, "chatgpt"), login);
    }
    renderSummary(rows);
  }

  async function refreshHealthCenter() {
    if (!extensionViewActive() || pollInFlight || typeof globalThis.api !== "function") return;
    pollInFlight = true;
    try {
      patchHeader();
      const data = await api("/api/admin/extensions");
      renderRows(Array.isArray(data.clients) ? data.clients : []);
    } catch (_) {
      // Historical extension management owns visible transport/auth errors.
    } finally {
      pollInFlight = false;
    }
  }

  const baseShow = typeof globalThis.show === "function" ? globalThis.show : null;
  if (baseShow && !baseShow.__chat2apiHealthCenterV216) {
    const wrappedShow = async (...args) => {
      const result = await baseShow(...args);
      if (args[0] === "extensions") await refreshHealthCenter();
      return result;
    };
    wrappedShow.__chat2apiHealthCenterV216 = true;
    globalThis.show = wrappedShow;
  }

  document.documentElement.dataset.chat2apiHealthCenterVersion = VERSION;
  patchHeader();
  ensureSummary();
  refreshHealthCenter();
  setInterval(refreshHealthCenter, POLL_MS);
})();