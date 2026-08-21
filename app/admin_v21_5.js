(() => {
  const VERSION = "0.21.5";
  const POLL_MS = 1000;
  const MIN_LIMIT = 1;
  const MAX_LIMIT = 32;
  let pollInFlight = false;

  function extensionViewActive() {
    return document.getElementById("view-extensions")?.classList.contains("active");
  }

  function columnCell(tr, key, fallbackIndex) {
    return tr?.querySelector(`td[data-chat2api-column-key="${key}"]`) || tr?.cells?.[fallbackIndex] || null;
  }

  function patchHeader() {
    const table = document.querySelector("#view-extensions #extensionDeviceBody")?.closest("table");
    const headers = table ? [...table.querySelectorAll("thead th")] : [];
    const target = headers.find(node => [
      "绑定 API Key 数",
      "API 调用数（实时并发）",
      "API 调用 / 并发上限",
    ].includes(node.textContent.trim()));
    if (target) {
      target.textContent = "API 调用 / 并发上限";
      target.title = "当前正在该扩展上执行的 API 请求数，并可按 Extension ID 单独设置最大并发数";
      target.dataset.chat2apiColumnKey = "concurrency";
    }
  }

  function renderConcurrencyCell(cell, item, active, limit) {
    const clientId = String(item.client_id || "");
    const source = String(item.concurrency_limit_source || item.capacity?.limit_source || "default");
    const currentEditor = cell.querySelector("[data-extension-concurrency-editor]");
    if (!currentEditor || currentEditor.dataset.clientId !== clientId) {
      cell.innerHTML = `
        <div data-extension-concurrency-editor data-client-id="${clientId}" style="display:flex;align-items:center;gap:6px;white-space:nowrap">
          <span data-concurrency-active>${active}</span>
          <span class="muted">/</span>
          <input data-concurrency-limit type="number" min="${MIN_LIMIT}" max="${MAX_LIMIT}" step="1" value="${limit}" style="width:64px;padding:5px 6px">
          <button class="action" data-concurrency-save style="padding:5px 8px">保存</button>
        </div>`;
    } else {
      const activeNode = currentEditor.querySelector("[data-concurrency-active]");
      if (activeNode) activeNode.textContent = String(active);
      const input = currentEditor.querySelector("[data-concurrency-limit]");
      if (input && document.activeElement !== input && input.value !== String(limit)) input.value = String(limit);
    }
    cell.title = source === "extension"
      ? `Extension ID ${clientId} 的独立并发上限；当前活动请求 ${active}`
      : `当前继承默认并发上限 ${limit}；点击保存后为 Extension ID ${clientId} 建立独立配置`;
  }

  async function refreshLiveConcurrency() {
    if (!extensionViewActive() || pollInFlight || typeof globalThis.api !== "function") return;
    pollInFlight = true;
    try {
      patchHeader();
      const data = await api("/api/admin/extensions");
      const byClient = new Map((data.clients || []).map(row => [String(row.client_id || ""), row]));
      const rows = document.querySelectorAll("#extensionDeviceBody tr");
      for (const tr of rows) {
        if (!tr.cells || tr.cells.length < 6) continue;
        const clientId = columnCell(tr, "client_id", 0)?.textContent?.trim() || "";
        const item = byClient.get(clientId);
        if (!item) continue;
        const active = Number.isFinite(Number(item.active_api_calls))
          ? Number(item.active_api_calls)
          : Number(item.capacity?.active_requests || 0);
        const limit = Number(item.max_concurrency || item.capacity?.limit_units || item.default_max_concurrency || 0);
        // v0.20 added account_type at base index 3, so the historical
        // bound-api-key/live-concurrency cell is base index 5, not 4.
        const concurrencyCell = columnCell(tr, "concurrency", 5);
        if (!concurrencyCell) continue;
        renderConcurrencyCell(concurrencyCell, item, active, limit);
      }
    } catch (_) {
      // The historical extension-management loader owns visible transport/auth errors.
      // Poll failures stay silent so a transient refresh cannot overwrite it.
    } finally {
      pollInFlight = false;
    }
  }

  async function saveExtensionConcurrency(button) {
    const editor = button?.closest?.("[data-extension-concurrency-editor]");
    const clientId = String(editor?.dataset?.clientId || "");
    const input = editor?.querySelector?.("[data-concurrency-limit]");
    const value = Number(input?.value || 0);
    if (!clientId || !Number.isInteger(value) || value < MIN_LIMIT || value > MAX_LIMIT) {
      if (typeof globalThis.status === "function") globalThis.status(`并发上限请输入 ${MIN_LIMIT}-${MAX_LIMIT} 的整数`, "bad");
      return;
    }
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "保存中";
    try {
      await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/concurrency`, {
        method: "PUT",
        body: { max_concurrency: value },
      });
      if (typeof globalThis.status === "function") globalThis.status(`${clientId} 并发上限已设为 ${value}`, "ok");
      await refreshLiveConcurrency();
    } catch (error) {
      if (typeof globalThis.status === "function") globalThis.status(`并发设置失败：${String(error?.message || error)}`, "bad");
    } finally {
      button.disabled = false;
      button.textContent = oldText || "保存";
    }
  }

  document.addEventListener("click", event => {
    const button = event.target?.closest?.("[data-concurrency-save]");
    if (!button) return;
    event.preventDefault();
    saveExtensionConcurrency(button).catch(() => {});
  });

  const baseShow = typeof globalThis.show === "function" ? globalThis.show : null;
  if (baseShow && !baseShow.__chat2apiPerExtensionConcurrencyV215) {
    const wrappedShow = async (...args) => {
      const result = await baseShow(...args);
      if (args[0] === "extensions") await refreshLiveConcurrency();
      return result;
    };
    wrappedShow.__chat2apiPerExtensionConcurrencyV215 = true;
    globalThis.show = wrappedShow;
  }

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;
  patchHeader();
  refreshLiveConcurrency();
  setInterval(() => {
    if (extensionViewActive()) refreshLiveConcurrency();
  }, POLL_MS);
})();
