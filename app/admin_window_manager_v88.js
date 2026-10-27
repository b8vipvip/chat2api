(() => {
  const KEY = "__CHAT2API_ADMIN_WINDOW_MANAGER_V88__";
  if (globalThis[KEY]) return;
  const state = {
    revision: 152,
    navigation_revision: 94,
    truth_revision: 89,
    structural_owner: "window-manager-only",
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

  function statusLabel(row, closed = false) {
    if (closed || String(row?.status || "") === "closed") return "已关闭";
    const value = String(row?.status || "");
    if (value === "ready") return row.server_admission_available === false ? "待机（调度容量已满）" : "可接待";
    if (value === "in_use" || value === "leased") {
      const name = String(row?.api_key_name || row?.route_key || "").trim();
      return `正在接待${name || "API请求"}`;
    }
    if (value === "loading") return "加载中";
    return value || "-";
  }

  const formatTime = value => {
    const ms = Number(value || 0);
    if (!Number.isFinite(ms) || ms <= 0) return "-";
    try {
      return new Intl.DateTimeFormat("zh-CN", {
        year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false,
      }).format(new Date(ms));
    } catch (_) { return new Date(ms).toLocaleString(); }
  };

  function isActive() {
    return Boolean(document.getElementById("view-window-manager")?.classList.contains("active"));
  }

  function stopPolling() {
    if (state.timer !== null) { clearTimeout(state.timer); state.timer = null; }
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
            <div><h2 style="margin:0">窗口管理</h2><div class="muted">备用窗口表示浏览器页面已经准备好，不代表服务端还有分发额度。只有调度额度与物理窗口同时可用时，新请求才能接待。请求终态及窗口关闭分别由现有请求控制器和窗口管理器决定。</div></div>
            <button class="action" id="wmRefresh">刷新</button>
          </div>
          <div id="wmTruthStatus" class="muted" style="margin-top:10px"></div>
          <div id="wmSchedulerCapacity" style="margin:12px 0"></div>
          <h3>当前窗口</h3>
          <div class="scroll"><table><thead><tr><th>窗口标识</th><th>设备码名称</th><th>请求ID</th><th>开启时间</th><th>状态</th><th>截图当前界面</th><th>查看</th></tr></thead><tbody id="wmActiveBody"></tbody></table></div>
          <h3 style="margin-top:22px">已关闭窗口</h3>
          <div class="scroll"><table><thead><tr><th>窗口标识</th><th>设备码名称</th><th>请求ID</th><th>开启时间</th><th>状态</th><th>截图当前界面</th><th>查看</th></tr></thead><tbody id="wmClosedBody"></tbody></table></div>
        </div>`;
      content.appendChild(section);
      section.querySelector("#wmRefresh")?.addEventListener("click", () => refresh(true).finally(() => schedulePoll(POLL_MS)));
    }
    ensureScreenshotDialog();
    return true;
  }

  function ensureScreenshotDialog() {
    if (document.getElementById("wmScreenshotDialog")) return;
    const dialog = document.createElement("dialog");
    dialog.id = "wmScreenshotDialog";
    dialog.innerHTML = `<div class="dialogHead"><b id="wmScreenshotTitle">窗口截图</b><button class="action" data-close>关闭</button></div><div class="dialogBody"><div id="wmScreenshotMeta" class="muted" style="margin-bottom:10px"></div><img id="wmScreenshotImage" alt="Worker window screenshot" style="display:block;max-width:100%;height:auto;margin:auto;border:1px solid var(--line);border-radius:10px"></div>`;
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

  function windowIdentity(clientId, windowNo) {
    const workerTail = String(clientId || "").trim().slice(-4) || "????";
    const localNo = Number(windowNo);
    return Number.isFinite(localNo) && localNo > 0 ? `${workerTail}#${localNo}` : "-";
  }

  function rowHtml(row, closed = false) {
    const clientId = String(row.client_id || "");
    const windowId = Number(row.window_id);
    const hasShot = String(row.screenshot_data_url || "").startsWith("data:image/");
    const device = String(row.device_name || row.device_code_id || clientId || "-");
    const req = String(row.request_id || "-");
    const canCapture = !closed && row.worker_online !== false && row.live_verified !== false && clientId && Number.isInteger(windowId);
    const leased = row.status === "leased" || row.status === "in_use";
    const title = [
      `window_id=${windowId}`,
      row.source ? `source=${row.source}` : "",
      row.api_key_name ? `api_key=${row.api_key_name}` : "",
      Number(row.lease_until_ms || 0) > 0 ? `lease_until=${formatTime(row.lease_until_ms)}` : "",
      row.live_verified === true ? "physical_truth=v89" : "",
      row.screenshot_error ? `截图错误=${row.screenshot_error}` : "",
    ].filter(Boolean).join(" · ");
    return `<tr data-client="${esc(clientId)}" data-window="${esc(windowId)}" title="${esc(title)}">
      <td>${esc(windowIdentity(clientId, row.window_no))}</td>
      <td>${esc(device)}</td>
      <td><code>${esc(req)}</code></td>
      <td>${esc(formatTime(row.opened_at_ms))}</td>
      <td><span class="pill ${row.status === "ready" && row.server_admission_available !== false ? "ok" : leased || row.server_admission_available === false ? "warn" : ""}">${esc(statusLabel(row, closed))}</span></td>
      <td>${canCapture ? '<button class="action" data-capture>截图</button>' : "-"}</td>
      <td>${hasShot ? `<button class="action" data-view-shot>查看${row.screenshot_at_ms ? ` · ${esc(formatTime(row.screenshot_at_ms))}` : ""}</button>` : `<span class="muted">${row.screenshot_error ? esc(row.screenshot_error) : "暂无截图"}</span>`}</td>
    </tr>`;
  }

  function bindRows(root, rows, closed) {
    if (!root) return;
    root.innerHTML = rows.length ? rows.map(row => rowHtml(row, closed)).join("") : '<tr><td colspan="7" class="muted">暂无窗口</td></tr>';
    root.querySelectorAll("tr[data-window]").forEach((tr, index) => {
      const row = rows[index];
      tr.querySelector("[data-capture]")?.addEventListener("click", event => { event.preventDefault(); capture(row, event.currentTarget); });
      tr.querySelector("[data-view-shot]")?.addEventListener("click", event => { event.preventDefault(); viewScreenshot(row); });
    });
  }

  function renderTruthStatus() {
    const box = document.getElementById("wmTruthStatus");
    if (!box) return;
    const truth = state.truth && typeof state.truth === "object" ? state.truth : {};
    const online = Math.max(0, Number(truth.online_workers || 0));
    const verified = Math.max(0, Number(truth.verified_workers || 0));
    const unverified = Math.max(0, Number(truth.unverified_workers || 0));
    const standby = Math.max(0, Number(truth.standby_windows || 0));
    const leased = Math.max(0, Number(truth.leased_route_windows || 0));
    const inUse = Math.max(0, Number(truth.in_use_windows || 0));
    if (Number(state.truth_revision || 0) < 89) {
      box.className = "warnText";
      box.textContent = "当前服务端尚未启用实时物理窗口核验。";
      return;
    }
    if (online <= 0) {
      box.className = "muted";
      box.textContent = "实时物理核验：暂无在线 Worker。历史缓存不会计入备用窗口。";
      return;
    }
    box.className = unverified > 0 ? "warnText" : "muted";
    const scheduled = Math.max(0, Number(truth.scheduler_used_units || 0));
    const limit = Math.max(0, Number(truth.scheduler_limit_units || 0));
    const queued = Math.max(0, Number(truth.scheduler_queued_requests || 0));
    box.textContent = `实时物理核验：${verified}/${online} 个在线 Worker 已核验；物理待机 ${standby} 个，执行中 ${inUse} 个；服务端调度已占用 ${scheduled}/${limit}，排队 ${queued} 个；5分钟接待租约 ${leased} 个。${unverified > 0 ? `另有 ${unverified} 个 Worker 未完成核验。` : ""}`;
  }

  function renderCapacityControls() {
    const box = document.getElementById("wmSchedulerCapacity");
    if (!box) return;
    const workers = state.workers.filter(worker => worker.online && worker.live_verified);
    box.replaceChildren();
    const explanation = document.createElement("div");
    explanation.className = "muted";
    explanation.textContent = "Worker并发是服务端v58唯一准入上限（默认3），与物理待机窗口数量不是同一个值。可以按设备资源调整；提高上限会增加浏览器内存及账号请求负载。";
    box.appendChild(explanation);
    for (const worker of workers) {
      const wrap = document.createElement("div");
      wrap.style.cssText = "display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px";
      const used = Number(worker.scheduler_used_units || 0);
      const limit = Number(worker.scheduler_limit_units || 0);
      const available = Number(worker.scheduler_available_units || 0);
      const queued = Number(worker.scheduler_queued_requests || 0);
      const idle = Number(worker.standby_window_count || 0);
      const label = document.createElement("span");
      label.textContent = `${worker.device_name || worker.client_id} · 分发额度 ${used}/${limit} · 可准入 ${available} · 物理待机 ${idle} · 排队 ${queued}`;
      wrap.appendChild(label);
      const input = document.createElement("input");
      input.type = "number"; input.min = "1"; input.max = "32"; input.step = "1";
      input.value = String(limit || 3);
      input.style.cssText = "width:68px;max-width:68px";
      input.setAttribute("aria-label", `设置${worker.device_name || worker.client_id}最大并发`);
      wrap.appendChild(input);
      const button = document.createElement("button");
      button.type = "button";button.className = "action";button.textContent = "更新并发额度";
      button.addEventListener("click", async () => {
        const target = Number(input.value);
        if (!Number.isInteger(target) || target < 1 || target > 32) {
          alert("Worker并发额度应为1至32的整数");return;
        }
        if (target === limit) return;
        if (!confirm(`将 ${worker.device_name || worker.client_id} 的Worker最大并发从 ${limit} 调整为 ${target}？增加会占用更多浏览器资源；只改变v58准入额度，不改变终态判定。`)) return;
        button.disabled = true;
        try {
          const response = await fetch(`/api/admin/extensions/${encodeURIComponent(worker.client_id)}/capacity-v57`, {
            method: "PUT", credentials: "same-origin",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({max_concurrency:target})
          });
          if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || `HTTP ${response.status}`);
          }
          await refresh(true);
        } catch (error) {
          alert("更新Worker并发失败："+String(error.message || error));
        } finally { button.disabled = false; }
      });
      wrap.appendChild(button);
      // A live verified physical standby is not a permit by itself. Offer a
      // deliberate one-click adjustment without silently changing capacity.
      const proposed = Math.min(32, used + idle);
      if (proposed > limit && worker.chatgpt_routing_ready === true) {
        const proposal = document.createElement("button");
        proposal.type = "button"; proposal.className = "action";
        proposal.textContent = `按当前${proposed}个窗口设定`;
        proposal.title = "将当前执行窗口和实测待机窗口合计作为新的并发上限；浏览器资源开销会增加";
        proposal.addEventListener("click", () => {
          input.value = String(proposed);
          button.click();
        });
        wrap.appendChild(proposal);
      }
      box.appendChild(wrap);
    }
  }

  function render() {
    bindRows(document.getElementById("wmActiveBody"), state.active, false);
    bindRows(document.getElementById("wmClosedBody"), state.closed, true);
    renderTruthStatus();
    renderCapacityControls();
  }

  async function refresh(force = false) {
    if (!isActive()) return null;
    if (state.refreshPromise) return state.refreshPromise;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    state.refreshController = controller;
    const task = (async () => {
      try {
        const response = await fetch("/api/admin/window-manager", {credentials:"same-origin", cache:"no-store", signal:controller.signal});
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
          truthRevision, truth,
          workers.map(row => [row.client_id, row.live_verified, row.truth_status, row.standby_window_count, row.leased_route_window_count, row.scheduler_limit_units, row.scheduler_used_units, row.scheduler_queued_requests]),
          active.map(row => [row.client_id, row.window_no, row.status, row.request_id, row.route_key, row.api_key_name, row.lease_until_ms, row.screenshot_at_ms]),
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
        } else renderTruthStatus();
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
      const response = await fetch(`/api/admin/window-manager/${encodeURIComponent(row.client_id)}/${encodeURIComponent(row.window_id)}/capture`, {method:"POST", credentials:"same-origin", cache:"no-store"});
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
        if (Number(updated?.screenshot_at_ms || 0) > before) { viewScreenshot(updated); break; }
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
    if (meta) meta.textContent = `窗口 ID：${row.window_id} · 截图时间：${formatTime(row.screenshot_at_ms)} · 状态：${statusLabel(row, false)}`;
    image.src = row.screenshot_data_url;
    dialog.showModal();
  }

  function installNavigationLifecycle() {
    document.addEventListener("click", event => {
      const button = event.target?.closest?.(".nav button[data-view]");
      if (!button) return;
      if (String(button.dataset.view || "") !== "window-manager") stopPolling();
    }, true);
    window.addEventListener("hashchange", () => {
      const view = (location.hash || "").slice(1);
      if (view !== "window-manager") stopPolling();
      else if (isActive()) schedulePoll(0);
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stopPolling();
      else if (isActive()) schedulePoll(0);
    });
  }

  function start() {
    if (!installView()) { setTimeout(start, 100); return; }
    installNavigationLifecycle();
    if ((location.hash || "").slice(1) === "window-manager") showWindowManager();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once:true});
  else start();
})();
