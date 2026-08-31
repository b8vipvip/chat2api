(() => {
  const VERSION = "0.22.40-capacity-v58";
  const MIN_LIMIT = 1;
  const MAX_LIMIT = 32;
  let workerRefreshInFlight = false;
  let keyRenderInFlight = false;
  let workerRefreshScheduled = false;
  let keyRefreshScheduled = false;
  const windowRefreshPending = new Set();

  function extensionViewActive() {
    return document.getElementById("view-extensions")?.classList.contains("active");
  }
  function keyViewActive() {
    return document.getElementById("view-keys")?.classList.contains("active");
  }
  function columnCell(tr, key, fallbackIndex) {
    return tr?.querySelector(`td[data-chat2api-column-key="${key}"]`) || tr?.cells?.[fallbackIndex] || null;
  }
  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  }

  function ensureToastHost() {
    let host = document.getElementById("chat2apiActionToastHost");
    if (host) return host;
    host = document.createElement("div");
    host.id = "chat2apiActionToastHost";
    host.style.cssText = "position:fixed;top:18px;right:18px;z-index:10000;display:flex;flex-direction:column;gap:8px;max-width:min(520px,calc(100vw - 36px));pointer-events:none";
    document.body.appendChild(host);
    return host;
  }

  function showActionResult(message, level = "ok", button = null) {
    const text = String(message || "").trim();
    if (!text) return;
    if (button) button.title = text;
    if (typeof globalThis.status === "function") globalThis.status(text, level === "bad" ? "bad" : level === "warn" ? "warnText" : "ok");
    const toast = document.createElement("div");
    toast.textContent = text;
    toast.style.cssText = "pointer-events:auto;padding:10px 12px;border:1px solid rgba(148,163,184,.32);border-radius:10px;background:#0f172a;box-shadow:0 14px 36px rgba(0,0,0,.4);font-size:13px;line-height:1.45;white-space:normal";
    if (level === "bad") toast.classList.add("bad");
    else if (level === "warn") toast.classList.add("warnText");
    else toast.classList.add("ok");
    ensureToastHost().appendChild(toast);
    setTimeout(() => toast.remove(), 4800);
  }
  globalThis.chat2apiActionToast = showActionResult;

  function patchColumnSettingsLabels() {
    const menu = document.getElementById("extensionColumnSettingsMenu");
    if (!menu) return;
    const replacements = new Map([
      ["平台", "Worker 窗口"],
      ["API 调用数（实时并发）", "旧并发列（已合并）"],
      ["API 调用 / 并发上限", "旧并发列（已合并）"],
      ["并发设置", "旧并发列（已合并）"],
      ["备用窗口", "旧备用窗口列（已合并）"],
    ]);
    for (const node of menu.querySelectorAll("span,button,label")) {
      const current = String(node.textContent || "").trim();
      if (replacements.has(current)) node.textContent = replacements.get(current);
    }
  }

  function patchExtensionColumns() {
    const body = document.getElementById("extensionDeviceBody");
    const table = body?.closest("table");
    const header = table?.querySelector("thead tr");
    if (!body || !header) return;
    const platformHeader = [...header.cells].find(th => String(th.dataset.chat2apiColumnKey || "") === "platform" || th.textContent.trim() === "平台" || th.textContent.trim() === "Worker 窗口");
    if (platformHeader) {
      platformHeader.textContent = "Worker 窗口";
      platformHeader.dataset.chat2apiColumnKey = "platform";
      platformHeader.dataset.chat2apiStructuralOwner = "worker-window-v58";
      platformHeader.title = "设置此 Worker 最大并发请求和最大空闲备用窗口；超过并发上限的请求进入 FIFO 队列。";
      platformHeader.style.display = "";
      platformHeader.style.minWidth = "350px";
    }
    for (const key of ["concurrency", "reserve_windows"]) {
      const th = [...header.cells].find(node => String(node.dataset.chat2apiColumnKey || "") === key);
      if (th) th.style.display = "none";
      for (const tr of body.rows) {
        const cell = tr.querySelector(`td[data-chat2api-column-key="${key}"]`);
        if (cell) cell.style.display = "none";
      }
    }
  }

  function platformText(item) {
    const metadata = item?.metadata || {};
    const os = String(metadata.platform_os || "").trim() || "unknown";
    const arch = String(metadata.platform_arch || "").trim();
    return arch ? `${os} · ${arch}` : os;
  }

  function workerEditorHtml(clientId) {
    return `<div data-worker-window-editor data-client-id="${esc(clientId)}" data-chat2api-structural-owner="worker-window-v58" style="min-width:340px;white-space:normal">
      <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap">
        <label class="muted">并发</label><input data-worker-max type="number" min="${MIN_LIMIT}" max="${MAX_LIMIT}" value="3" style="width:58px;padding:5px 6px">
        <label class="muted">备用</label><input data-worker-reserve type="number" min="${MIN_LIMIT}" max="${MAX_LIMIT}" value="3" style="width:58px;padding:5px 6px">
        <button class="action" data-worker-save style="padding:5px 8px">保存</button>
        <button class="action" data-worker-refresh style="padding:5px 8px">刷新</button>
      </div>
      <div class="muted" data-worker-live style="margin-top:5px;font-size:11px"></div>
      <div class="muted" data-worker-platform style="margin-top:2px;font-size:11px"></div>
    </div>`;
  }

  function updateWorkerEditor(cell, clientId, item, settings) {
    let editor = cell.querySelector("[data-worker-window-editor]");
    if (!editor || String(editor.dataset.clientId || "") !== clientId) {
      // Structural DOM is created once per row. Subsequent refreshes update only
      // values/text so health polling cannot cause visual teardown/rebuild flicker.
      cell.innerHTML = workerEditorHtml(clientId);
      editor = cell.querySelector("[data-worker-window-editor]");
    }
    if (!editor) return;
    cell.dataset.chat2apiStructuralOwner = "worker-window-v58";
    cell.style.display = "";
    cell.title = "并发 = 同时执行的最大请求数；备用 = 维持的最大空闲 Worker 窗口数。两者默认均为 3。";

    const metadata = item?.metadata || {};
    const maximum = Number(settings?.max_concurrency || item?.worker_window_settings?.max_concurrency || 3);
    const reserve = Number(settings?.reserve_windows || item?.worker_window_settings?.reserve_windows || 3);
    const active = Number(item?.active_api_calls ?? item?.capacity?.active_requests ?? settings?.active ?? 0);
    const total = Number(metadata.reserve_window_total || 0);
    const idle = Number(metadata.reserve_window_idle || 0);
    const queued = Number(settings?.queued ?? item?.capacity?.queued_requests ?? 0);
    const cooling = Boolean(settings?.rate_limit_cooldown ?? item?.capacity?.rate_limit_cooldown_active);
    const remaining = Number(settings?.rate_limit_remaining_seconds ?? item?.capacity?.rate_limit_cooldown_remaining_seconds ?? 0);

    const focused = editor.contains(document.activeElement);
    const maxInput = editor.querySelector("[data-worker-max]");
    const reserveInput = editor.querySelector("[data-worker-reserve]");
    if (!focused) {
      if (maxInput && Number(maxInput.value) !== maximum) maxInput.value = String(maximum);
      if (reserveInput && Number(reserveInput.value) !== reserve) reserveInput.value = String(reserve);
    }
    const live = editor.querySelector("[data-worker-live]");
    if (live) {
      const cooldown = cooling ? ` · ChatGPT 限流冷却 ${Math.ceil(remaining)}s` : "";
      const next = `当前 ${active}/${maximum} · 排队 ${queued} · 窗口 ${total} · 空闲 ${idle}/${reserve}${cooldown}`;
      if (live.textContent !== next) live.textContent = next;
      live.classList.toggle("bad", cooling);
    }
    const platform = editor.querySelector("[data-worker-platform]");
    const platformLabel = platformText(item);
    if (platform && platform.textContent !== platformLabel) platform.textContent = platformLabel;
  }

  async function refreshWorkerSettings(extensionRows = null) {
    if (!extensionViewActive() || workerRefreshInFlight || typeof globalThis.api !== "function") return;
    workerRefreshInFlight = true;
    try {
      patchExtensionColumns();
      patchColumnSettingsLabels();
      let rows = Array.isArray(extensionRows) ? extensionRows : null;
      let capacity;
      if (rows) {
        capacity = await api("/api/admin/capacity-v57");
      } else {
        const payloads = await Promise.all([api("/api/admin/extensions"), api("/api/admin/capacity-v57")]);
        rows = Array.isArray(payloads[0]?.clients) ? payloads[0].clients : [];
        capacity = payloads[1];
      }
      const byClient = new Map((rows || []).map(row => [String(row.client_id || ""), row]));
      const settingsByClient = capacity?.workers || {};
      for (const tr of document.querySelectorAll("#extensionDeviceBody tr")) {
        if (!tr.cells || tr.cells.length < 2) continue;
        const clientId = columnCell(tr, "client_id", 0)?.textContent?.trim() || "";
        const item = byClient.get(clientId);
        if (!item) continue;
        const cell = columnCell(tr, "platform", 8);
        if (!cell) continue;
        updateWorkerEditor(cell, clientId, item, settingsByClient[clientId] || item.worker_window_settings || {});
      }
    } catch (_) {
      // Base extension manager owns visible auth/transport errors.
    } finally {
      workerRefreshInFlight = false;
    }
  }

  function scheduleWorkerRefresh() {
    if (workerRefreshScheduled) return;
    workerRefreshScheduled = true;
    requestAnimationFrame(() => {
      workerRefreshScheduled = false;
      refreshWorkerSettings().catch(() => {});
    });
  }

  async function saveWorkerSettings(button) {
    const editor = button.closest("[data-worker-window-editor]");
    const clientId = String(editor?.dataset.clientId || "");
    const maximum = Number(editor?.querySelector("[data-worker-max]")?.value || 0);
    const reserve = Number(editor?.querySelector("[data-worker-reserve]")?.value || 0);
    if (!clientId || !Number.isInteger(maximum) || !Number.isInteger(reserve) || maximum < MIN_LIMIT || maximum > MAX_LIMIT || reserve < MIN_LIMIT || reserve > MAX_LIMIT) {
      showActionResult(`并发和备用窗口都请输入 ${MIN_LIMIT}-${MAX_LIMIT} 的整数`, "bad", button);
      return;
    }
    const refresh = editor.querySelector("[data-worker-refresh]");
    button.disabled = true;
    if (refresh) refresh.disabled = true;
    const old = button.textContent;
    try {
      button.textContent = "保存中";
      await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/capacity-v57`, {method:"PUT", body:{max_concurrency:maximum,reserve_windows:reserve}});
      button.textContent = "执行中";
      let apply = null;
      try {
        apply = await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/capacity/apply`, {method:"POST", body:{target:reserve}});
      } catch (_) {}
      if (apply?.ok === true) {
        showActionResult(`保存成功：最大并发 ${maximum}，空闲备用窗口 ${reserve}；超过并发上限的请求将排队。`, apply.target_reached === false ? "warn" : "ok", button);
      } else {
        showActionResult(`配置已保存：最大并发 ${maximum}，空闲备用窗口 ${reserve}；设备将在心跳/窗口同步时收敛。`, "warn", button);
      }
      await refreshWorkerSettings();
    } catch (error) {
      showActionResult(`Worker 窗口设置失败：${String(error?.message || error)}`, "bad", button);
    } finally {
      button.disabled = false;
      button.textContent = old || "保存";
      if (refresh) refresh.disabled = false;
    }
  }

  async function refreshExtensionWindows(button) {
    const editor = button.closest("[data-worker-window-editor]");
    const clientId = String(editor?.dataset.clientId || "");
    if (!clientId || windowRefreshPending.has(clientId)) return;
    windowRefreshPending.add(clientId);
    button.disabled = true;
    const old = button.textContent;
    button.textContent = "刷新中";
    try {
      const result = await api(`/api/admin/extensions/${encodeURIComponent(clientId)}/windows/refresh`, {method:"POST"});
      const snap = result?.window_snapshot || {};
      showActionResult(result?.ok === true ? `窗口已刷新：总数 ${Number(snap.total || 0)}，活动 ${Number(snap.active || 0)}，空闲 ${Number(snap.idle || 0)}` : `窗口刷新失败：${String(result?.error || "无返回")}`, result?.ok === true ? "ok" : "bad", button);
      await refreshWorkerSettings();
    } catch (error) {
      showActionResult(`窗口刷新失败：${String(error?.message || error)}`, "bad", button);
    } finally {
      windowRefreshPending.delete(clientId);
      button.disabled = false;
      button.textContent = old || "刷新";
    }
  }

  function ensureKeyConcurrencyHeader() {
    const body = document.getElementById("keysBody");
    const header = body?.closest("table")?.querySelector("thead tr");
    if (!body || !header) return false;
    let th = header.querySelector("th[data-key-concurrency-v57]");
    if (!th) {
      th = document.createElement("th");
      th.dataset.keyConcurrencyV57 = "1";
      th.textContent = "最大并发";
      th.title = "此 API Key 可同时执行的最大请求数，默认 3；超过后 FIFO 排队。";
      header.insertBefore(th, header.lastElementChild);
    }
    return true;
  }

  async function renderKeyConcurrency() {
    if (!keyViewActive() || keyRenderInFlight || typeof globalThis.api !== "function" || !ensureKeyConcurrencyHeader()) return;
    keyRenderInFlight = true;
    try {
      const [keys, capacity] = await Promise.all([api("/api/admin/keys"), api("/api/admin/capacity-v57")]);
      const rows = [...document.querySelectorAll("#keysBody tr")];
      const data = Array.isArray(keys.data) ? keys.data : [];
      const defaults = Number(capacity?.defaults?.api_key_max_concurrency || 3);
      const configured = capacity?.keys || {};
      const active = capacity?.key_active || {};
      rows.forEach((tr, index) => {
        const item = data[index];
        if (!item || tr.cells.length < 2) return;
        const keyId = String(item.key_id || "");
        let cell = tr.querySelector("td[data-key-concurrency-v57]");
        if (!cell) {
          cell = document.createElement("td");
          cell.dataset.keyConcurrencyV57 = "1";
          tr.insertBefore(cell, tr.lastElementChild);
        }
        const limit = Number(configured[keyId] || defaults);
        let editor = cell.querySelector("[data-key-capacity-editor]");
        if (!editor || String(editor.dataset.keyId || "") !== keyId) {
          cell.innerHTML = `<div data-key-capacity-editor data-key-id="${esc(keyId)}" style="display:flex;align-items:center;gap:6px"><span class="muted" data-key-active></span><input data-key-max type="number" min="${MIN_LIMIT}" max="${MAX_LIMIT}" value="${limit}" style="width:58px;padding:5px 6px"><button class="action" data-key-save style="padding:5px 8px">保存</button></div>`;
          editor = cell.querySelector("[data-key-capacity-editor]");
        }
        if (!editor) return;
        const activeNode = editor.querySelector("[data-key-active]");
        const activeText = `${Number(active[keyId] || 0)}/`;
        if (activeNode && activeNode.textContent !== activeText) activeNode.textContent = activeText;
        const input = editor.querySelector("[data-key-max]");
        if (input && !editor.contains(document.activeElement) && Number(input.value) !== limit) input.value = String(limit);
        cell.title = "当前活动请求 / 最大并发；超过上限时不报 409/180 秒容量错误，而是在服务器 FIFO 队列中等待。";
      });
    } catch (_) {
    } finally {
      keyRenderInFlight = false;
    }
  }

  function scheduleKeyRefresh() {
    if (keyRefreshScheduled) return;
    keyRefreshScheduled = true;
    requestAnimationFrame(() => {
      keyRefreshScheduled = false;
      renderKeyConcurrency().catch(() => {});
    });
  }

  async function saveKeyConcurrency(button) {
    const editor = button.closest("[data-key-capacity-editor]");
    const keyId = String(editor?.dataset.keyId || "");
    const maximum = Number(editor?.querySelector("[data-key-max]")?.value || 0);
    if (!keyId || !Number.isInteger(maximum) || maximum < MIN_LIMIT || maximum > MAX_LIMIT) {
      showActionResult(`Key 最大并发请输入 ${MIN_LIMIT}-${MAX_LIMIT} 的整数`, "bad", button);
      return;
    }
    button.disabled = true;
    const old = button.textContent;
    button.textContent = "保存中";
    try {
      await api(`/api/admin/keys/${encodeURIComponent(keyId)}/concurrency-v57`, {method:"PUT", body:{max_concurrency:maximum}});
      showActionResult(`API Key 最大并发已设置为 ${maximum}；超过部分将排队依次执行。`, "ok", button);
      await renderKeyConcurrency();
    } catch (error) {
      showActionResult(`API Key 并发设置失败：${String(error?.message || error)}`, "bad", button);
    } finally {
      button.disabled = false;
      button.textContent = old || "保存";
    }
  }

  document.addEventListener("click", event => {
    const workerSave = event.target?.closest?.("[data-worker-save]");
    if (workerSave) { event.preventDefault(); saveWorkerSettings(workerSave).catch(() => {}); return; }
    const workerRefresh = event.target?.closest?.("[data-worker-refresh]");
    if (workerRefresh) { event.preventDefault(); refreshExtensionWindows(workerRefresh).catch(() => {}); return; }
    const keySave = event.target?.closest?.("[data-key-save]");
    if (keySave) { event.preventDefault(); saveKeyConcurrency(keySave).catch(() => {}); return; }
    if (event.target?.closest?.("#extensionColumnSettingsButton, #extensionColumnSettingsMenu")) setTimeout(patchColumnSettingsLabels, 0);
  }, true);

  const extensionBody = document.getElementById("extensionDeviceBody");
  if (extensionBody) {
    new MutationObserver(mutations => {
      // Observe only direct tbody row replacement by the base list renderer.
      // Updating children inside our own Worker-window cell does not retrigger it.
      if (mutations.some(mutation => mutation.type === "childList")) {
        patchExtensionColumns();
        scheduleWorkerRefresh();
      }
    }).observe(extensionBody, {childList:true});
  }
  const keysBody = document.getElementById("keysBody");
  if (keysBody) new MutationObserver(() => scheduleKeyRefresh()).observe(keysBody, {childList:true});

  globalThis.chat2apiRefreshWorkerWindowEditorsV58 = rows => refreshWorkerSettings(rows);
  globalThis.__CHAT2API_WORKER_WINDOW_RENDER_OWNER_V58__ = {
    owner: "worker-window-v58",
    column: "platform",
    structural_updates: "create-once-update-values",
    polling: false,
  };

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · ${VERSION}`;
  patchExtensionColumns();
  patchColumnSettingsLabels();
  refreshWorkerSettings();
  renderKeyConcurrency();
})();
