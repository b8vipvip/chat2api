(() => {
  const KEY = "__CHAT2API_WORKER_RUNTIME_V61__";
  if (globalThis[KEY]) return;

  const COLUMN_KEY = "occupied_windows";
  const POLL_MS = 1000;
  const state = {
    version: 61,
    timer: null,
    inFlight: false,
    polls: 0,
    valueUpdates: 0,
    structuralRepairs: 0,
  };
  globalThis[KEY] = state;

  function extensionViewActive() {
    return document.getElementById("view-extensions")?.classList.contains("active");
  }

  function tableParts() {
    const body = document.getElementById("extensionDeviceBody");
    const table = body?.closest("table") || null;
    const header = table?.querySelector("thead tr") || null;
    return { body, table, header };
  }

  function clientIdForRow(tr) {
    return tr?.querySelector?.('td[data-chat2api-column-key="client_id"]')?.textContent?.trim()
      || tr?.cells?.[0]?.textContent?.trim()
      || "";
  }

  function insertAfter(reference, node) {
    if (!reference?.parentNode) return;
    reference.parentNode.insertBefore(node, reference.nextSibling);
  }

  function ensureColumn() {
    const { body, header } = tableParts();
    if (!body || !header) return false;

    let th = header.querySelector(`th[data-chat2api-column-key="${COLUMN_KEY}"]`);
    const anchor = header.querySelector('th[data-chat2api-column-key="worker_settings"]');
    if (!th) {
      th = document.createElement("th");
      th.dataset.chat2apiColumnKey = COLUMN_KEY;
      th.dataset.chat2apiWorkerOccupiedV61 = "1";
      th.textContent = "当前占用";
      th.title = "当前正在处理 API 请求、实际占用的 Worker 窗口数量。此数字独立刷新，不重绘 Worker 列表。";
      if (anchor) insertAfter(anchor, th);
      else header.appendChild(th);
      state.structuralRepairs += 1;
    } else if (anchor && th.previousElementSibling !== anchor) {
      insertAfter(anchor, th);
    }

    for (const tr of body.rows) {
      if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) {
        tr.cells[0].colSpan = Math.max(1, header.children.length);
        continue;
      }
      let cell = tr.querySelector(`td[data-chat2api-column-key="${COLUMN_KEY}"]`);
      const rowAnchor = tr.querySelector('td[data-chat2api-column-key="worker_settings"]');
      if (!cell) {
        cell = document.createElement("td");
        cell.dataset.chat2apiColumnKey = COLUMN_KEY;
        cell.dataset.chat2apiWorkerOccupiedV61 = "1";
        cell.dataset.workerOccupied = "1";
        cell.textContent = "0";
        cell.title = "当前占用窗口数";
        if (rowAnchor) insertAfter(rowAnchor, cell);
        else tr.appendChild(cell);
        state.structuralRepairs += 1;
      } else if (rowAnchor && cell.previousElementSibling !== rowAnchor) {
        insertAfter(rowAnchor, cell);
      }
    }
    return true;
  }

  function setOccupied(cell, value) {
    const next = String(Math.max(0, Number(value) || 0));
    if (cell.textContent === next) return;
    cell.textContent = next;
    cell.classList.toggle("warnText", Number(next) > 0);
    state.valueUpdates += 1;
  }

  async function refreshOccupied() {
    if (state.inFlight || document.hidden || !extensionViewActive() || typeof globalThis.api !== "function") return;
    state.inFlight = true;
    state.polls += 1;
    try {
      ensureColumn();
      const payload = await api("/api/admin/capacity-v57");
      const workers = payload?.workers && typeof payload.workers === "object" ? payload.workers : {};
      const { body } = tableParts();
      if (!body) return;
      for (const tr of body.rows) {
        if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) continue;
        const clientId = clientIdForRow(tr);
        if (!clientId) continue;
        const cell = tr.querySelector(`td[data-chat2api-column-key="${COLUMN_KEY}"]`);
        if (!cell) continue;
        setOccupied(cell, workers?.[clientId]?.active ?? 0);
      }
    } catch (_) {
    } finally {
      state.inFlight = false;
    }
  }

  function schedule(delay = POLL_MS) {
    clearTimeout(state.timer);
    state.timer = setTimeout(async () => {
      state.timer = null;
      await refreshOccupied();
      schedule(POLL_MS);
    }, Math.max(0, delay));
  }

  function observeStructure() {
    const { body, header } = tableParts();
    if (typeof MutationObserver !== "function") return;
    const repair = () => {
      ensureColumn();
      refreshOccupied().catch(() => {});
    };
    if (body) new MutationObserver(repair).observe(body, { childList: true });
    if (header) new MutationObserver(repair).observe(header, { childList: true });
  }

  function boot() {
    ensureColumn();
    observeStructure();
    refreshOccupied().catch(() => {});
    schedule(POLL_MS);
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshOccupied().catch(() => {});
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();