(() => {
  const VERSION = "0.22.113-worker-list-v157";
  const COLUMN_SCHEMA_REVISION = 152;
  const STORAGE_KEY = "chat2api.extensionColumns.v3";
  const LEGACY_STORAGE_KEY = "chat2api.extensionColumns.v2";
  const COLUMNS = [
    {key: "client_id", label: "Worker ID"},
    {key: "device_id", label: "设备标识"},
    {key: "account_type", label: "账户类型"},
    {key: "status", label: "状态"},
    {key: "worker_settings", label: "并发 / 备用设置"},
    {key: "last_seen", label: "最后在线"},
    {key: "chatgpt", label: "ChatGPT"},
    {key: "actions", label: "操作"},
    {key: "device_name", label: "设备名称"},
    {key: "occupancy", label: "请求 / 备用窗口"},
  ];
  const KNOWN_KEYS = new Set(COLUMNS.map(item => item.key));
  const DEFAULT_ORDER = COLUMNS.map(item => item.key);
  const LEGACY_KEY_MAP = new Map([["platform", "worker_settings"]]);
  const REMOVED_KEYS = new Set(["concurrency", "reserve_windows", "bound_api_keys", "occupied_windows", "version", "network"]);

  let prefs = null;
  let menuOpen = false;
  let bodyOverflowBeforeModal = "";
  let renderInFlight = null;
  let extensionSnapshot = null;
  let truthSnapshot = null;
  // Last *verified* physical standby observations, never configured max_windows.
  // A timed-out probe must not erase valid truth, and cached truth is labeled stale.
  const verifiedStandby = new Map();
  const STANDBY_GRACE_MS = 45000;
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
    return {order:[...DEFAULT_ORDER], visible:Object.fromEntries(DEFAULT_ORDER.map(key => [key, true]))};
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
    for (const key of DEFAULT_ORDER) if (!seen.has(key)) order.push(key);
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
      if (current) return (prefs = normalizePrefs(JSON.parse(current)));
      const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
      prefs = legacy ? normalizePrefs(JSON.parse(legacy)) : defaultPrefs();
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
      return prefs;
    } catch (_) {
      return (prefs = defaultPrefs());
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

  function keyedChild(parent, key) {
    return [...(parent?.children || [])].find(node => String(node.dataset?.chat2apiColumnKey || "") === key) || null;
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
      const owner = key === "worker_settings" ? ' data-chat2api-structural-owner="worker-settings-v152"' : "";
      const title = key === "worker_settings"
        ? ' title="并发=同时执行请求上限；备用=持续维持的未分配可接待窗口数量"'
        : key === "occupancy" ? ' title="正在执行请求 / 实时核验的未分配备用窗口"' : "";
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
    const value = String(meta.network_probe_status || "unknown");
    const country = String(meta.network_country_code || "").trim();
    if (value === "external") return {text:`外网${country ? ` · ${country}` : ""}`, cls:"ok"};
    if (value === "china-mainland") return {text:`中国大陆${country ? ` · ${country}` : ""}`, cls:"warnText"};
    if (value === "offline") return {text:"浏览器离线", cls:"bad"};
    if (value === "error") return {text:"探测失败", cls:"warnText"};
    return {text:"未知", cls:"warnText"};
  }

  function chatgptLabel(row) {
    const meta = row?.metadata || {};
    const value = String(meta.chatgpt_login_state || "unknown");
    if (value === "ready") return {text:"已登录", cls:meta.chatgpt_login_composer_ready === true ? "ok" : "warnText"};
    if (value === "login_required") return {text:"未登录", cls:"bad"};
    if (value === "checking") return {text:"检测中", cls:"warnText"};
    return {text:"未知", cls:"warnText"};
  }

  function limitEditor(row) {
    const id = String(row?.client_id || "");
    const concurrency = Math.max(1, Math.min(32, Number(row?.max_concurrency || row?.capacity?.limit_units || 1)));
    const windows = Math.max(1, Math.min(32, Number(row?.max_windows || 1)));
    return `<div data-v121-worker-limits="${esc(id)}" style="position:relative;display:inline-flex;align-items:center;gap:7px;white-space:nowrap;min-width:78px">
      <strong data-v121-limit-summary style="font-variant-numeric:tabular-nums">${concurrency}/${windows}</strong>
      <button class="action" type="button" data-v121-edit-limits title="编辑并发 / 备用设置" aria-label="编辑并发 / 备用设置" style="padding:4px 7px;min-width:30px">✎</button>
      <div data-v121-limit-popover hidden style="position:absolute;right:0;top:calc(100% + 7px);z-index:80;min-width:250px;padding:12px;border:1px solid #334155;border-radius:10px;background:#111827;box-shadow:0 14px 34px rgba(0,0,0,.38);white-space:normal">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <label style="display:grid;gap:5px;font-size:12px">并发<input data-v121-concurrency type="number" min="1" max="32" value="${concurrency}" style="width:100%;padding:7px 8px"></label>
          <label style="display:grid;gap:5px;font-size:12px">备用<input data-v121-windows type="number" min="1" max="32" value="${windows}" style="width:100%;padding:7px 8px"></label>
        </div>
        <div style="display:flex;align-items:center;justify-content:flex-end;gap:7px;margin-top:10px">
          <span class="muted" data-v121-limit-note style="font-size:11px;margin-right:auto"></span>
          <button class="action" type="button" data-v121-cancel-limits>取消</button>
          <button class="action good" type="button" data-v121-save-limits>保存</button>
        </div>
      </div>
    </div>`;
  }

  function truthByClient(payload) {
    const result = new Map();
    const now = Date.now();
    const authoritative = Number(payload?.truth_revision || 0) >= 89;
    const reportedIds = new Set();
    for (const worker of Array.isArray(payload?.workers) ? payload.workers : []) {
      const id = String(worker?.client_id || "");
      if (!id) continue;
      reportedIds.add(id);
      const status = String(worker?.truth_status || "unverified");
      const countRaw = worker?.standby_window_count;
      const liveVerified = authoritative && worker?.live_verified === true
        && worker?.chatgpt_routing_ready === true
        && countRaw !== null && countRaw !== undefined
        && Number.isFinite(Number(countRaw)) && Number(countRaw) >= 0;
      if (liveVerified) {
        const standby = Math.max(0, Number(countRaw));
        const previous = verifiedStandby.get(id);
        const snapshotAt = Number(worker?.snapshot_updated_at_ms || 0);
        // Ignore an out-of-order response with an older physical snapshot.
        if (previous && snapshotAt > 0 && previous.snapshotAt > snapshotAt) {
          result.set(id, {liveVerified:false, standby:previous.standby, status:"last-verified"});
        } else {
          verifiedStandby.set(id, {standby, at:now, snapshotAt});
          result.set(id, {liveVerified:true, standby, status:"verified"});
        }
      } else if (status === "refresh-timeout" || status === "unverified") {
        // A timeout does not establish zero (or any new count).
        const previous = verifiedStandby.get(id);
        if (previous && now - previous.at <= STANDBY_GRACE_MS)
          result.set(id, {liveVerified:false, standby:previous.standby, status:"last-verified"});
      } else {
        // Offline, revoked, login-required or unsupported: never reuse cached truth.
        verifiedStandby.delete(id);
      }
    }
    if (!payload) {
      // Transport/API error: the extension list still decides whether a Worker is online.
      for (const [id, previous] of verifiedStandby) {
        if (now - previous.at <= STANDBY_GRACE_MS)
          result.set(id, {liveVerified:false, standby:previous.standby, status:"last-verified"});
      }
    }
    for (const [id, previous] of verifiedStandby) {
      if (now - previous.at > STANDBY_GRACE_MS) verifiedStandby.delete(id);
    }
    return result;
  }

  function occupancy(row, info = null) {
    const capacity = row?.capacity && typeof row.capacity === "object" ? row.capacity : {};
    const usedRaw = capacity.used_units ?? row?.active_api_calls;
    const usedNumber = usedRaw == null || String(usedRaw).trim() === "" ? NaN : Number(usedRaw);
    const usedKnown = Number.isFinite(usedNumber) && usedNumber >= 0;
    const used = usedKnown ? Math.max(0, usedNumber) : null;
    const usedText = used === null ? "?" : String(used);
    const queueRaw = capacity.queued_requests;
    const queueNumber = queueRaw == null || String(queueRaw).trim() === "" ? NaN : Number(queueRaw);
    const queued = Number.isFinite(queueNumber) && queueNumber >= 0 ? queueNumber : null;
    const enabled = row?.connection_enabled !== false && row?.online === true;
    const standbyKnown = enabled && info?.liveVerified === true
      && info?.standby != null && Number.isFinite(Number(info.standby));
    const lastVerified = enabled && !standbyKnown && info?.status === "last-verified"
      && info?.standby != null && Number.isFinite(Number(info.standby));
    const standbyText = standbyKnown || lastVerified ? String(Math.max(0, Number(info.standby))) : "?";
    const color = standbyKnown ? "#22c55e" : "#f59e0b";
    const label = lastVerified ? ' <small data-chat2api-standby-stale="1" style="font-size:11px">上次核验</small>' : "";
    return {
      html: `${usedText} / <span data-chat2api-live-standby-count="1" data-chat2api-standby-source="${standbyKnown ? "live" : lastVerified ? "cached" : "unknown"}" style="color:${color};font-weight:700">${standbyText}</span>${label}${queued != null && queued > 0 ? ` · 排队 ${queued}` : ""}`,
      title: `正在执行请求 ${usedText}；备用窗口 ${standbyText}${standbyKnown ? "（实时物理核验，已排除正在接待及5分钟租约窗口）" : lastVerified ? "（上次核验成功的数量，本次尚未获得实时数据；此值不可用于实时调度判断）" : "（尚未实时核验）"}${queued != null && queued > 0 ? `；排队 ${queued}` : ""}`,
      cls: usedKnown && used > 0 ? "warnText" : "muted",
    };
  }

  function workerActions(row) {
    const id = esc(row.client_id || "");
    const connect = row.connection_enabled === false
      ? `<button class="action good" data-worker-list-action="enable" data-client-id="${id}">启用</button>`
      : `<button class="action danger" data-worker-list-action="disconnect" data-client-id="${id}">禁用</button>`;
    return `<div class="rowactions">${connect}<button class="action" type="button" data-worker-login-edit="${id}">登录</button><button class="action" type="button" data-windows-worker-initialize="${id}">初始化</button><button class="action" type="button" data-windows-worker-remote="${id}">远程</button><button class="action danger" data-worker-list-action="delete" data-client-id="${id}" data-online="${row.online ? "1" : "0"}">删除</button></div>`;
  }

  function rowHtml(row, truthInfo = null) {
    const login = chatgptLabel(row);
    const occupied = occupancy(row, truthInfo);
    const clientId = esc(row.client_id || "");
    const deviceName = String(row?.device_name || "").trim();
    const deviceNameHtml = deviceName
      ? `<span title="${esc(row?.device_code_id || row?.pairing_id || "")}">${esc(deviceName)}</span>`
      : '<span class="muted">-</span>';
    return `<tr data-chat2api-canonical-worker-row="1" data-client-id="${clientId}">
      <td data-chat2api-column-key="client_id"><code>${clientId}</code></td>
      <td data-chat2api-column-key="device_id"><code>${esc(row.device_id || row.metadata?.device_id || "-")}</code></td>
            <td data-chat2api-column-key="account_type">${accountPill(row)}</td>
      <td data-chat2api-column-key="status">${statusPill(row)}</td>
      <td data-chat2api-column-key="worker_settings" data-chat2api-structural-owner="worker-settings-v152">${limitEditor(row)}</td>
      <td data-chat2api-column-key="last_seen">${typeof fmtTime === "function" ? fmtTime(row.last_seen_at) : esc(row.last_seen_at || "-")}</td>
            <td data-chat2api-column-key="chatgpt" data-chat2api-health-cell="chatgpt" class="${login.cls}">${esc(login.text)}</td>
      <td data-chat2api-column-key="actions">${workerActions(row)}</td>
      <td data-chat2api-column-key="device_name">${deviceNameHtml}</td>
      <td data-chat2api-column-key="occupancy" class="${occupied.cls}" title="${esc(occupied.title)}">${occupied.html}</td>
    </tr>`;
  }

  function renderWorkerRows(rows, truthPayload = null) {
    const {body, headerRow, table} = tableParts();
    if (!body || !headerRow) return;
    const truth = truthByClient(truthPayload);
    canonicalizing = true;
    try {
      const header = canonicalHeaderHtml();
      if (headerRow.innerHTML !== header) headerRow.innerHTML = header;
      body.innerHTML = rows.length
        ? rows.map(row => rowHtml(row, truth.get(String(row?.client_id || "")) || null)).join("")
        : `<tr><td colspan="${COLUMNS.length}" class="muted">暂无 Worker。</td></tr>`;
      applyLayout();
      document.documentElement.dataset.chat2apiWorkerListReady = "1";
      document.documentElement.dataset.chat2apiWorkerListVersion = VERSION;
      document.documentElement.dataset.chat2apiWorkerColumnSchemaRevision = String(COLUMN_SCHEMA_REVISION);
      document.documentElement.dataset.chat2apiWorkerListSingleRenderer = "1";
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
        <button class="action" data-pairing-list-action="rename" data-pairing-id="${esc(row.pairing_id)}" data-pairing-name="${esc(row.name || "")}">改名</button>
        <button class="action" data-pairing-list-action="copy" data-pairing-id="${esc(row.pairing_id)}">复制</button>
        <button class="action" data-pairing-list-action="toggle" data-pairing-id="${esc(row.pairing_id)}" data-enable="${row.enabled ? "0" : "1"}">${row.enabled ? "停用" : "启用"}</button>
        <button class="action danger" data-pairing-list-action="delete" data-pairing-id="${esc(row.pairing_id)}">删除</button>
      </div></td>
    </tr>`).join("") : '<tr><td colspan="7">暂无配对码，请先创建。</td></tr>';
  }

  async function loadCanonicalExtensions(force = false) {
    // Coalesce explicit and automatic refreshes: older replies cannot overwrite newer truth.
    if (renderInFlight) return renderInFlight;
    const task = (async () => {
      try {
        const [data, truth] = await Promise.all([
          api("/api/admin/extensions"),
          api("/api/admin/window-manager").catch(error => {
            console.warn("canonical Worker standby truth refresh failed", error);
            return null;
          }),
        ]);
        extensionSnapshot = (Array.isArray(data.clients) ? data.clients : []).filter(row => !row.metadata?.linux_worker_id && String(row.metadata?.platform || "").toLowerCase() !== "linux");
        truthSnapshot = truth;
        renderPairings(Array.isArray(data.pairing_codes) ? data.pairing_codes : []);
        renderWorkerRows(extensionSnapshot, truthSnapshot);
        globalThis.__chat2apiWindowsSnapshotV156 = {at:Date.now(), data};
        document.dispatchEvent(new Event("chat2api:extensions-loaded"));
        if (typeof globalThis.status === "function") status(document.documentElement.dataset.chat2apiRuntimeVersion ? `v${document.documentElement.dataset.chat2apiRuntimeVersion}` : "运行时版本未知", "muted");
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
      if (!canonicalizing && !isCanonical() && extensionSnapshot) renderWorkerRows(extensionSnapshot, truthSnapshot);
    });
  }

  function observeLegacyRebuilds() {
    const {body, headerRow} = tableParts();
    if (typeof MutationObserver !== "function") return;
    if (body) new MutationObserver(queueCanonicalRepair).observe(body, {childList:true});
    if (headerRow) new MutationObserver(queueCanonicalRepair).observe(headerRow, {childList:true});
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
    if (!baseShow || baseShow.__chat2apiCanonicalWorkerListV152) return;
    const wrapped = async viewName => {
      if (viewName !== "extensions") return baseShow(viewName);
      const gate = document.getElementById("adminLoginGate");
      if (gate && gate.style.display !== "none") return;
      activateExtensionView();
      return loadCanonicalExtensions(true);
    };
    wrapped.__chat2apiCanonicalWorkerListV152 = true;
    globalThis.show = wrapped;
  }

  function ensureSettingsButton() {
    const body = document.getElementById("extensionDeviceBody");
    const panel = body?.closest(".panel");
    const heading = panel?.querySelector("h3");
    if (!heading) return null;
    heading.textContent = "Worker列表";
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
    if (action === "rename") {
      const next = prompt("设备名称", String(button.dataset.pairingName || ""));
      if (next == null) return;
      const name = next.trim();
      if (!name) throw new Error("设备名称不能为空");
      await api(`/api/admin/pairing-codes/${encodeURIComponent(id)}/name`, {method:"PATCH", body:{name}});
      await loadCanonicalExtensions(true);
      return;
    }
    if (action === "copy") {
      const data = await api(`/api/admin/pairing-codes/${encodeURIComponent(id)}/secret`);
      await navigator.clipboard.writeText(data.code || "");
      if (typeof globalThis.status === "function") status(data.rotated ? "旧配对码已轮换并复制" : "配对码已复制", "ok");
      if (data.rotated) await loadCanonicalExtensions(true);
      return;
    }
    if (action === "toggle") {
      await api(`/api/admin/pairing-codes/${encodeURIComponent(id)}`, {method:"PATCH", body:{enabled:button.dataset.enable === "1"}});
      await loadCanonicalExtensions(true);
      return;
    }
    if (action === "delete") {
      if (!confirm("确定删除这个配对码？已绑定 Worker 不会因此立即断开，但以后重新绑定需要新配对码。")) return;
      await api(`/api/admin/pairing-codes/${encodeURIComponent(id)}`, {method:"DELETE"});
      await loadCanonicalExtensions(true);
    }
  }

  async function workerAction(button) {
    const action = button.dataset.workerListAction || "";
    const id = button.dataset.clientId || "";
    if (!id) return;
    if (action === "disconnect") {
      if (!confirm("断开该 Worker 并禁止它自动接入？之后可点击“连接”恢复。")) return;
      await api(`/api/admin/extensions/${encodeURIComponent(id)}/disconnect`, {method:"POST"});
    } else if (action === "enable") {
      await api(`/api/admin/extensions/${encodeURIComponent(id)}/enable`, {method:"POST"});
    } else if (action === "delete") {
      const online = button.dataset.online === "1";
      const text = online ? "该 Worker 当前在线。删除会立即断开并删除设备凭据与粘性路由，以后必须重新配对。确定继续？" : "删除后将移除设备凭据与粘性路由，以后必须重新配对。确定继续？";
      if (!confirm(text)) return;
      await api(`/api/admin/extensions/${encodeURIComponent(id)}`, {method:"DELETE"});
    } else return;
    await loadCanonicalExtensions(true);
  }

  function installActions() {
    document.addEventListener("click", event => {
      const target = event.target;
      if (target?.closest?.("#extensionColumnSettingsButton")) { event.preventDefault(); openMenu(); return; }
      if (target?.closest?.("[data-close-columns]")) { event.preventDefault(); closeMenu(); return; }
      if (target?.closest?.("[data-reset-columns]")) { event.preventDefault(); savePrefs(defaultPrefs()); applyLayout(); renderMenu(); return; }
      const visible = target?.closest?.("[data-column-visible]");
      if (visible) { setVisible(visible.dataset.columnVisible, visible.checked); return; }
      const move = target?.closest?.("[data-column-move]");
      if (move) { event.preventDefault(); moveColumn(move.dataset.columnMove, Number(move.dataset.delta || 0)); return; }
      const pairing = target?.closest?.("[data-pairing-list-action]");
      if (pairing) { event.preventDefault(); pairingAction(pairing).catch(error => status(String(error?.message || error), "bad")); return; }
      const initialize=target?.closest?.("[data-windows-worker-initialize]");
      if(initialize){event.preventDefault();const id=initialize.dataset.windowsWorkerInitialize;if(!confirm("重新启动此 Windows Worker 的 Chrome 扩展运行时？运行中的请求会暂时中断。"))return;
        api("/api/admin/extensions/"+encodeURIComponent(id)+"/initialize",{method:"POST"}).then(()=>loadCanonicalExtensions(true)).catch(err=>alert(err.message));return;}
      const remote=target?.closest?.("[data-windows-worker-remote]");
      if(remote){event.preventDefault();alert("Windows Chrome 扩展目前没有远程桌面画面通道。本按钮不会打开登录页，也不会伪装为远程画面。请使用 Windows 远程桌面连接主机；Linux Worker 支持控制台实时远程。");return;}
      const worker = target?.closest?.("[data-worker-list-action]");
      if (worker) { event.preventDefault(); workerAction(worker).catch(error => status(String(error?.message || error), "bad")); }
    }, true);
    document.addEventListener("keydown", event => { if (event.key === "Escape" && menuOpen) closeMenu(); });

    const create = document.getElementById("createPairing");
    if (create) create.onclick = async () => {
      try {
        const name = document.getElementById("pairingName")?.value.trim() || "Chrome 扩展";
        const data = await api("/api/admin/pairing-codes", {method:"POST", body:{name}});
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
    loadPrefs();
    ensureSettingsButton();
    installCanonicalShowOwner();
    installActions();
    observeLegacyRebuilds();
    globalThis.chat2apiReloadCanonicalWorkerListV59 = () => loadCanonicalExtensions(true);
    globalThis.__CHAT2API_CANONICAL_WORKER_LIST_V59__ = {
      version: VERSION,
      column_schema_revision: COLUMN_SCHEMA_REVISION,
      columns:[...DEFAULT_ORDER],
      removed_columns:["concurrency", "reserve_windows", "platform", "bound_api_keys", "occupied_windows"],
      structural_owner:"admin_extension_columns",
      worker_settings_owner:"canonical-v152",
      window_truth_owner:"canonical-v152",
      legacy_renderers_bypassed:true,
      single_renderer:true,
      reload:loadCanonicalExtensions,
    };
    loadCanonicalExtensions(true).catch(() => {});
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once:true});
  else boot();
})();
