(() => {
  const KEY = "__CHAT2API_WORKER_PRESENTATION_V64__";
  if (globalThis[KEY]) return;

  const VERSION = 64;
  const STORAGE_KEY = "chat2api.workerExtraColumns.v64";
  const EXTRAS = [
    { key: "device_name", label: "设备名称", defaultAfter: "device_id" },
    { key: "occupancy", label: "当前占用", defaultAfter: "worker_settings" },
  ];
  const EXTRA_KEYS = new Set(EXTRAS.map(item => item.key));
  let refreshTask = null;
  let lastSnapshot = null;
  let applyQueued = false;

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
      visible: Object.fromEntries(EXTRAS.map(item => [item.key, true])),
      after: Object.fromEntries(EXTRAS.map(item => [item.key, item.defaultAfter])),
    };
  }

  function loadPrefs() {
    const base = defaultPrefs();
    try {
      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (raw?.visible && typeof raw.visible === "object") {
        for (const item of EXTRAS) if (typeof raw.visible[item.key] === "boolean") base.visible[item.key] = raw.visible[item.key];
      }
      if (raw?.after && typeof raw.after === "object") {
        for (const item of EXTRAS) if (typeof raw.after[item.key] === "string") base.after[item.key] = raw.after[item.key];
      }
    } catch (_) {}
    return base;
  }

  function savePrefs(prefs) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs)); } catch (_) {}
  }

  function parts() {
    const body = document.getElementById("extensionDeviceBody");
    const table = body?.closest("table") || null;
    const header = table?.querySelector("thead tr") || null;
    return { body, table, header };
  }

  function cellKey(node) {
    return String(node?.dataset?.chat2apiColumnKey || "");
  }

  function baseOrder() {
    const { header } = parts();
    return [...(header?.children || [])].map(cellKey).filter(key => key && !EXTRA_KEYS.has(key));
  }

  function combinedOrder(prefs = loadPrefs()) {
    const order = baseOrder();
    const pending = EXTRAS.map(item => item.key);
    for (let pass = 0; pass < EXTRAS.length + 2 && pending.length; pass += 1) {
      for (let index = pending.length - 1; index >= 0; index -= 1) {
        const key = pending[index];
        const after = String(prefs.after[key] || "");
        if (after === "__start__") {
          order.unshift(key);
          pending.splice(index, 1);
          continue;
        }
        const anchorIndex = order.indexOf(after);
        if (anchorIndex >= 0) {
          order.splice(anchorIndex + 1, 0, key);
          pending.splice(index, 1);
        }
      }
    }
    for (const key of pending.reverse()) order.push(key);
    return order;
  }

  function placeExtraNodes(parent, order) {
    if (!parent) return;
    for (const key of EXTRAS.map(item => item.key)) {
      const node = [...parent.children].find(child => cellKey(child) === key);
      if (!node) continue;
      const index = order.indexOf(key);
      const nextKey = order.slice(index + 1).find(candidate => [...parent.children].some(child => cellKey(child) === candidate));
      const next = nextKey ? [...parent.children].find(child => cellKey(child) === nextKey) : null;
      if (next) parent.insertBefore(node, next);
      else parent.appendChild(node);
    }
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

  function ensureHeader() {
    const { header } = parts();
    if (!header) return;
    for (const item of EXTRAS) {
      if ([...header.children].some(node => cellKey(node) === item.key)) continue;
      const th = document.createElement("th");
      th.dataset.chat2apiColumnKey = item.key;
      th.dataset.chat2apiPresentationV64 = "1";
      th.textContent = item.label;
      header.appendChild(th);
    }
  }

  function ensureRowCells(rows) {
    const { body } = parts();
    if (!body) return;
    const byClient = new Map((rows || []).map(row => [String(row?.client_id || ""), row]));
    for (const tr of body.rows) {
      if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) continue;
      const clientId = String(tr.dataset?.clientId || tr.querySelector("[data-chat2api-column-key='client_id'] code")?.textContent || "").trim();
      const row = byClient.get(clientId) || null;
      for (const item of EXTRAS) {
        let td = [...tr.children].find(node => cellKey(node) === item.key);
        if (!td) {
          td = document.createElement("td");
          td.dataset.chat2apiColumnKey = item.key;
          td.dataset.chat2apiPresentationV64 = "1";
          tr.appendChild(td);
        }
        if (item.key === "device_name") {
          const name = String(row?.device_name || "").trim();
          td.innerHTML = name ? `<span title="${esc(row?.device_code_id || row?.pairing_id || "")}">${esc(name)}</span>` : '<span class="muted">-</span>';
        } else if (item.key === "occupancy") {
          const value = occupancy(row);
          td.className = value.cls;
          td.textContent = value.text;
          td.title = value.title;
        }
      }
    }
  }

  function applyVisibilityAndOrder() {
    const prefs = loadPrefs();
    const order = combinedOrder(prefs);
    const { body, header } = parts();
    if (!header || !body) return;
    placeExtraNodes(header, order);
    for (const item of EXTRAS) {
      const th = [...header.children].find(node => cellKey(node) === item.key);
      if (th) th.style.display = prefs.visible[item.key] === false ? "none" : "";
    }
    for (const tr of body.rows) {
      if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) {
        const shown = [...header.children].filter(node => node.style.display !== "none").length;
        tr.cells[0].colSpan = Math.max(1, shown);
        continue;
      }
      placeExtraNodes(tr, order);
      for (const item of EXTRAS) {
        const td = [...tr.children].find(node => cellKey(node) === item.key);
        if (td) td.style.display = prefs.visible[item.key] === false ? "none" : "";
      }
    }
  }

  function pairingRows(rows) {
    const body = document.getElementById("pairingBody");
    if (!body) return;
    const list = Array.isArray(rows) ? rows : [];
    [...body.rows].forEach((tr, index) => {
      const row = list[index];
      if (!row) return;
      const id = String(row.pairing_id || "");
      tr.dataset.v64PairingId = id;
      tr.dataset.v64PairingName = String(row.name || "");
      const actions = tr.querySelector(".rowactions");
      if (!actions || actions.querySelector("[data-v64-pairing-rename]")) return;
      const button = document.createElement("button");
      button.className = "action";
      button.dataset.v64PairingRename = "1";
      button.dataset.pairingId = id;
      button.dataset.pairingName = String(row.name || "");
      button.textContent = "改名";
      actions.insertBefore(button, actions.firstChild || null);
    });
  }

  function cardForBaseKey(grid, key) {
    const input = grid?.querySelector(`[data-column-visible="${CSS.escape(key)}"]`);
    return input?.closest("label")?.parentElement?.parentElement || null;
  }

  function extraCard(item, prefs) {
    const card = document.createElement("div");
    card.dataset.v64ExtraCard = item.key;
    card.style.cssText = "display:flex;flex-direction:column;gap:10px;min-width:0;padding:12px;border:1px solid rgba(148,163,184,.16);border-radius:11px;background:rgba(15,23,42,.72)";
    card.innerHTML = `<div style="display:flex;align-items:center;gap:9px"><span data-v64-order-number style="min-width:30px;padding:3px 6px;text-align:center;border-radius:999px;background:rgba(59,130,246,.16);color:#93c5fd;font-size:11px">00</span><label style="display:flex;align-items:center;gap:8px;flex:1;font-weight:600"><input type="checkbox" data-v64-column-visible="${item.key}" ${prefs.visible[item.key] !== false ? "checked" : ""}><span>${item.label}</span></label></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px"><button class="action" data-v64-column-move="${item.key}" data-delta="-1">← 前移</button><button class="action" data-v64-column-move="${item.key}" data-delta="1">后移 →</button></div>`;
    return card;
  }

  function patchSettingsMenu() {
    const backdrop = document.getElementById("extensionColumnSettingsBackdrop");
    const menu = document.getElementById("extensionColumnSettingsMenu");
    if (!menu || !backdrop || backdrop.style.display === "none") return;
    const grid = [...menu.querySelectorAll("div")].find(node => String(node.style?.gridTemplateColumns || "").includes("minmax(250px"));
    if (!grid) return;
    const prefs = loadPrefs();
    for (const item of EXTRAS) {
      let card = grid.querySelector(`[data-v64-extra-card="${item.key}"]`);
      if (!card) {
        card = extraCard(item, prefs);
        grid.appendChild(card);
      }
      const checkbox = card.querySelector("[data-v64-column-visible]");
      if (checkbox) checkbox.checked = prefs.visible[item.key] !== false;
    }

    const order = combinedOrder(prefs);
    const cards = new Map();
    for (const key of order) {
      const card = EXTRA_KEYS.has(key)
        ? grid.querySelector(`[data-v64-extra-card="${key}"]`)
        : cardForBaseKey(grid, key);
      if (card) cards.set(key, card);
    }
    for (const key of order) {
      const card = cards.get(key);
      if (card) grid.appendChild(card);
    }
    [...grid.children].forEach((card, index) => {
      const badge = card.querySelector("[data-v64-order-number]") || card.firstElementChild?.querySelector("span");
      if (badge) badge.textContent = String(index + 1).padStart(2, "0");
    });

    for (const item of EXTRAS) {
      const card = grid.querySelector(`[data-v64-extra-card="${item.key}"]`);
      const index = order.indexOf(item.key);
      const left = card?.querySelector('[data-delta="-1"]');
      const right = card?.querySelector('[data-delta="1"]');
      if (left) left.disabled = index <= 0;
      if (right) right.disabled = index < 0 || index >= order.length - 1;
    }

    const summary = [...menu.querySelectorAll(".muted")].find(node => /^已显示\s+\d+\s*\/\s*\d+\s*列/.test(String(node.textContent || "").trim()));
    if (summary) {
      const baseVisible = [...grid.querySelectorAll("input[data-column-visible]")].filter(input => input.checked).length;
      const extraVisible = EXTRAS.filter(item => prefs.visible[item.key] !== false).length;
      summary.textContent = `已显示 ${baseVisible + extraVisible} / ${grid.children.length} 列`;
    }
  }

  function moveExtra(key, delta) {
    const prefs = loadPrefs();
    const order = combinedOrder(prefs);
    const index = order.indexOf(key);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= order.length) return;
    const without = order.filter(item => item !== key);
    const insertionIndex = Math.max(0, Math.min(without.length, target));
    prefs.after[key] = insertionIndex === 0 ? "__start__" : without[insertionIndex - 1];
    savePrefs(prefs);
    applyVisibilityAndOrder();
    patchSettingsMenu();
  }

  async function callApi(path, options) {
    if (typeof globalThis.api === "function") return globalThis.api(path, options);
    if (typeof api === "function") return api(path, options);
    throw new Error("管理控制台 API helper 不可用");
  }

  async function refresh(force = false) {
    if (refreshTask && !force) return refreshTask;
    const task = (async () => {
      const view = document.getElementById("view-extensions");
      if (!force && view && !view.classList.contains("active")) return null;
      const data = await callApi("/api/admin/extensions");
      lastSnapshot = data;
      ensureHeader();
      ensureRowCells(Array.isArray(data?.clients) ? data.clients : []);
      applyVisibilityAndOrder();
      pairingRows(Array.isArray(data?.pairing_codes) ? data.pairing_codes : []);
      patchSettingsMenu();
      document.documentElement.dataset.chat2apiWorkerPresentationRevision = String(VERSION);
      return data;
    })().catch(error => {
      console.warn("chat2api worker presentation v64 refresh failed", error);
      return null;
    });
    refreshTask = task;
    try { return await task; } finally { if (refreshTask === task) refreshTask = null; }
  }

  function queueApply() {
    if (applyQueued) return;
    applyQueued = true;
    queueMicrotask(() => {
      applyQueued = false;
      if (lastSnapshot) {
        ensureHeader();
        ensureRowCells(Array.isArray(lastSnapshot.clients) ? lastSnapshot.clients : []);
        applyVisibilityAndOrder();
        pairingRows(Array.isArray(lastSnapshot.pairing_codes) ? lastSnapshot.pairing_codes : []);
      }
      patchSettingsMenu();
    });
  }

  document.addEventListener("change", event => {
    const input = event.target?.closest?.("[data-v64-column-visible]");
    if (!input) return;
    const key = String(input.dataset.v64ColumnVisible || "");
    if (!EXTRA_KEYS.has(key)) return;
    const prefs = loadPrefs();
    prefs.visible[key] = Boolean(input.checked);
    savePrefs(prefs);
    applyVisibilityAndOrder();
    patchSettingsMenu();
  }, true);

  document.addEventListener("click", async event => {
    const move = event.target?.closest?.("[data-v64-column-move]");
    if (move) {
      event.preventDefault();
      event.stopPropagation();
      moveExtra(String(move.dataset.v64ColumnMove || ""), Number(move.dataset.delta || 0));
      return;
    }
    const rename = event.target?.closest?.("[data-v64-pairing-rename]");
    if (rename) {
      event.preventDefault();
      event.stopPropagation();
      const pairingId = String(rename.dataset.pairingId || "");
      const current = String(rename.dataset.pairingName || "");
      const next = prompt("设备名称", current);
      if (next == null) return;
      const clean = next.trim();
      if (!clean) {
        if (typeof globalThis.status === "function") globalThis.status("设备名称不能为空", "bad");
        return;
      }
      try {
        await callApi(`/api/admin/pairing-codes/${encodeURIComponent(pairingId)}/name`, { method: "PATCH", body: { name: clean } });
        if (typeof globalThis.status === "function") globalThis.status("设备名称已更新", "ok");
        await refresh(true);
        const row = document.querySelector(`#pairingBody tr[data-v64-pairing-id="${CSS.escape(pairingId)}"]`);
        if (row?.cells?.[0]) row.cells[0].textContent = clean;
      } catch (error) {
        if (typeof globalThis.status === "function") globalThis.status("设备名称修改失败：" + String(error?.message || error), "bad");
      }
      return;
    }
    if (event.target?.closest?.("#extensionColumnSettingsButton")) setTimeout(patchSettingsMenu, 0);
  }, true);

  const start = () => {
    const { body, header } = parts();
    if (typeof MutationObserver === "function") {
      if (body) new MutationObserver(queueApply).observe(body, { childList: true });
      if (header) new MutationObserver(queueApply).observe(header, { childList: true });
      const pairingBody = document.getElementById("pairingBody");
      if (pairingBody) new MutationObserver(queueApply).observe(pairingBody, { childList: true });
    }
    refresh(true);
    setInterval(() => refresh(false), 1500);
    setInterval(patchSettingsMenu, 250);
  };

  globalThis[KEY] = Object.freeze({ version: VERSION, refresh, applyVisibilityAndOrder, patchSettingsMenu });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
