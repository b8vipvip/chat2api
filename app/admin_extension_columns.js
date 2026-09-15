(() => {
  const VERSION = "0.22.41-worker-list-v60";
  const COLUMN_SCHEMA_REVISION = 67;
  const STORAGE_KEY = "chat2api.extensionColumns.v3";
  const LEGACY_STORAGE_KEY = "chat2api.extensionColumns.v2";
  const COLUMNS = [
    {key: "client_id", label: "Worker ID"},
    {key: "device_id", label: "设备标识"},
    {key: "version", label: "版本"},
    {key: "account_type", label: "账户类型"},
    {key: "status", label: "状态"},
    {key: "worker_settings", label: "并发设置"},
    {key: "last_seen", label: "最后在线"},
    {key: "network", label: "网络"},
    {key: "chatgpt", label: "ChatGPT"},
    {key: "actions", label: "操作"},
    {key: "device_name", label: "设备名称"},
    {key: "occupancy", label: "当前占用"},
  ];
  const KNOWN_KEYS = new Set(COLUMNS.map(item => item.key));
  const DEFAULT_ORDER = COLUMNS.map(item => item.key);
  const LEGACY_KEY_MAP = new Map([
    ["platform", "worker_settings"],
  ]);
  const REMOVED_KEYS = new Set(["concurrency", "reserve_windows", "bound_api_keys", "occupied_windows"]);

  let prefs = null;
  let menuOpen = false;
  let bodyOverflowBeforeModal = "";
  let renderInFlight = null;
  let extensionSnapshot = null;
  let canonicalizing = false;
  let repairQueued = false;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function defaultPrefs() {
    return {
      order: [...DEFAULT_ORDER],
      visible: Object.fromEntries(DEFAULT_ORDER.map(key => [key, true])),
    };
  }

  function normalizePrefs(raw) {
    const result = defaultPrefs();
    const order = [];
    const seen = new Set();
    for (const original of Array.isArray(raw?.order) ? raw.order : []) {
      if (REMOVED_KEYS.has(original)) continue;
      const key = LEGACY_KEY_MAP.get(original) || original;
      if (!KNOWN_KEYS.has(key) || seen.has(key)) continue;
      seen.add(key);
      order.push(key);
    }
    for (const key of DEFAULT_ORDER) {
      if (!seen.has(key)) order.push(key);
    }
    result.order = order;

    if (raw?.visible && typeof raw.visible === "object") {
      for (const [original, value] of Object.entries(raw.visible)) {
        if (typeof value !== "boolean" || REMOVED_KEYS.has(original)) continue;
        const key = LEGACY_KEY_MAP.get(original) || original;
        if (KNOWN_KEYS.has(key)) result.visible[key] = value;
      }
    }
    return result;
  }

  function loadPrefs() {
    if (prefs) return prefs;
    try {
      const current = localStorage.getItem(STORAGE_KEY);
      if (current) {
        prefs = normalizePrefs(JSON.parse(current));
        return prefs;
      }
      const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
      prefs = legacy ? normalizePrefs(JSON.parse(legacy)) : defaultPrefs();
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
      return prefs;
    } catch (_) {
      prefs = defaultPrefs();
      return prefs;
    }
  }

  function savePrefs(next) {
    prefs = normalizePrefs(next);
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs)); } catch (_) {}
  }

  function tableParts() {
    const body = document.getElementById("extensionDeviceBody");
    const table = body?.closest("table") || null;
    const headerRow = table?.querySelector("thead tr") || null;
    return {body, table, headerRow};
  }

  function ensureCompactWorkerSettingsStyle() {
    let style = document.getElementById("chat2apiWorkerSettingsCompactV60");
    if (style) return style;
    style = document.createElement("style");
    style.id = "chat2apiWorkerSettingsCompactV60";
    style.textContent = '[data-worker-window-editor] [data-worker-live],[data-worker-window-editor] [data-worker-platform]{display:none!important}';
    document.head.appendChild(style);
    return style;
  }

  function keyedChild(parent, key) {
    return [...(parent?.children || [])].find(node =>
      String(node.dataset?.chat2apiColumnKey || "") === key,
    ) || null;
  }

  function reorder(parent, activePrefs) {
    if (!parent) return;
    const current = [...parent.children];
    const known = activePrefs.order.map(key => keyedChild(parent, key)).filter(Boolean);
    const unknown = current.filter(node => !KNOWN_KEYS.has(String(node.dataset?.chat2apiColumnKey || "")));
    const desired = [...known, ...unknown];
    if (current.length === desired.length && current.every((node, index) => node === desired[index])) return;
    const fragment = document.createDocumentFragment();
    for (const node of desired) fragment.appendChild(node);
    parent.appendChild(fragment);
  }

  function applyVisibility(parent, activePrefs) {
    if (!parent) return;
    for (const key of activePrefs.order) {
      const node = keyedChild(parent, key);
      if (!node) continue;
      const next = activePrefs.visible[key] === false ? "none" : "";
      if (node.style.display !== next) node.style.display = next;
    }
  }

  function applyLayout() {
    const activePrefs = loadPrefs();
    const {body, headerRow} = tableParts();
    if (!body || !headerRow) return;
    reorder(headerRow, activePrefs);
    applyVisibility(headerRow, activePrefs);
    for (const tr of body.rows) {
      if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) {
        const visible = activePrefs.order.filter(key => activePrefs.visible[key] !== false && keyedChild(headerRow, key)).length;
        tr.cells[0].colSpan = Math.max(1, visible);
        continue;
      }
      reorder(tr, activePrefs);
      applyVisibility(tr, activePrefs);
    }
  }

  function canonicalHeaderHtml() {
    return COLUMNS.map(({key, label}) => {
      const health = key === "network" || key === "chatgpt" ? ` data-chat2api-health-column="${key}"` : "";
      const owner = key === "worker_settings" ? ' data-chat2api-structural-owner="worker-settings-v59"' : "";
      const title = key === "occupancy" ? ' title="当前占用 / 配置并发上限"' : "";
      return `<th data-chat2api-column-key="${key}"${health}${owner}${title}>${label}</th>`;
    }).join("");
  }

  function accountType(row) {
    const value = String(row?.account_type || row?.metadata?.account_type || "unknown").toLowerCase();
    return value === "free" || value === "paid" ? value : "unknown";
  }

  function accountPill(row) {
    const value = accountType(row);
    const strategy = String(row?.metadata?.account_detection_strategy || "");
    const confidence = String(row?.metadata?.account_detection_confidence || "");
    const title = esc([strategy, confidence].filter(Boolean).join(" · "));
    if (value === "free") return `<span class="pill warn" title="${title}">Free</span>`;
    if (value === "paid") return `<span class="pill ok" title="${title}">付费</span>`;
    return `<span class="pill" title="${title}">未知</span>`;
  }

  function statusPill(row) {
    if (row?.connection_enabled === false) return '<span class="pill">已禁用</span>';
    if (row?.online && row?.busy) return '<span class="pill warn">忙碌</span>';
    if (row?.online) return '<span class="pill ok">在线</span>';
    return '<span class="pill bad">离线</span>';
  }

  function networkLabel(row) {
    const meta = row?.metadata || {};
    const state = String(meta.network_probe_status || "unknown");
    const country = String(meta.network_country_code || "").trim();
    if (state === "external") return {text: `外网${country ? ` · ${country}` : ""}`, cls: "ok"};
    if (state === "china-mainland") return {text: `中国大陆${country ? ` · ${country}` : ""}`, cls: "warnText"};
    if (state === "offline") return {text: "浏览器离线", cls: "bad"};
    if (state === "error") return {text: "探测失败", cls: "warnText"};
    return {text: "未知", cls: "warnText"};
  }

  function chatgptLabel(row) {
    const meta = row?.metadata || {};
    const state = String(meta.chatgpt_login_state || "unknown");
    if (state === "ready") return {text: "已登录", cls: meta.chatgpt_login_composer_ready === true ? "ok" : "warnText"};
    if (state === "login_required") return {text: "未登录", cls: "bad"};
    if (state === "checking") return {text: "检测中", cls: "warnText"};
    return {text: "未知", cls: "warnText"};
  }

  function occupancy(row) {
    const capacity = row?.capacity && typeof row.capacity === "object" ? row.capacity : {};
    const usedRaw = capacity.used_units ?? row?.active_api_calls ?? 0;
    const limitRaw = capacity.limit_units ?? row?.max_concurrency ?? row?.configured_max_concurrency ?? 0;
    const queueRaw = capacity.queued_requests ?? 0;
    const used = Number.isFinite(Number(usedRaw)) ? Number(usedRaw) : 0;
    const limit = Number.isFinite(Number(limitRaw)) ? Number(limitRaw) : 0;
    const queued = Number.isFinite(Number(queueRaw)) ? Number(queueRaw) : 0;
    const cooling = capacity.rate_limit_cooldown_active === true;
    const remaining = Number(capacity.rate_limit_cooldown_remaining_seconds || 0);
    return {
      text: `${used} / ${limit || "-"}${queued > 0 ? ` · 排队 ${queued}` : ""}`,
      title: `当前占用 ${used}${limit ? ` / ${limit}` : ""}${queued > 0 ? `；排队 ${queued}` : ""}${cooling ? `；额度冷却 ${Math.max(0, Math.ceil(remaining))} 秒` : ""}`,
      cls: used > 0 ? "warnText" : "muted",
    };
  }

  function workerActions(row) {
    const id = esc(row.client_id || "");
    const connect = row.connection_enabled === false
      ? `<button class="action good" data-worker-list-action="enable" data-client-id="${id}">连接</button>`
      : `<button class="action danger" data-worker-list-action="disconnect" data-client-id="${id}">断开</button>`;
    return `<div class="rowactions">${connect}<button class="action danger" data-worker-list-action="delete" data-client-id="${id}" data-online="${row.online ? "1" : "0"}">删除</button></div>`;
  }

  function rowHtml(row) {
    const network = networkLabel(row);
    const login = chatgptLabel(row);
    const occupied = occupancy(row);
    const clientId = esc(row.client_id || "");
    const deviceName = String(row?.device_name || "").trim();
    const deviceNameHtml = deviceName
      ? `<span title="${esc(row?.device_code_id || row?.pairing_id || "")}">${esc(deviceName)}</span>`
      : '<span class="muted">-</span>';
    return `<tr data-chat2api-canonical-worker-row="1" data-client-id="${clientId}">
      <td data-chat2api-column-key="client_id"><code>${clientId}</code></td>
      <td data-chat2api-column-key="device_id"><code>${esc(row.device_id || row.metadata?.device_id || "-")}</code></td>
      <td data-chat2api-column-key="version">${esc(row.metadata?.extension_version || row.version || "-")}</td>
      <td data-chat2api-column-key="account_type">${accountPill(row)}</td>
      <td data-chat2api-column-key="status">${statusPill(row)}</td>
      <td data-chat2api-column-key="worker_settings" data-chat2api-structural-owner="worker-settings-v59"><span class="muted">加载中…</span></td>
      <td data-chat2api-column-key="last_seen">${typeof fmtTime === "function" ? fmtTime(row.last_seen_at) : esc(row.last_seen_at || "-")}</td>
      <td data-chat2api-column-key="network" data-chat2api-health-cell="network" class="${network.cls}">${esc(network.text)}</td>
      <td data-chat2api-column-key="chatgpt" data-chat2api-health-cell="chatgpt" class="${login.cls}">${esc(login.text)}</td>
      <td data-chat2api-column-key="actions">${workerActions(row)}</td>
      <td data-chat2api-column-key="device_name">${deviceNameHtml}</td>
      <td data-chat2api-column-key="occupancy" class="${occupied.cls}" title="${esc(occupied.title)}">${esc(occupied.text)}</td>
    </tr>`;
  }

  function renderWorkerRows(rows) {
    const {body, headerRow, table} = tableParts();
    if (!body || !headerRow) return;
    canonicalizing = true;
    try {
      const header = canonicalHeaderHtml();
      if (headerRow.innerHTML !== header) headerRow.innerHTML = header;
      body.innerHTML = rows.length
        ? rows.map(rowHtml).join("")
        : `<tr><td colspan="${COLUMNS.length}" class="muted">暂无 Worker。</td></tr>`;
      applyLayout();
      document.documentElement.dataset.chat2apiWorkerListReady = "1";
      document.documentElement.dataset.chat2apiWorkerListVersion = VERSION;
      document.documentElement.dataset.chat2apiWorkerColumnSchemaRevision = String(COLUMN_SCHEMA_REVISION);
      if (table) table.style.visibility = "";
    } finally {
      canonicalizing = false;
    }
  }

  function pairingState(row) {
    const paired = (row.pairing_status || (row.bound_client_id ? "paired" : "unpaired")) === "paired";
    return `<span class="pill ${paired ? "ok" : "warn"}">${paired ? "已配对" : "未配对"}</span>`;
  }

  function renderPairings(rows) {
    const body = document.getElementById("pairingBody");
    if (!body) return;
    body.innerHTML = rows.length ? rows.map(row => `<tr>
      <td>${esc(row.name)}</td>
      <td><code>${esc(row.prefix || "-")}</code></td>
      <td><code>${esc(row.bound_client_id || "-")}</code></td>
      <td><code>${esc(row.bound_device_id || "-")}</code></td>
      <td>${pairingState(row)}</td>
      <td>${typeof fmtTime === "function" ? fmtTime(row.last_paired_at) : esc(row.last_paired_at || "-")}</td>
      <td><div class="rowactions">
        <button class="action" data-pairing-list-action="copy" data-pairing-id="${esc(row.pairing_id)}">复制</button>
        <button class="action" data-pairing-list-action="toggle" data-pairing-id="${esc(row.pairing_id)}" data-enable="${row.enabled ? "0" : "1"}">${row.enabled ? "停用" : "启用"}</button>
        <button class="action danger" data-pairing-list-action="delete" data-pairing-id="${esc(row.pairing_id)}">删除</button>
      </div></td>
    </tr>`).join("") : '<tr><td colspan="7">暂无配对码，请先创建。</td></tr>';
  }

  async function loadCanonicalExtensions(force = false) {
    if (renderInFlight && !force) return renderInFlight;
    const task = (async () => {
      try {
        const data = await api("/api/admin/extensions");
        extensionSnapshot = Array.isArray(data.clients) ? data.clients : [];
        renderPairings(Array.isArray(data.pairing_codes) ? data.pairing_codes : []);
        renderWorkerRows(extensionSnapshot);
        if (typeof globalThis.status === "function") status(`v${document.documentElement.dataset.chat2apiRuntimeVersion || "0.22.40"}`, "muted");
        return data;
      } catch (error) {
        const {table} = tableParts();
        if (table) table.style.visibility = "";
        if (typeof globalThis.status === "function") status("Worker 列表加载失败：" + String(error?.message || error), "bad");
        throw error;
      }
    })();
    renderInFlight = task;
    try { return await task; } finally { if (renderInFlight === task) renderInFlight = null; }
  }

  function isCanonical() {
    const {body, headerRow} = tableParts();
    if (!body || !headerRow) return true;
    const headers = [...headerRow.children].filter(node => KNOWN_KEYS.has(String(node.dataset?.chat2apiColumnKey || "")));
    if (headers.length !== COLUMNS.length) return false;
    for (const tr of body.rows) {
      if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) continue;
      if (!tr.dataset.chat2apiCanonicalWorkerRow) return false;
      if (!DEFAULT_ORDER.every(key => Boolean(keyedChild(tr, key)))) return false;
    }
    return true;
  }

  function queueCanonicalRepair() {
    if (canonicalizing || repairQueued || !extensionSnapshot) return;
    repairQueued = true;
    queueMicrotask(() => {
      repairQueued = false;
      if (!canonicalizing && !isCanonical() && extensionSnapshot) renderWorkerRows(extensionSnapshot);
    });
  }

  function observeLegacyRebuilds() {
    const {body, headerRow} = tableParts();
    if (typeof MutationObserver !== "function") return;
    if (body) new MutationObserver(queueCanonicalRepair).observe(body, {childList: true});
    if (headerRow) new MutationObserver(queueCanonicalRepair).observe(headerRow, {childList: true});
  }

  function activateExtensionView() {
    document.querySelectorAll(".view").forEach(node => node.classList.remove("active"));
    document.querySelectorAll(".nav button").forEach(node => node.classList.toggle("active", node.dataset.view === "extensions"));
    document.getElementById("view-extensions")?.classList.add("active");
    const pageTitle = document.getElementById("pageTitle");
    if (pageTitle) pageTitle.textContent = "Worker管理";
    if (location.hash !== "#extensions") location.hash = "extensions";
  }

  function installCanonicalShowOwner() {
    const baseShow = typeof globalThis.show === "function" ? globalThis.show : null;
    if (!baseShow || baseShow.__chat2apiCanonicalWorkerListV59) return;
    const wrapped = async viewName => {
      if (viewName !== "extensions") return baseShow(viewName);
      const gate = document.getElementById("adminLoginGate");
      if (gate && gate.style.display !== "none") return;
      activateExtensionView();
      return loadCanonicalExtensions(true);
    };
    wrapped.__chat2apiCanonicalWorkerListV59 = true;
    globalThis.show = wrapped;
  }

  function ensureSettingsButton() {
    const body = document.getElementById("extensionDeviceBody");
    const panel = body?.closest(".panel");
    const heading = panel?.querySelector("h3");
    if (!heading) return null;
    if (!/Worker列表|扩展列表|绑定设备/.test(String(heading.textContent || ""))) heading.textContent = "Worker列表";
    else heading.textContent = "Worker列表";
    let button = document.getElementById("extensionColumnSettingsButton");
    if (!button) {
      button = document.createElement("button");
      button.id = "extensionColumnSettingsButton";
      button.className = "action";
      button.textContent = "⚙";
      button.title = "Worker列表列设置";
      button.style.marginLeft = "8px";
      heading.appendChild(button);
    }
    return button;
  }

  function ensureMenu() {
    let backdrop = document.getElementById("extensionColumnSettingsBackdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.id = "extensionColumnSettingsBackdrop";
      backdrop.style.cssText = "position:fixed;inset:0;z-index:300;display:none;align-items:center;justify-content:center;padding:24px;background:rgba(2,6,23,.72);backdrop-filter:blur(2px)";
      backdrop.addEventListener("mousedown", event => { if (event.target === backdrop) closeMenu(); });
      document.body.appendChild(backdrop);
    }
    let menu = document.getElementById("extensionColumnSettingsMenu");
    if (!menu) {
      menu = document.createElement("div");
      menu.id = "extensionColumnSettingsMenu";
      menu.tabIndex = -1;
      menu.style.cssText = "width:min(980px,calc(100vw - 48px));max-height:min(82vh,760px);display:flex;flex-direction:column;overflow:hidden;border:1px solid rgba(148,163,184,.28);border-radius:16px;background:#0f172a;box-shadow:0 28px 90px rgba(0,0,0,.55)";
      menu.addEventListener("mousedown", event => event.stopPropagation());
      backdrop.appendChild(menu);
    }
    return menu;
  }

  function closeMenu() {
    const backdrop = document.getElementById("extensionColumnSettingsBackdrop");
    if (backdrop) backdrop.style.display = "none";
    if (menuOpen) document.body.style.overflow = bodyOverflowBeforeModal;
    menuOpen = false;
  }

  function moveColumn(key, delta) {
    const active = structuredClone(loadPrefs());
    const index = active.order.indexOf(key);
    const next = index + delta;
    if (index < 0 || next < 0 || next >= active.order.length) return;
    [active.order[index], active.order[next]] = [active.order[next], active.order[index]];
    savePrefs(active);
    applyLayout();
    renderMenu();
  }

  function setVisible(key, visible) {
    const active = structuredClone(loadPrefs());
    active.visible[key] = Boolean(visible);
    savePrefs(active);
    applyLayout();
    renderMenu();
  }

  function renderMenu() {
    const menu = ensureMenu();
    const active = loadPrefs();
    const labels = new Map(COLUMNS.map(item => [item.key, item.label]));
    const visibleCount = active.order.filter(key => active.visible[key] !== false).length;
    const cards = active.order.map((key, index) => `<div style="display:flex;flex-direction:column;gap:10px;min-width:0;padding:12px;border:1px solid rgba(148,163,184,.16);border-radius:11px;background:rgba(15,23,42,.72)">
      <div style="display:flex;align-items:center;gap:9px"><span style="min-width:30px;padding:3px 6px;text-align:center;border-radius:999px;background:rgba(59,130,246,.16);color:#93c5fd;font-size:11px">${String(index + 1).padStart(2, "0")}</span>
      <label style="display:flex;align-items:center;gap:8px;flex:1;font-weight:600"><input type="checkbox" data-column-visible="${key}" ${active.visible[key] !== false ? "checked" : ""}><span>${esc(labels.get(key) || key)}</span></label></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px"><button class="action" data-column-move="${key}" data-delta="-1" ${index === 0 ? "disabled" : ""}>← 前移</button><button class="action" data-column-move="${key}" data-delta="1" ${index === active.order.length - 1 ? "disabled" : ""}>后移 →</button></div>
    </div>`).join("");
    menu.innerHTML = `<div style="display:flex;justify-content:space-between;gap:16px;padding:18px 20px 14px;border-bottom:1px solid rgba(148,163,184,.16)"><div><div style="font-size:17px;font-weight:700">Worker列表列设置</div><div class="muted" style="font-size:12px;margin-top:5px">只保留当前有效列；旧并发列、旧备用窗口列与绑定 API Key 数列已永久移除。</div></div><div style="display:flex;gap:8px"><button class="action" data-reset-columns>恢复默认</button><button class="action" data-close-columns>✕</button></div></div>
      <div class="muted" style="padding:10px 20px 0;font-size:12px">已显示 ${visibleCount} / ${active.order.length} 列</div>
      <div style="overflow:auto;padding:14px 20px 18px"><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px">${cards}</div></div>
      <div style="display:flex;justify-content:flex-end;padding:12px 20px;border-top:1px solid rgba(148,163,184,.16)"><button class="action" data-close-columns>完成</button></div>`;
  }

  function openMenu() {
    const menu = ensureMenu();
    renderMenu();
    if (!menuOpen) bodyOverflowBeforeModal = document.body.style.overflow;
    menuOpen = true;
    document.body.style.overflow = "hidden";
    const backdrop = document.getElementById("extensionColumnSettingsBackdrop");
    if (backdrop) backdrop.style.display = "flex";
    requestAnimationFrame(() => menu.focus());
  }

  async function pairingAction(button) {
    const action = button.dataset.pairingListAction || "";
    const id = button.dataset.pairingId || "";
    if (!id) return;
    if (action === "copy") {
      const data = await api(`/api/admin/pairing-codes/${encodeURIComponent(id)}/secret`);
      await navigator.clipboard.writeText(data.code || "");
      if (typeof globalThis.status === "function") status(data.rotated ? "旧配对码已轮换并复制" : "配对码已复制", "ok");
      if (data.rotated) await loadCanonicalExtensions(true);
      return;
    }
    if (action === "toggle") {
      await api(`/api/admin/pairing-codes/${encodeURIComponent(id)}`, {method: "PATCH", body: {enabled: button.dataset.enable === "1"}});
      await loadCanonicalExtensions(true);
      return;
    }
    if (action === "delete") {
      if (!confirm("确定删除这个配对码？已绑定 Worker 不会因此立即断开，但以后重新绑定需要新配对码。")) return;
      await api(`/api/admin/pairing-codes/${encodeURIComponent(id)}`, {method: "DELETE"});
      await loadCanonicalExtensions(true);
    }
  }

  async function workerAction(button) {
    const action = button.dataset.workerListAction || "";
    const id = button.dataset.clientId || "";
    if (!id) return;
    if (action === "disconnect") {
      if (!confirm("断开该 Worker 并禁止它自动接入？之后可点击“连接”恢复。")) return;
      await api(`/api/admin/extensions/${encodeURIComponent(id)}/disconnect`, {method: "POST"});
    } else if (action === "enable") {
      await api(`/api/admin/extensions/${encodeURIComponent(id)}/enable`, {method: "POST"});
    } else if (action === "delete") {
      const online = button.dataset.online === "1";
      const text = online
        ? "该 Worker 当前在线。删除会立即断开并删除设备凭据与粘性路由，以后必须重新配对。确定继续？"
        : "删除后将移除设备凭据与粘性路由，以后必须重新配对。确定继续？";
      if (!confirm(text)) return;
      await api(`/api/admin/extensions/${encodeURIComponent(id)}`, {method: "DELETE"});
    } else return;
    await loadCanonicalExtensions(true);
  }

  function installActions() {
    document.addEventListener("click", event => {
      const target = event.target;
      const settings = target?.closest?.("#extensionColumnSettingsButton");
      if (settings) { event.preventDefault(); openMenu(); return; }
      const close = target?.closest?.("[data-close-columns]");
      if (close) { event.preventDefault(); closeMenu(); return; }
      const reset = target?.closest?.("[data-reset-columns]");
      if (reset) { event.preventDefault(); savePrefs(defaultPrefs()); applyLayout(); renderMenu(); return; }
      const visible = target?.closest?.("[data-column-visible]");
      if (visible) { setVisible(visible.dataset.columnVisible, visible.checked); return; }
      const move = target?.closest?.("[data-column-move]");
      if (move) { event.preventDefault(); moveColumn(move.dataset.columnMove, Number(move.dataset.delta || 0)); return; }
      const pairing = target?.closest?.("[data-pairing-list-action]");
      if (pairing) { event.preventDefault(); pairingAction(pairing).catch(error => status(String(error?.message || error), "bad")); return; }
      const worker = target?.closest?.("[data-worker-list-action]");
      if (worker) { event.preventDefault(); workerAction(worker).catch(error => status(String(error?.message || error), "bad")); }
    }, true);

    document.addEventListener("keydown", event => { if (event.key === "Escape" && menuOpen) closeMenu(); });

    const create = document.getElementById("createPairing");
    if (create) create.onclick = async () => {
      try {
        const name = document.getElementById("pairingName")?.value.trim() || "Chrome 扩展";
        const data = await api("/api/admin/pairing-codes", {method: "POST", body: {name}});
        const value = document.getElementById("pairingCodeValue");
        if (value) value.textContent = data.code || "";
        document.getElementById("pairingSecret")?.classList.remove("hidden");
        await loadCanonicalExtensions(true);
      } catch (error) { status(String(error?.message || error), "bad"); }
    };
    const copy = document.getElementById("copyPairingCode");
    if (copy) copy.onclick = () => navigator.clipboard.writeText(document.getElementById("pairingCodeValue")?.textContent || "");
  }

  function boot() {
    const {table} = tableParts();
    if (table) table.style.visibility = "hidden";
    ensureCompactWorkerSettingsStyle();
    loadPrefs();
    ensureSettingsButton();
    installCanonicalShowOwner();
    installActions();
    observeLegacyRebuilds();
    globalThis.chat2apiReloadCanonicalWorkerListV59 = () => loadCanonicalExtensions(true);
    globalThis.__CHAT2API_CANONICAL_WORKER_LIST_V59__ = {
      version: VERSION,
      column_schema_revision: COLUMN_SCHEMA_REVISION,
      columns: [...DEFAULT_ORDER],
      removed_columns: ["concurrency", "reserve_windows", "platform", "bound_api_keys", "occupied_windows"],
      structural_owner: "admin_extension_columns",
      legacy_renderers_bypassed: true,
    };
    if ((location.hash || "").slice(1) === "extensions") {
      activateExtensionView();
      loadCanonicalExtensions(true).catch(() => {});
    } else if (table) {
      table.style.visibility = "";
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once: true});
  else boot();
})();
