(() => {
  const VERSION = "0.21.13";
  const STORAGE_KEY = "chat2api.extensionColumns.v1";
  const BASE_KEYS = ["client_id", "device_id", "version", "account_type", "status", "concurrency", "last_seen", "actions"];
  const COLUMNS = [
    {key: "client_id", label: "扩展 ID"},
    {key: "device_id", label: "设备标识"},
    {key: "version", label: "版本"},
    {key: "account_type", label: "账户类型"},
    {key: "status", label: "状态"},
    {key: "concurrency", label: "API 调用数（实时并发）"},
    {key: "last_seen", label: "最后在线"},
    {key: "actions", label: "操作"},
    {key: "platform", label: "平台"},
    {key: "network", label: "网络"},
    {key: "chatgpt", label: "ChatGPT"},
    {key: "reserve_windows", label: "备用窗口"},
  ];
  const KNOWN_KEYS = new Set(COLUMNS.map(item => item.key));
  const LABEL_TO_KEY = new Map([
    ["扩展 ID", "client_id"],
    ["设备标识", "device_id"],
    ["版本", "version"],
    ["账户类型", "account_type"],
    ["状态", "status"],
    ["绑定 API Key 数", "concurrency"],
    ["API 调用数（实时并发）", "concurrency"],
    ["最后在线", "last_seen"],
    ["操作", "actions"],
    ["平台", "platform"],
    ["网络", "network"],
    ["ChatGPT", "chatgpt"],
    ["备用窗口", "reserve_windows"],
  ]);

  let menuOpen = false;
  let observedTable = null;
  let tableObserver = null;
  let refreshScheduled = false;
  let bodyOverflowBeforeModal = "";

  function defaultPrefs() {
    return {
      order: COLUMNS.map(item => item.key),
      visible: Object.fromEntries(COLUMNS.map(item => [item.key, true])),
    };
  }

  function normalizePrefs(raw) {
    const defaults = defaultPrefs();
    const seen = new Set();
    const order = [];
    const candidate = Array.isArray(raw?.order) ? [...raw.order] : [];
    if (candidate.length && !candidate.includes("account_type")) {
      const versionIndex = candidate.indexOf("version");
      candidate.splice(versionIndex + 1, 0, "account_type");
    }
    for (const key of candidate) {
      if (!KNOWN_KEYS.has(key) || seen.has(key)) continue;
      seen.add(key);
      order.push(key);
    }
    for (const key of defaults.order) {
      if (!seen.has(key)) order.push(key);
    }
    const visible = {...defaults.visible};
    if (raw?.visible && typeof raw.visible === "object") {
      for (const key of defaults.order) {
        if (typeof raw.visible[key] === "boolean") visible[key] = raw.visible[key];
      }
    }
    return {order, visible};
  }

  function loadPrefs() {
    try {
      return normalizePrefs(JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"));
    } catch (_) {
      return defaultPrefs();
    }
  }

  function savePrefs(prefs) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(normalizePrefs(prefs))); } catch (_) {}
  }

  function tableParts() {
    const body = document.getElementById("extensionDeviceBody");
    const table = body?.closest("table") || null;
    const headerRow = table?.querySelector("thead tr") || null;
    return {body, table, headerRow};
  }

  function headerKey(th) {
    const explicit = String(th?.dataset?.chat2apiColumnKey || "").trim();
    if (KNOWN_KEYS.has(explicit)) return explicit;
    const health = String(th?.dataset?.chat2apiHealthColumn || "").trim();
    if (KNOWN_KEYS.has(health)) return health;
    return LABEL_TO_KEY.get(String(th?.textContent || "").trim()) || "";
  }

  function markColumns() {
    const {body, headerRow} = tableParts();
    if (!body || !headerRow) return false;

    for (const th of headerRow.cells) {
      const key = headerKey(th);
      if (key) th.dataset.chat2apiColumnKey = key;
    }

    for (const tr of body.rows) {
      if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) continue;
      const hasBaseKeys = BASE_KEYS.every(key => tr.querySelector(`td[data-chat2api-column-key="${key}"]`));
      if (!hasBaseKeys) {
        for (let index = 0; index < Math.min(BASE_KEYS.length, tr.cells.length); index += 1) {
          const cell = tr.cells[index];
          const explicit = String(cell.dataset.chat2apiColumnKey || "").trim();
          const health = String(cell.dataset.chat2apiHealthCell || "").trim();
          if (!KNOWN_KEYS.has(explicit) && !KNOWN_KEYS.has(health)) {
            cell.dataset.chat2apiColumnKey = BASE_KEYS[index];
          }
        }
      }
      for (const cell of tr.cells) {
        const health = String(cell.dataset.chat2apiHealthCell || "").trim();
        if (KNOWN_KEYS.has(health)) cell.dataset.chat2apiColumnKey = health;
      }
    }
    return true;
  }

  function keyedDirectChild(parent, key) {
    return [...(parent?.children || [])].find(node =>
      String(node.dataset?.chat2apiColumnKey || "").trim() === key,
    ) || null;
  }

  function reorderKnownChildren(parent, prefs) {
    if (!parent) return false;
    const current = [...parent.children];
    const orderedKnown = prefs.order.map(key => keyedDirectChild(parent, key)).filter(Boolean);
    const unknown = current.filter(node =>
      !KNOWN_KEYS.has(String(node.dataset?.chat2apiColumnKey || "").trim()),
    );
    const desired = [...orderedKnown, ...unknown];
    if (current.length === desired.length && current.every((node, index) => node === desired[index])) {
      return false;
    }
    const fragment = document.createDocumentFragment();
    for (const node of desired) fragment.appendChild(node);
    parent.appendChild(fragment);
    return true;
  }

  function applyVisibility(parent, prefs) {
    if (!parent) return;
    for (const key of prefs.order) {
      const node = keyedDirectChild(parent, key);
      if (!node) continue;
      const display = prefs.visible[key] === false ? "none" : "";
      if (node.style.display !== display) node.style.display = display;
    }
  }

  function applyLayout() {
    if (!markColumns()) return;
    const prefs = loadPrefs();
    const {body, headerRow} = tableParts();
    if (!body || !headerRow) return;

    reorderKnownChildren(headerRow, prefs);
    for (const tr of body.rows) {
      if (!(tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan"))) reorderKnownChildren(tr, prefs);
    }
    applyVisibility(headerRow, prefs);
    for (const tr of body.rows) {
      if (!(tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan"))) applyVisibility(tr, prefs);
    }

    const visibleCount = prefs.order.filter(
      key => prefs.visible[key] !== false && keyedDirectChild(headerRow, key),
    ).length;
    for (const tr of body.rows) {
      if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) {
        const next = Math.max(1, visibleCount);
        if (tr.cells[0].colSpan !== next) tr.cells[0].colSpan = next;
      }
    }
  }

  function moveColumn(key, delta) {
    const prefs = loadPrefs();
    const index = prefs.order.indexOf(key);
    const next = index + delta;
    if (index < 0 || next < 0 || next >= prefs.order.length) return;
    [prefs.order[index], prefs.order[next]] = [prefs.order[next], prefs.order[index]];
    savePrefs(prefs);
    applyLayout();
    renderMenu();
  }

  function setColumnVisible(key, visible) {
    const prefs = loadPrefs();
    prefs.visible[key] = Boolean(visible);
    savePrefs(prefs);
    applyLayout();
    renderMenu();
  }

  function resetLayout() {
    savePrefs(defaultPrefs());
    applyLayout();
    renderMenu();
  }

  function ensureMenu() {
    let backdrop = document.getElementById("extensionColumnSettingsBackdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.id = "extensionColumnSettingsBackdrop";
      backdrop.style.cssText = [
        "position:fixed",
        "inset:0",
        "z-index:300",
        "display:none",
        "align-items:center",
        "justify-content:center",
        "padding:24px",
        "background:rgba(2,6,23,.72)",
        "backdrop-filter:blur(2px)",
      ].join(";");
      backdrop.addEventListener("mousedown", event => {
        if (event.target === backdrop) closeMenu();
      });
      document.body.appendChild(backdrop);
    }

    let menu = document.getElementById("extensionColumnSettingsMenu");
    if (!menu) {
      menu = document.createElement("div");
      menu.id = "extensionColumnSettingsMenu";
      menu.setAttribute("role", "dialog");
      menu.setAttribute("aria-modal", "true");
      menu.setAttribute("aria-labelledby", "extensionColumnSettingsTitle");
      menu.tabIndex = -1;
      menu.style.cssText = [
        "width:min(980px,calc(100vw - 48px))",
        "max-width:980px",
        "max-height:min(82vh,760px)",
        "display:flex",
        "flex-direction:column",
        "overflow:hidden",
        "border:1px solid rgba(148,163,184,.28)",
        "border-radius:16px",
        "background:#0f172a",
        "box-shadow:0 28px 90px rgba(0,0,0,.55)",
      ].join(";");
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

  function openMenu() {
    const menu = ensureMenu();
    const backdrop = document.getElementById("extensionColumnSettingsBackdrop");
    renderMenu();
    if (!menuOpen) bodyOverflowBeforeModal = document.body.style.overflow;
    menuOpen = true;
    document.body.style.overflow = "hidden";
    if (backdrop) backdrop.style.display = "flex";
    requestAnimationFrame(() => menu.focus());
  }

  function renderMenu() {
    const menu = ensureMenu();
    const previousScroll = menu.querySelector("#extensionColumnSettingsBody")?.scrollTop || 0;
    const prefs = loadPrefs();
    const labelByKey = new Map(COLUMNS.map(item => [item.key, item.label]));
    const visibleCount = prefs.order.filter(key => prefs.visible[key] !== false).length;
    const cards = prefs.order.map((key, index) => {
      const label = labelByKey.get(key) || key;
      const checked = prefs.visible[key] !== false ? "checked" : "";
      const number = String(index + 1).padStart(2, "0");
      return `<div data-column-card="${key}" style="display:flex;flex-direction:column;gap:10px;min-width:0;padding:12px;border:1px solid rgba(148,163,184,.16);border-radius:11px;background:rgba(15,23,42,.72)">
        <div style="display:flex;align-items:center;gap:9px;min-width:0">
          <span style="flex:none;min-width:30px;padding:3px 6px;text-align:center;border-radius:999px;background:rgba(59,130,246,.16);color:#93c5fd;font-size:11px;font-variant-numeric:tabular-nums">${number}</span>
          <label style="display:flex;align-items:center;gap:8px;min-width:0;flex:1;cursor:pointer;font-size:13px;font-weight:600">
            <input type="checkbox" data-column-visible="${key}" ${checked}>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${label}">${label}</span>
          </label>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <button type="button" class="action" data-column-move="${key}" data-delta="-1" title="前移一位" ${index === 0 ? "disabled" : ""}>← 前移</button>
          <button type="button" class="action" data-column-move="${key}" data-delta="1" title="后移一位" ${index === prefs.order.length - 1 ? "disabled" : ""}>后移 →</button>
        </div>
      </div>`;
    }).join("");

    menu.innerHTML = `<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:18px 20px 14px;border-bottom:1px solid rgba(148,163,184,.16)">
        <div style="min-width:0">
          <div id="extensionColumnSettingsTitle" style="font-size:17px;font-weight:700">扩展列表列设置</div>
          <div class="muted" style="font-size:12px;margin-top:5px">勾选控制显示；编号代表当前列顺序，使用“前移 / 后移”调整。</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex:none">
          <button type="button" class="action" id="resetExtensionColumns">恢复默认</button>
          <button type="button" class="action" data-close-extension-columns title="关闭" aria-label="关闭列设置">✕</button>
        </div>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 20px 0;font-size:12px" class="muted">
        <span>已显示 ${visibleCount} / ${prefs.order.length} 列</span>
        <span>宽屏自动多列排列</span>
      </div>
      <div id="extensionColumnSettingsBody" style="flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding:14px 20px 18px">
        <div id="extensionColumnSettingsGrid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px">${cards}</div>
      </div>
      <div style="display:flex;align-items:center;justify-content:flex-end;gap:10px;padding:12px 20px;border-top:1px solid rgba(148,163,184,.16)">
        <button type="button" class="action" data-close-extension-columns>完成</button>
      </div>`;

    for (const input of menu.querySelectorAll("[data-column-visible]")) {
      input.addEventListener("change", event =>
        setColumnVisible(event.currentTarget.dataset.columnVisible, event.currentTarget.checked),
      );
    }
    for (const button of menu.querySelectorAll("[data-column-move]")) {
      button.addEventListener("click", event =>
        moveColumn(event.currentTarget.dataset.columnMove, Number(event.currentTarget.dataset.delta || 0)),
      );
    }
    for (const button of menu.querySelectorAll("[data-close-extension-columns]")) {
      button.addEventListener("click", closeMenu);
    }
    menu.querySelector("#resetExtensionColumns")?.addEventListener("click", resetLayout);
    const body = menu.querySelector("#extensionColumnSettingsBody");
    if (body) body.scrollTop = previousScroll;
  }

  function ensureSettingsButton() {
    if (document.getElementById("extensionColumnSettingsButton")) return true;
    const body = document.getElementById("extensionDeviceBody");
    const panel = body?.closest(".panel");
    if (!panel) return false;
    const heading = [...panel.querySelectorAll("h3")]
      .find(node => String(node.textContent || "").trim().startsWith("扩展列表"))
      || [...panel.querySelectorAll("h3")].find(node => String(node.textContent || "").trim().startsWith("绑定设备"))
      || panel.querySelector("h3");
    if (!heading) return false;

    const button = document.createElement("button");
    button.id = "extensionColumnSettingsButton";
    button.type = "button";
    button.className = "action";
    button.textContent = "⚙";
    button.title = "设置扩展列表显示列和排序";
    button.setAttribute("aria-label", "设置扩展列表列");
    button.style.cssText = "margin-left:8px;padding:2px 8px;min-width:32px;vertical-align:middle;font-size:15px";
    button.addEventListener("click", event => {
      event.stopPropagation();
      if (menuOpen) closeMenu(); else openMenu();
    });
    heading.appendChild(button);
    ensureMenu();
    return true;
  }

  function scheduleRefresh() {
    if (refreshScheduled) return;
    refreshScheduled = true;
    const run = () => {
      refreshScheduled = false;
      refreshUi();
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
    else setTimeout(run, 0);
  }

  function ensureObserver() {
    const {table} = tableParts();
    if (!table || typeof MutationObserver !== "function") return;
    if (observedTable === table && tableObserver) return;
    tableObserver?.disconnect();
    observedTable = table;
    tableObserver = new MutationObserver(() => scheduleRefresh());
    tableObserver.observe(table, {childList: true, subtree: true});
  }

  function refreshUi() {
    if (!ensureSettingsButton()) return;
    ensureObserver();
    applyLayout();
  }

  const baseLoadExtensions = typeof globalThis.loadExtensions === "function" ? globalThis.loadExtensions : null;
  if (baseLoadExtensions && !baseLoadExtensions.__chat2apiColumnLayoutV2113) {
    const wrappedLoadExtensions = async (...args) => {
      const result = await baseLoadExtensions(...args);
      scheduleRefresh();
      return result;
    };
    wrappedLoadExtensions.__chat2apiColumnLayoutV2113 = true;
    globalThis.loadExtensions = wrappedLoadExtensions;
  }

  const baseShow = typeof globalThis.show === "function" ? globalThis.show : null;
  if (baseShow && !baseShow.__chat2apiColumnLayoutV2113) {
    const wrappedShow = async (...args) => {
      const result = await baseShow(...args);
      if (args[0] === "extensions") scheduleRefresh();
      return result;
    };
    wrappedShow.__chat2apiColumnLayoutV2113 = true;
    globalThis.show = wrappedShow;
  }

  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && menuOpen) closeMenu();
  });

  document.documentElement.dataset.chat2apiExtensionColumnLayoutVersion = VERSION;
  refreshUi();
  scheduleRefresh();
})();