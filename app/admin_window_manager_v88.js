(() => {
  const KEY = "__CHAT2API_ADMIN_WINDOW_MANAGER_V88__";
  if (globalThis[KEY]) return;
  const state = {
    revision: 88,
    navigation_revision: 89,
    truth_revision: 89,
    active: [],
    closed: [],
    workers: [],
    truth: null,
    signature: "",
    timer: null,
    refreshPromise: null,
    refreshController: null,
  };
  globalThis[KEY] = state;

  const POLL_MS = 2500;
  const FETCH_TIMEOUT_MS = 8000;
  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  const statusLabel = value => ({
    loading: "加载中",
    ready: "可接待",
    in_use: "正在调用",
    closed: "已关闭",
  })[String(value || "")] || String(value || "-");

  const formatTime = value => {
    const ms = Number(value || 0);
    if (!Number.isFinite(ms) || ms <= 0) return "-";
    try {
      return new Intl.DateTimeFormat("zh-CN", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
        hour12: false,
      }).format(new Date(ms));
    } catch (_) { return new Date(ms).toLocaleString(); }
  };

  function isActive() {
    return Boolean(document.getElementById("view-window-manager")?.classList.contains("active"));
  }

  function stopPolling() {
    if (state.timer !== null) {
      clearTimeout(state.timer);
      state.timer = null;
    }
    if (state.refreshController) {
      try { state.refreshController.abort(); } catch (_) {}
    }
  }

  function schedulePoll(delay = POLL_MS) {
    if (state.timer !== null) clearTimeout(state.timer);
    state.timer = null;
    if (!isActive() || document.hidden) return;
    state.timer = setTimeout(async () => {
      state.timer = null;
      if (!isActive() || document.hidden) return;
      await refresh(false);
      if (isActive() && !document.hidden) schedulePoll(POLL_MS);
    }, Math.max(250, Number(delay || POLL_MS)));
  }

  function installView() {
    const nav = document.querySelector(".nav");
    const content = document.querySelector(".content");
    if (!nav || !content) return false;

    if (!nav.querySelector('[data-view="window-manager"]')) {
      const button = document.createElement("button");
      button.dataset.view = "window-manager";
      button.textContent = "窗口管理";
      button.addEventListener("click", () => showWindowManager());
      const requests = nav.querySelector('[data-view="requests"]');
      if (requests?.nextSibling) nav.insertBefore(button, requests.nextSibling);
      else nav.appendChild(button);
    }

    if (!document.getElementById("view-window-manager")) {
      const section = document.createElement("section");
      section.className = "view";
      section.id = "view-window-manager";
      section.innerHTML = `
        <div class="panel">
          <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap">
            <div><h2 style="margin:0">窗口管理</h2><div class="muted">选择策略：按开启时间从早到晚，只调用最早的“可接待”备用窗口。请求中的窗口保持“正在调用”，成功后保留并进入 5 分钟同 Key 可接待租约。</div></div>
            <button class="action" id="wmRefresh">刷新</button>
          </div>
          <div id="wmTruthStatus" class="muted" style="margin-top:10px"></div>
          <h3>接待中窗口</h3>
          <div class="scroll"><table><thead><tr><th>窗口编号</th><th>设备码名称</th><th>请求ID</th><th>开启时间</th><th>状态</th><th>截图当前界面</th><th>查看</th></tr></thead><tbody id="wmActiveBody"></tbody></table></div>
          <h3 style="margin-top:22px">已关闭窗口</h3>
          <div class="scroll"><table><thead><tr><th>窗口编号</th><th>设备码名称</th><th>请求ID</th><th>开启时间</th><th>状态</th><th>截图当前界面</th><th>查看</th></tr></thead><tbody id="wmClosedBody"></tbody></table></div>
        </div>`;
      content.appendChild(section);
      section.querySelector("#wmRefresh")?.addEventListener("click", () => {
        refresh(true).finally(() => schedulePoll(POLL_MS));
      });
    }
    ensureScreenshotDialog();
    return true;
  }

  function ensureScreenshotDialog() {
    if (document.getElementById("wmScreenshotDialog")) return;
    const dialog = document.createElement("dialog");
    dialog.id = "wmScreenshotDialog";
    dialog.innerHTML = `
      <div class="dialogHead"><b id="wmScreenshotTitle">窗口截图</b><button class="action" data-close>关闭</button></div>
      <div class="dialogBody"><div id="wmScreenshotMeta" class="muted" style="margin-bottom:10px"></div><img id="wmScreenshotImage" alt="Worker window screenshot" style="display:block;max-width:100%;height:auto;margin:auto;border:1px solid var(--line);border-radius:10px"></div>`;
    dialog.querySelector("[data-close]")?.addEventListener("click", () => dialog.close());
    document.body.appendChild(dialog);
  }

  function showWindowManager() {
    if (!installView()) return;
    document.querySelectorAll(".view").forEach(view => view.classList.toggle("active", view.id === "view-window-manager"));
    document.querySelectorAll(".nav button").forEach(button => button.classList.toggle("active", button.dataset.view === "window-manager"));
    const title = document.getElementById("pageTitle");
    if (title) title.textContent = "窗口管理";
    if (location.hash !== "#window-manager") location.hash = "window-manager";
    refresh(true).finally(() => schedulePoll(POLL_MS));
  }

  function rowHtml(row, closed = false) {
    const clientId = String(row.client_id || "");
    const windowId = Number(row.window_id);
    const hasShot = String(row.screenshot_data_url || "").startsWith("data:image/");
    const device = String(row.device_name || row.device_code_id || clientId || "-");
    const req = String(row.request_id || "-");
    const canCapture = !closed && row.worker_online !== false && row.live_verified !== false && clientId && Number.isInteger(windowId);
    const title = [
      `window_id=${windowId}`,
      row.source ? `source=${row.source}` : "",
      row.live_verified === true ? "physical_truth=v89" : "",
      row.screenshot_error ? `截图错误=${row.screenshot_error}` : "",
    ].filter(Boolean).join(" · ");
    return `<tr data-client="${esc(clientId)}" data-window="${esc(windowId)}" title="${esc(title)}">
      <td>#${esc(row.window_no || "-")}</td>
      <td>${esc(device)}</td>
      <td><code>${esc(req)}</code></td>
      <td>${esc(formatTime(row.opened_at_ms))}</td>
      <td><span class="pill ${row.status === "ready" ? "ok" : row.status === "in_use" ? "warn" : ""}">${esc(statusLabel(closed ? "closed" : row.status))}</span></td>
      <td>${canCapture ? `<button class="action" data-capture>截图</button>` : "-"}</td>
      <td>${hasShot ? `<button class="action" data-view-shot>查看${row.screenshot_at_ms ? ` · ${esc(formatTime(row.screenshot_at_ms))}` : ""}</button>` : `<span class="muted">${row.screenshot_error ? esc(row.screenshot_error) : "暂无截图"}</span>`}</td>
    </tr>`;
  }

  function bindRows(root, rows, closed) {
    if (!root) return;
    root.innerHTML = rows.length ? rows.map(row => rowHtml(row, closed)).join("") : `<tr><td colspan="7" class="muted">暂无窗口</td></tr>`;
    root.querySelectorAll("tr[data-window]").forEach((tr, index) => {
      const row = rows[index];
      tr.querySelector("[data-capture]")?.addEventListener("click", buttonEvent => {
        buttonEvent.preventDefault();
        capture(row, buttonEvent.currentTarget);
      });
      tr.querySelector("[data-view-shot]")?.addEventListener("click", buttonEvent => {
        buttonEvent.preventDefault();
        viewScreenshot(row);
      });
    });
  }

  function renderTruthStatus() {
    const box = document.getElementById("wmTruthStatus");
    if (!box) return;
    const truth = state.truth && typeof state.truth === "object" ? state.truth : {};
    const online = Math.max(0, Number(truth.online_workers || 0));
    const verified = Math.max(0, Number(truth.verified_workers || 0));
    const unverified = Math.max(0, Number(truth.unverified_workers || 0));
    const suppressed = Math.max(0, Number(truth.cached_active_rows_suppressed || 0));
    if (Number(state.truth_revision || 0) < 89) {
      box.className = "warnText";
      box.textContent = "当前服务端尚未启用 v89 物理窗口核验；列表可能来自 Worker 历史遥测。";
      return;
    }
    if (online <= 0) {
      box.className = "muted";
      box.textContent = "实时物理核验：暂无在线 Worker。历史缓存不会计入“接待中窗口”。";
      return;
    }
    if (unverified > 0) {
      const reasons = state.workers
        .filter(row => row?.online && row?.live_verified !== true)
        .map(row => `${row.device_name || row.client_id || "Worker"}：${row.truth_status === "upgrade-required" ? "需升级到 Worker 0.8.27+" : row.truth_status === "refresh-timeout" ? "核验超时" : "未核验"}`)
        .join("；");
      box.className = "warnText";
      box.textContent = `实时物理核验：${verified}/${online} 个在线 Worker 已核验；${unverified} 个未核验。已抑制 ${suppressed} 条历史缓存窗口，不计入“接待中窗口”。${reasons ? ` ${reasons}` : ""}`;
      return;
    }
    box.className = "muted";
    box.textContent = `实时物理核验：${verified}/${online} 个在线 Worker 已核验。当前“接待中窗口”只显示本次从 Chrome 实际窗口图重新确认存在的窗口。`;
  }

  function render() {
    bindRows(document.getElementById("wmActiveBody"), state.active, false);
    bindRows(document.getElementById("wmClosedBody"), state.closed, true);
    renderTruthStatus();
  }

  async function refresh(force = false) {
    if (!isActive()) return null;
    if (state.refreshPromise) return state.refreshPromise;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    state.refreshController = controller;
    const task = (async () => {
      try {
        const response = await fetch("/api/admin/window-manager", {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          if (force) console.warn("window manager refresh failed", response.status);
          return null;
        }
        const payload = await response.json();
        if (!isActive()) return payload;
        const active = Array.isArray(payload.active) ? payload.active : [];
        const closed = Array.isArray(payload.closed) ? payload.closed : [];
        const workers = Array.isArray(payload.workers) ? payload.workers : [];
        const truth = payload.truth && typeof payload.truth === "object" ? payload.truth : null;
        const truthRevision = Number(payload.truth_revision || 0);
        const signature = JSON.stringify([
          truthRevision,
          truth,
          workers.map(row => [row.client_id, row.live_verified, row.truth_status, row.snapshot_updated_at_ms, row.cached_active_count]),
          active.map(row => [row.client_id, row.window_no, row.status, row.request_id, row.screenshot_at_ms]),
          closed.map(row => [row.client_id, row.window_no, row.closed_at_ms, row.screenshot_at_ms]),
        ]);
        state.active = active;
        state.closed = closed;
        state.workers = workers;
        state.truth = truth;
        state.truth_revision = truthRevision;
        if (force || signature !== state.signature) {
          state.signature = signature;
          render();
        } else {
          renderTruthStatus();
        }
        return payload;
      } catch (error) {
        if (error?.name !== "AbortError" && force) console.warn("window manager refresh failed", error);
        return null;
      } finally {
        clearTimeout(timeout);
        if (state.refreshController === controller) state.refreshController = null;
      }
    })();
    state.refreshPromise = task;
    try { return await task; } finally { if (state.refreshPromise === task) state.refreshPromise = null; }
  }

  async function capture(row, button) {
    if (!row?.client_id || !Number.isInteger(Number(row.window_id))) return;
    const original = button?.textContent || "截图";
    if (button) { button.disabled = true; button.textContent = "截图中…"; }
    try {
      const response = await fetch(`/api/admin/window-manager/${encodeURIComponent(row.client_id)}/${encodeURIComponent(row.window_id)}/capture`, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try { detail = (await response.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
      }
      const before = Number(row.screenshot_at_ms || 0);
      for (let attempt = 0; attempt < 10; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 350));
        if (!isActive()) break;
        await refresh(true);
        const updated = state.active.find(item => item.client_id === row.client_id && Number(item.window_id) === Number(row.window_id));
        if (Number(updated?.screenshot_at_ms || 0) > before) {
          viewScreenshot(updated);
          break;
        }
      }
    } catch (error) {
      alert(`截图失败：${String(error?.message || error)}`);
    } finally {
      if (button) { button.disabled = false; button.textContent = original; }
      if (isActive()) schedulePoll(POLL_MS);
    }
  }

  function viewScreenshot(row) {
    const dialog = document.getElementById("wmScreenshotDialog");
    const image = document.getElementById("wmScreenshotImage");
    const title = document.getElementById("wmScreenshotTitle");
    const meta = document.getElementById("wmScreenshotMeta");
    if (!dialog || !image || !String(row?.screenshot_data_url || "").startsWith("data:image/")) return;
    if (title) title.textContent = `窗口 #${row.window_no || "-"} · ${row.device_name || row.device_code_id || row.client_id || "Worker"}`;
    if (meta) meta.textContent = `窗口 ID：${row.window_id} · 截图时间：${formatTime(row.screenshot_at_ms)} · 请求 ID：${row.request_id || "-"}`;
    image.src = row.screenshot_data_url;
    dialog.showModal();
  }

  function paintRequestIds() {
    const header = document.querySelector("#view-requests table thead tr");
    const body = document.getElementById("rqBody");
    if (!header || !body) return;
    let th = header.querySelector("th[data-chat2api-request-id-v88]");
    if (!th) {
      th = document.createElement("th");
      th.dataset.chat2apiRequestIdV88 = "1";
      th.textContent = "请求ID";
      header.insertBefore(th, header.children[1] || null);
    }
    const index = Array.from(header.children).indexOf(th);
    const requestState = globalThis.__CHAT2API_ADMIN_DEVICE_IDENTITY_V47__;
    const rows = Array.isArray(requestState?.rows) ? requestState.rows : [];
    Array.from(body.querySelectorAll(":scope > tr")).forEach((tr, rowIndex) => {
      if (tr.children.length === 1) {
        tr.children[0].colSpan = Math.max(Number(tr.children[0].colSpan || 1), header.children.length);
        return;
      }
      let cell = tr.querySelector("td[data-chat2api-request-id-v88]");
      if (!cell) {
        cell = document.createElement("td");
        cell.dataset.chat2apiRequestIdV88 = "1";
        tr.insertBefore(cell, tr.children[index] || null);
      }
      const requestId = String(rows[rowIndex]?.request_id || "");
      const next = requestId || "-";
      if (cell.textContent !== next) cell.innerHTML = requestId ? `<code>${esc(requestId)}</code>` : "-";
      if (cell.title !== requestId) cell.title = requestId;
    });
  }

  function installRequestIdObserver() {
    const body = document.getElementById("rqBody");
    if (!body || body.dataset.chat2apiRequestIdObserverV88) return;
    body.dataset.chat2apiRequestIdObserverV88 = "1";
    new MutationObserver(() => queueMicrotask(paintRequestIds)).observe(body, { childList: true, subtree: false });
    paintRequestIds();
  }

  function installNavigationLifecycle() {
    document.addEventListener("click", event => {
      const button = event.target?.closest?.(".nav button[data-view]");
      if (!button) return;
      const view = String(button.dataset.view || "");
      if (view !== "window-manager") stopPolling();
      if (view === "requests") setTimeout(paintRequestIds, 0);
    }, true);
    window.addEventListener("hashchange", () => {
      const view = (location.hash || "").slice(1);
      if (view !== "window-manager") stopPolling();
      else if (isActive()) schedulePoll(0);
      if (view === "requests") setTimeout(paintRequestIds, 0);
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stopPolling();
      else if (isActive()) schedulePoll(0);
    });
  }

  function start() {
    if (!installView()) {
      setTimeout(start, 100);
      return;
    }
    installRequestIdObserver();
    installNavigationLifecycle();
    if ((location.hash || "").slice(1) === "window-manager") showWindowManager();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();