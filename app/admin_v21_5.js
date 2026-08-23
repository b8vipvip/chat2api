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

  function ensureToastHost() {
    let host = document.getElementById("chat2apiActionToastHost");
    if (host) return host;
    host = document.createElement("div");
    host.id = "chat2apiActionToastHost";
    host.style.cssText = [
      "position:fixed",
      "top:18px",
      "right:18px",
      "z-index:10000",
      "display:flex",
      "flex-direction:column",
      "gap:8px",
      "max-width:min(460px,calc(100vw - 36px))",
      "pointer-events:none",
    ].join(";");
    document.body.appendChild(host);
    return host;
  }

  function showActionResult(message, level = "ok", button = null) {
    const text = String(message || "").trim();
    if (!text) return;
    if (button) button.title = text;
    if (typeof globalThis.status === "function") {
      globalThis.status(text, level === "bad" ? "bad" : level === "warn" ? "warnText" : "ok");
    }
    const host = ensureToastHost();
    const toast = document.createElement("div");
    toast.setAttribute("role", "status");
    toast.dataset.level = level;
    toast.textContent = text;
    toast.style.cssText = [
      "pointer-events:auto",
      "padding:10px 12px",
      "border:1px solid rgba(148,163,184,.32)",
      "border-radius:10px",
      "background:#0f172a",
      "box-shadow:0 14px 36px rgba(0,0,0,.4)",
      "font-size:13px",
      "line-height:1.45",
      "white-space:normal",
    ].join(";");
    if (level === "bad") toast.classList.add("bad");
    else if (level === "warn") toast.classList.add("warnText");
    else toast.classList.add("ok");
    host.appendChild(toast);
    setTimeout(() => toast.remove(), 4200);
  }
  if (typeof globalThis.chat2apiActionToast !== "function") globalThis.chat2apiActionToast = showActionResult;

  function patchColumnSettingsLabels() {
    const menu = document.getElementById("extensionColumnSettingsMenu");
    if (!menu) return;
    const replacements = new Map([
      ["API 调用数（实时并发）", "并发设置"],
      ["API 调用 / 并发上限", "并发设置"],
      ["备用窗口", "实时窗口"],
    ]);
    for (const node of menu.querySelectorAll("span,button,label")) {
      const current = String(node.textContent || "").trim();
      if (replacements.has(current)) node.textContent = replacements.get(current);
      const title = String(node.getAttribute?.("title") || "").trim();
      if (replacements.has(title)) node.setAttribute("title", replacements.get(title));
    }
  }

  function patchHeader() {
    const table = document.querySelector("#view-extensions #extensionDeviceBody")?.closest("table");
    const headers = table ? [...table.querySelectorAll("thead th")] : [];
    const target = headers.find(node => [
      "绑定 API Key 数",
      "API 调用数（实时并发）",
      "API 调用 / 并发上限",
      "并发设置",
    ].includes(node.textContent.trim()));
    if (target) {
      target.textContent = "并发设置";
      target.title = "当前 API 调用数 / 此 Extension ID 的最大并发；保存后立即触发设备扩容或减容";
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
      : `当前继承默认并发上限 ${limit}；点击保存后为 Extension ID ${clientId} 建立独立配置并立即调整设备窗口`;
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
        const concurrencyCell = columnCell(tr, "concurrency", 5);
        if (!concurrencyCell) continue;
        renderConcurrencyCell(concurrencyCell, item, active, limit);
      }
    } catch (_) {
      // The historical extension-management loader owns visible transport/auth errors.
    } finally {
      pollInFlight = false;
    }
  }

  function snapshotText(snapshot, target) {
    if (!snapshot || typeof snapshot !== "object" || snapshot.total === undefined) return `目标 ${target}`;
    const total = Math.max(0, Number(snapshot.total || 0));
    const active = Math.max(0, Number(snapshot.active || 0));
    return `实时窗口 ${total}(${active})，目标 ${target}`;
  }

  async function saveExtensionConcurrency(button) {
    const editor = button?.closest?.("[data-extension-concurrency-editor]");
    const clientId = String(editor?.dataset?.clientId || "");
    const input = editor?.querySelector?.("[data-concurrency-limit]");
    const value = Number(input?.value || 0);
    if (!clientId || !Number.isInteger(value) || value < MIN_LIMIT || value > MAX_LIMIT) {
      showActionResult(`并发上限请输入 ${MIN_LIMIT}-${MAX_LIMIT} 的整数`, "bad", button);
      return;
    }

    button.disabled = true;
    const oldText = button.textContent;
    let persisted = false;
    try {
      button.textContent = "保存中";
      const saved = await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/concurrency`, {
        method: "PUT",
        body: { max_concurrency: value },
      });
      persisted = true;
      const appliedLimit = Number(saved?.max_concurrency || value);
      if (input && input.value !== String(appliedLimit)) input.value = String(appliedLimit);

      button.textContent = "执行中";
      const applied = await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/capacity/apply`, {
        method: "POST",
        body: { target: appliedLimit },
      });
      const snapshot = applied?.window_snapshot || {};
      if (applied?.ok === true && applied?.target_reached === true) {
        showActionResult(`保存成功：并发 ${appliedLimit}；${snapshotText(snapshot, appliedLimit)}`, "ok", button);
      } else if (applied?.ok === true) {
        const reason = String(applied?.pending_reason || "设备仍在收敛");
        showActionResult(`配置已保存，设备已执行：${snapshotText(snapshot, appliedLimit)}；${reason}`, "warn", button);
      } else {
        const reason = String(applied?.error || "设备没有返回执行确认");
        showActionResult(`配置已保存，但设备未确认执行：${reason}`, "warn", button);
      }
      await refreshLiveConcurrency();
    } catch (error) {
      const detail = String(error?.message || error);
      showActionResult(persisted ? `配置已保存，但设备执行失败：${detail}` : `并发设置保存失败：${detail}`, "bad", button);
    } finally {
      button.disabled = false;
      button.textContent = oldText || "保存";
    }
  }

  document.addEventListener("click", event => {
    const button = event.target?.closest?.("[data-concurrency-save]");
    if (button) {
      event.preventDefault();
      saveExtensionConcurrency(button).catch(() => {});
    }
    if (event.target?.closest?.("#extensionColumnSettingsButton, #extensionColumnSettingsMenu")) {
      setTimeout(patchColumnSettingsLabels, 0);
    }
  });
  document.addEventListener("change", event => {
    if (event.target?.closest?.("#extensionColumnSettingsMenu")) setTimeout(patchColumnSettingsLabels, 0);
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
  patchColumnSettingsLabels();
  refreshLiveConcurrency();
  setInterval(() => {
    if (extensionViewActive()) refreshLiveConcurrency();
  }, POLL_MS);
})();
