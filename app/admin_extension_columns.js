(() => {
  const VERSION = "0.21.9";
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
  ]);

  let menuOpen = false;
  let observedTable = null;
  let tableObserver = null;
  let refreshScheduled = false;

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

      const hasBaseKeys = BASE_KEYS.every(key =>
        tr.querySelector(`td[data-chat2api-column-key="${key}"]`),
      );
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
    const orderedKnown = prefs.order
      .map(key => keyedDirectChild(parent, key))
      .filter(Boolean);
    const unknown = current.filter(node =>
      !KNOWN_KEYS.has(String(node.dataset?.chat2apiColumnKey || "").trim()),
    );
    const desired = [...orderedKnown, ...unknown];
    if (
      current.length === desired.length
      && current.every((node, index) => node === desired[index])
    ) {
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
      if (!(tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan"))) {
        reorderKnownChildren(tr, prefs);
      }
    }

    applyVisibility(headerRow, prefs);
    for (const tr of body.rows) {
      if (!(tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan"))) {
        applyVisibility(tr, prefs);
      }
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
    let menu = document.getElementById("extensionColumnSettingsMenu");
    if (menu) return menu;
    menu = document.createElement("div");
    menu.id = "extensionColumnSettingsMenu";
    menu.style.cssText = [
      "position:fixed",
      "z-index:120",
      "display:none",
      "width:340px",
      "max-width:calc(100vw - 24px)",
      "max-height:min(70vh,620px)",
      "overflow:auto",
      "padding:12px",
      "border:1px solid rgba(148,163,184,.24)",
      "border-radius:12px",
      "background:#0f172a",
      "box-shadow:0 18px 55px rgba(0,0,0,.38)",
    ].join(";");
    menu.addEventListener("click", event => event.stopPropagation());
    document.body.appendChild(menu);
    return menu;
  }

  function positionMenu() {
    const button = document.getElementById("extensionColumnSettingsButton");
    const menu = document.getElementById("extensionColumnSettingsMenu");
    if (!button || !menu || !menuOpen) return;
    const rect = button.getBoundingClientRect();
    const width = Math.min(340, window.innerWidth - 24);
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
    const top = Math.min(rect.bottom + 8, window.innerHeight - 120);
    menu.style.width = `${width}px`;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  function closeMenu() {
    const menu = document.getElementById("extensionColumnSettingsMenu");
    if (menu) menu.style.display = "none";
    menuOpen = false;
  }

  function openMenu() {
    const menu = ensureMenu();
    menuOpen = true;
    renderMenu();
    menu.style.display = "block";
    positionMenu();
  }

  function renderMenu() {
    const menu = ensureMenu();
    const prefs = loadPrefs();
    const labelByKey = new Map(COLUMNS.map(item => [item.key, item.label]));
    const rows = prefs.order.map((key, index) => {
      const label = labelByKey.get(key) || key;
      const checked = prefs.visible[key] !== false ? "checked" : "";
      return `<div data-column-row="${key}" style="display:flex;align-items:center;gap:8px;padding:7px 4px;border-bottom:1px solid rgba(148,163,184,.12)">
        <label style="display:flex;align-items:center;gap:8px;flex:1;min-width:0;cursor:pointer;font-size:13px">
          <input type="checkbox" data-column-visible="${key}" ${checked}>
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${label}</span>
        </label>
        <button type="button" class="action" data-column-move="${key}" data-delta="-1" title="前移" ${index === 0 ? "disabled" : ""}>↑</button>
        <button type="button" class="action" data-column-move="${key}" data-delta="1" title="后移" ${index === prefs.order.length - 1 ? "disabled" : ""}>↓</button>
      </div>`;
    }).join("");
    menu.innerHTML = `<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:6px">
        <div><b>列表列设置</b><div class="muted" style="font-size:12px;margin-top:2px">勾选显示；↑ / ↓ 调整前后顺序</div></div>
        <button type="button" class="action" id="resetExtensionColumns">恢复默认</button>
      </div>${rows}`;

    for (const input of menu.querySelectorAll("[data-column-visible]")) {
      input.addEventListener("change", event =>
        setColumnVisible(event.currentTarget.dataset.columnVisible, event.currentTarget.checked),
      );
    }
    for (const button of menu.querySelectorAll("[data-column-move]")) {
      button.addEventListener("click", event =>
        moveColumn(
          event.currentTarget.dataset.columnMove,
          Number(event.currentTarget.dataset.delta || 0),
        ),
      );
    }
    menu.querySelector("#resetExtensionColumns")?.addEventListener("click", resetLayout);
    if (menuOpen) positionMenu();
  }

  function ensureSettingsButton() {
    if (document.getElementById("extensionColumnSettingsButton")) return true;
    const body = document.getElementById("extensionDeviceBody");
    const panel = body?.closest(".panel");
    if (!panel) return false;
    const heading = [...panel.querySelectorAll("h3")]
      .find(node => String(node.textContent || "").trim().startsWith("扩展列表"))
      || [...panel.querySelectorAll("h3")]
        .find(node => String(node.textContent || "").trim().startsWith("绑定设备"))
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
  if (baseLoadExtensions && !baseLoadExtensions.__chat2apiColumnLayoutV219) {
    const wrappedLoadExtensions = async (...args) => {
      const result = await baseLoadExtensions(...args);
      scheduleRefresh();
      return result;
    };
    wrappedLoadExtensions.__chat2apiColumnLayoutV219 = true;
    globalThis.loadExtensions = wrappedLoadExtensions;
  }

  const baseShow = typeof globalThis.show === "function" ? globalThis.show : null;
  if (baseShow && !baseShow.__chat2apiColumnLayoutV219) {
    const wrappedShow = async (...args) => {
      const result = await baseShow(...args);
      if (args[0] === "extensions") scheduleRefresh();
      return result;
    };
    wrappedShow.__chat2apiColumnLayoutV219 = true;
    globalThis.show = wrappedShow;
  }

  document.addEventListener("click", closeMenu);
  document.addEventListener("keydown", event => { if (event.key === "Escape") closeMenu(); });
  window.addEventListener("resize", positionMenu);
  window.addEventListener("scroll", positionMenu, true);

  document.documentElement.dataset.chat2apiExtensionColumnLayoutVersion = VERSION;
  refreshUi();
  scheduleRefresh();
})();