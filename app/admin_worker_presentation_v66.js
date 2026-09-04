(() => {
  const KEY = "__CHAT2API_WORKER_PRESENTATION_V66__";
  if (globalThis[KEY]) return;

  const VERSION = 66;
  const WINDOW_TRUTH_REVISION = 89;
  let refreshTask = null;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function callApi(path, options) {
    if (typeof globalThis.api === "function") return globalThis.api(path, options);
    if (typeof api === "function") return api(path, options);
    return Promise.reject(new Error("管理控制台 API helper 不可用"));
  }

  function parts() {
    const body = document.getElementById("extensionDeviceBody");
    const table = body?.closest("table") || null;
    const header = table?.querySelector("thead tr") || null;
    return { body, header };
  }

  function keyedChild(parent, key) {
    return [...(parent?.children || [])].find(node => String(node.dataset?.chat2apiColumnKey || "") === key) || null;
  }

  function liveWindowTruth(payload) {
    const authoritative = Number(payload?.truth_revision || 0) >= WINDOW_TRUTH_REVISION;
    const activeCounts = new Map();
    for (const row of Array.isArray(payload?.active) ? payload.active : []) {
      const clientId = String(row?.client_id || "");
      if (!clientId) continue;
      activeCounts.set(clientId, (activeCounts.get(clientId) || 0) + 1);
    }
    const result = new Map();
    for (const worker of Array.isArray(payload?.workers) ? payload.workers : []) {
      const clientId = String(worker?.client_id || "");
      if (!clientId) continue;
      result.set(clientId, {
        authoritative,
        liveVerified: authoritative && worker?.live_verified === true,
        status: String(worker?.truth_status || (authoritative ? "unverified" : "legacy")),
        physical: authoritative && worker?.live_verified === true ? (activeCounts.get(clientId) || 0) : null,
        cachedSuppressed: Math.max(0, Number(worker?.cached_active_count || 0)),
      });
    }
    return { authoritative, byClient: result };
  }

  function truthLabel(info) {
    if (!info?.authoritative || info?.liveVerified) return "";
    if (info.status === "upgrade-required") return "Worker 版本过旧，需升级后实时核验窗口";
    if (info.status === "offline") return "Worker 离线，无法实时核验窗口";
    if (info.status === "refresh-timeout") return "窗口实时核验超时";
    return "窗口尚未完成实时核验";
  }

  function occupancy(row, info = null) {
    const capacity = row?.capacity && typeof row.capacity === "object" ? row.capacity : {};
    const metadata = row?.metadata && typeof row.metadata === "object" ? row.metadata : {};
    const usedRaw = capacity.used_units ?? row?.active_api_calls ?? 0;
    const limitRaw = capacity.limit_units ?? row?.max_concurrency ?? row?.configured_max_concurrency ?? 0;
    const queueRaw = capacity.queued_requests ?? 0;
    const used = Number.isFinite(Number(usedRaw)) ? Math.max(0, Number(usedRaw)) : 0;
    const limit = Number.isFinite(Number(limitRaw)) ? Math.max(0, Number(limitRaw)) : 0;
    const queued = Number.isFinite(Number(queueRaw)) ? Math.max(0, Number(queueRaw)) : 0;
    const cooling = capacity.rate_limit_cooldown_active === true;
    const remaining = Number(capacity.rate_limit_cooldown_remaining_seconds || 0);

    let physicalKnown = false;
    let physical = 0;
    let source = "";
    if (info?.authoritative) {
      physicalKnown = info.liveVerified === true && Number.isFinite(Number(info.physical));
      physical = physicalKnown ? Math.max(0, Number(info.physical)) : 0;
      source = physicalKnown ? "实时物理核验" : truthLabel(info);
    } else {
      // Backward compatibility only. On v0.22.58+ the server provides v89
      // physical truth; older servers can still show their last Worker telemetry,
      // but the tooltip labels it as legacy so it is never confused with v89.
      const candidates = [metadata.reserve_window_all_chatgpt_windows, metadata.reserve_window_total];
      const physicalRaw = candidates.find(value => value !== null && value !== undefined && Number.isFinite(Number(value)));
      physicalKnown = physicalRaw !== undefined;
      physical = physicalKnown ? Math.max(0, Number(physicalRaw)) : 0;
      source = physicalKnown ? "旧版遥测（未实时核验）" : "未核验";
    }

    const physicalText = physicalKnown ? String(physical) : "?";
    const physicalStyle = physicalKnown
      ? "color:#22c55e;font-weight:700"
      : "color:#f59e0b;font-weight:700";
    const queueText = queued > 0 ? ` · 排队 ${queued}` : "";
    const suppressed = Number(info?.cachedSuppressed || 0) > 0
      ? `；已忽略 ${Math.max(0, Number(info.cachedSuppressed))} 条未核验历史窗口记录`
      : "";
    return {
      text: `${used} / ${physicalText}${queueText}`,
      html: `${used} / <span data-chat2api-live-window-count="1" style="${physicalStyle}">${physicalText}</span>${queueText}`,
      title: `正在执行请求 ${used}；实际 ChatGPT 窗口 ${physicalText}（${source || "未核验"}）；并发上限 ${limit || "-"}${queued > 0 ? `；排队 ${queued}` : ""}${cooling ? `；额度冷却 ${Math.max(0, Math.ceil(remaining))} 秒` : ""}${suppressed}`,
      cls: used > 0 ? "warnText" : "muted",
    };
  }

  function ensureHeader() {
    const { header } = parts();
    if (!header) return;
    if (!keyedChild(header, "device_name")) {
      const th = document.createElement("th");
      th.dataset.chat2apiColumnKey = "device_name";
      th.dataset.chat2apiPresentationV66 = "1";
      th.textContent = "设备名称";
      header.appendChild(th);
    }
    let occupancyHeader = keyedChild(header, "occupancy");
    if (!occupancyHeader) {
      occupancyHeader = document.createElement("th");
      occupancyHeader.dataset.chat2apiColumnKey = "occupancy";
      occupancyHeader.dataset.chat2apiPresentationV66 = "1";
      header.appendChild(occupancyHeader);
    }
    occupancyHeader.textContent = "请求 / 实际窗口";
    occupancyHeader.title = "正在执行的请求数 / 经 Worker 实时物理核验的 ChatGPT 窗口数；并发上限在“并发设置”列单独配置。";
  }

  function applyRows(rows, truthPayload = null) {
    const { body, header } = parts();
    if (!body || !header) return;
    ensureHeader();
    const truth = liveWindowTruth(truthPayload);
    const byClient = new Map((Array.isArray(rows) ? rows : []).map(row => [String(row?.client_id || ""), row]));
    for (const tr of body.rows) {
      if (tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan")) {
        tr.cells[0].colSpan = Math.max(Number(tr.cells[0].colSpan || 1), header.children.length);
        continue;
      }
      const clientId = String(tr.dataset?.clientId || keyedChild(tr, "client_id")?.textContent || "").trim();
      const row = byClient.get(clientId) || null;
      let nameCell = keyedChild(tr, "device_name");
      if (!nameCell) {
        nameCell = document.createElement("td");
        nameCell.dataset.chat2apiColumnKey = "device_name";
        nameCell.dataset.chat2apiPresentationV66 = "1";
        tr.appendChild(nameCell);
      }
      const name = String(row?.device_name || "").trim();
      const nextName = name
        ? `<span title="${esc(row?.device_code_id || row?.pairing_id || "")}">${esc(name)}</span>`
        : '<span class="muted">-</span>';
      if (nameCell.innerHTML !== nextName) nameCell.innerHTML = nextName;

      let occupancyCell = keyedChild(tr, "occupancy");
      if (!occupancyCell) {
        occupancyCell = document.createElement("td");
        occupancyCell.dataset.chat2apiColumnKey = "occupancy";
        occupancyCell.dataset.chat2apiPresentationV66 = "1";
        tr.appendChild(occupancyCell);
      }
      const value = occupancy(row, truth.byClient.get(clientId) || (truth.authoritative ? { authoritative: true, liveVerified: false, status: "unverified", physical: null, cachedSuppressed: 0 } : null));
      if (occupancyCell.innerHTML !== value.html) occupancyCell.innerHTML = value.html;
      if (occupancyCell.className !== value.cls) occupancyCell.className = value.cls;
      if (occupancyCell.title !== value.title) occupancyCell.title = value.title;
    }
  }

  function applyPairings(rows) {
    const body = document.getElementById("pairingBody");
    if (!body) return;
    const list = Array.isArray(rows) ? rows : [];
    [...body.rows].forEach((tr, index) => {
      const row = list[index];
      if (!row) return;
      const id = String(row.pairing_id || "");
      const actions = tr.querySelector(".rowactions");
      if (!actions) return;
      let button = actions.querySelector("[data-v66-pairing-rename]");
      if (!button) {
        button = document.createElement("button");
        button.className = "action";
        button.dataset.v66PairingRename = "1";
        button.textContent = "改名";
        actions.insertBefore(button, actions.firstChild || null);
      }
      button.dataset.pairingId = id;
      button.dataset.pairingName = String(row.name || "");
    });
  }

  async function refresh(force = false) {
    if (refreshTask) return refreshTask;
    const view = document.getElementById("view-extensions");
    if (!force && view && !view.classList.contains("active")) return null;
    const task = (async () => {
      const [data, truthPayload] = await Promise.all([
        callApi("/api/admin/extensions"),
        callApi("/api/admin/window-manager").catch(error => {
          console.warn("chat2api physical window truth refresh failed", error);
          return null;
        }),
      ]);
      applyRows(data?.clients, truthPayload);
      applyPairings(data?.pairing_codes);
      document.documentElement.dataset.chat2apiWorkerPresentationRevision = String(VERSION);
      document.documentElement.dataset.chat2apiWindowTruthRevision = String(Number(truthPayload?.truth_revision || 0));
      return data;
    })().catch(error => {
      console.warn("chat2api worker presentation v66 refresh failed", error);
      return null;
    });
    refreshTask = task;
    try { return await task; } finally { if (refreshTask === task) refreshTask = null; }
  }

  function installReloadHook() {
    const base = globalThis.chat2apiReloadCanonicalWorkerListV59;
    if (typeof base !== "function" || base.__chat2apiPresentationV66) return;
    const wrapped = async (...args) => {
      const result = await base(...args);
      await refresh(true);
      return result;
    };
    wrapped.__chat2apiPresentationV66 = true;
    globalThis.chat2apiReloadCanonicalWorkerListV59 = wrapped;
  }

  function installShowHook() {
    const base = globalThis.show;
    if (typeof base !== "function" || base.__chat2apiPresentationV66) return;
    const wrapped = async (...args) => {
      const result = await base(...args);
      if (args[0] === "extensions") await refresh(true);
      return result;
    };
    wrapped.__chat2apiPresentationV66 = true;
    globalThis.show = wrapped;
  }

  document.addEventListener("click", async event => {
    const rename = event.target?.closest?.("[data-v66-pairing-rename]");
    if (!rename) return;
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
      const reload = globalThis.chat2apiReloadCanonicalWorkerListV59;
      if (typeof reload === "function") await reload();
      else await refresh(true);
    } catch (error) {
      if (typeof globalThis.status === "function") globalThis.status("设备名称修改失败：" + String(error?.message || error), "bad");
    }
  }, true);

  function start() {
    installReloadHook();
    installShowHook();
    // Two bounded startup passes cover the canonical renderer's initial async
    // fetch without creating any autonomous observer or repeating-timer loop.
    setTimeout(() => refresh(true), 120);
    setTimeout(() => refresh(true), 900);
  }

  globalThis[KEY] = Object.freeze({ version: VERSION, refresh, applyRows, applyPairings, liveWindowTruth });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();