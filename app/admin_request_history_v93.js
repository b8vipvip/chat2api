(() => {
  const KEY = "__CHAT2API_REQUEST_HISTORY_OWNER_V93__";
  if (globalThis[KEY]) return;

  const state = {
    revision: 93,
    loading: false,
    loadEpoch: 0,
    lastRows: [],
  };
  globalThis[KEY] = state;

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  const fmtTimeSafe = value => {
    try {
      if (typeof globalThis.fmtTime === "function") return globalThis.fmtTime(value);
    } catch (_) {}
    if (!value) return "-";
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", { hour12: false }) : String(value);
  };

  const fmtMsSafe = value => {
    try {
      if (typeof globalThis.fmtMs === "function") return globalThis.fmtMs(value);
    } catch (_) {}
    const n = Number(value);
    return Number.isFinite(n) ? `${Math.round(n)} ms` : "-";
  };

  const pillSafe = value => {
    try {
      if (typeof globalThis.pill === "function") return globalThis.pill(value);
    } catch (_) {}
    return `<span class="pill">${esc(value || "-")}</span>`;
  };

  function requestTable() {
    const body = $("rqBody");
    return { body, table: body?.closest("table") || null, header: body?.closest("table")?.querySelector("thead tr") || null };
  }

  function installCanonicalHeader() {
    const { header } = requestTable();
    if (!header) return false;
    header.dataset.chat2apiRequestOwner = "v93";
    header.innerHTML = `
      <th>时间（北京时间）</th>
      <th data-chat2api-request-id-v88="1">请求ID</th>
      <th>类型</th>
      <th>状态</th>
      <th>Key</th>
      <th data-chat2api-device-identity="1">设备标识</th>
      <th>模型</th>
      <th>附件</th>
      <th>首包</th>
      <th>总耗时</th>
      <th>Token</th>
      <th data-prompt-column="1">提示词</th>
      <th>日志</th>`;
    return true;
  }

  function deviceLabel(row) {
    const name = String(row?.device_name || "").trim();
    const client = String(row?.worker_client_id || row?.client_id || "").trim();
    return name || client || "-";
  }

  function deviceTitle(row) {
    const client = String(row?.worker_client_id || row?.client_id || "").trim();
    const code = String(row?.device_code_id || "").trim();
    return [client ? `Worker ID：${client}` : "", code ? `设备码 ID：${code}` : ""].filter(Boolean).join(" · ");
  }

  function rowHtml(row) {
    const requestId = String(row?.request_id || "").trim();
    const keyName = String(row?.api_key_name || row?.key_name || row?.key_id || "-");
    const model = String(row?.requested_model || row?.model || "-");
    const requestType = String(row?.request_type || row?.type || "text");
    const attachments = Number(row?.attachments_count ?? row?.attachments?.length ?? 0);
    const tokens = Number(row?.usage?.total_tokens ?? row?.total_tokens ?? 0);
    return `<tr data-request-id="${esc(requestId)}" data-chat2api-request-owner="v93">
      <td>${esc(fmtTimeSafe(row?.recorded_at || row?.created_at))}</td>
      <td data-chat2api-request-id-v88="1" title="${esc(requestId)}"><code>${esc(requestId || "-")}</code></td>
      <td>${esc(requestType)}</td>
      <td>${pillSafe(row?.status)}</td>
      <td>${esc(keyName)}</td>
      <td data-chat2api-device-identity="1" title="${esc(deviceTitle(row))}">${esc(deviceLabel(row))}</td>
      <td><code>${esc(model)}</code></td>
      <td>${attachments}</td>
      <td>${esc(fmtMsSafe(row?.timings?.first_token_ms))}</td>
      <td>${esc(fmtMsSafe(row?.timings?.total_ms))}</td>
      <td>${tokens}</td>
      <td data-prompt-cell="1">${requestId ? `<button type="button" class="action" data-request-action="prompt" data-request-id="${esc(requestId)}">提示词</button>` : "-"}</td>
      <td>${requestId ? `<button type="button" class="action" data-request-action="detail" data-request-id="${esc(requestId)}">日志</button>` : "-"}</td>
    </tr>`;
  }

  function renderRows(rows) {
    const { body } = requestTable();
    if (!body) return;
    installCanonicalHeader();
    state.lastRows = Array.isArray(rows) ? rows.map(row => ({ ...row })) : [];
    body.dataset.chat2apiRequestOwner = "v93";
    body.innerHTML = state.lastRows.length
      ? state.lastRows.map(rowHtml).join("")
      : '<tr><td colspan="13" class="muted">暂无请求记录</td></tr>';
  }

  function queryParams() {
    const p = new URLSearchParams({ limit: "100" });
    const search = $("rqSearch")?.value?.trim() || "";
    const status = $("rqStatus")?.value || "";
    const model = $("rqModel")?.value?.trim() || "";
    if (search) p.set("q", search);
    if (status) p.set("status", status);
    if (model) p.set("model", model);
    return p;
  }

  async function loadRequestsV93() {
    if (state.loading) return;
    try {
      if (typeof globalThis.key === "function" && !globalThis.key()) return;
    } catch (_) {}
    if (typeof globalThis.api !== "function") return;

    const epoch = ++state.loadEpoch;
    state.loading = true;
    try {
      const payload = await globalThis.api(`/api/admin/requests?${queryParams()}`);
      if (epoch !== state.loadEpoch) return;
      renderRows(Array.isArray(payload?.data) ? payload.data : []);
    } catch (error) {
      if (epoch !== state.loadEpoch) return;
      const { body } = requestTable();
      installCanonicalHeader();
      if (body) body.innerHTML = `<tr><td colspan="13" class="bad">请求记录加载失败：${esc(error?.message || error)}</td></tr>`;
      try {
        if (typeof globalThis.status === "function") globalThis.status(`请求记录加载失败：${String(error?.message || error)}`, "bad");
      } catch (_) {}
    } finally {
      if (epoch === state.loadEpoch) state.loading = false;
    }
  }

  function ensureDetailDialog() {
    let dialog = $("requestDetailV93");
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.id = "requestDetailV93";
    dialog.style.width = "min(1050px,94vw)";
    dialog.style.maxHeight = "90vh";
    dialog.innerHTML = `
      <div class="dialogHead"><b id="requestDetailTitleV93">请求日志</b><button type="button" class="action" data-close>关闭</button></div>
      <div class="dialogBody">
        <div id="requestDetailMetaV93" class="muted" style="margin-bottom:10px"></div>
        <pre id="requestDetailTextV93" style="white-space:pre-wrap;word-break:break-word;max-height:65vh;overflow:auto"></pre>
        <div style="display:flex;justify-content:flex-end;margin-top:10px"><button type="button" class="action" id="requestDetailDownloadV93">下载 JSON</button></div>
      </div>`;
    dialog.querySelector("[data-close]")?.addEventListener("click", () => dialog.close());
    document.body.appendChild(dialog);
    return dialog;
  }

  async function showDetail(requestId) {
    const dialog = ensureDetailDialog();
    const title = $("requestDetailTitleV93");
    const meta = $("requestDetailMetaV93");
    const text = $("requestDetailTextV93");
    const download = $("requestDetailDownloadV93");
    if (title) title.textContent = `请求日志 · ${requestId}`;
    if (meta) meta.textContent = "正在加载…";
    if (text) text.textContent = "";
    dialog.showModal();
    try {
      const payload = await globalThis.api(`/api/admin/requests/${encodeURIComponent(requestId)}`);
      const serialized = JSON.stringify(payload, null, 2);
      if (text) text.textContent = serialized;
      if (meta) meta.textContent = `请求 ${requestId} · ${serialized.length} 字符`;
      if (download) {
        download.onclick = () => {
          const blob = new Blob([serialized], { type: "application/json;charset=utf-8" });
          const href = URL.createObjectURL(blob);
          const anchor = document.createElement("a");
          anchor.href = href;
          anchor.download = `chat2api-request-${requestId}.json`;
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
          setTimeout(() => URL.revokeObjectURL(href), 0);
        };
      }
    } catch (error) {
      if (meta) meta.textContent = `请求 ${requestId}`;
      if (text) text.textContent = `加载失败：${String(error?.message || error)}`;
      if (download) download.onclick = null;
    }
  }

  function installActions() {
    const { body } = requestTable();
    if (!body || body.dataset.chat2apiRequestActionsV93 === "1") return;
    body.dataset.chat2apiRequestActionsV93 = "1";
    body.addEventListener("click", event => {
      const button = event.target?.closest?.("button[data-request-action][data-request-id]");
      if (!button || !body.contains(button)) return;
      event.preventDefault();
      event.stopPropagation();
      const requestId = String(button.dataset.requestId || "");
      if (!requestId) return;
      if (button.dataset.requestAction === "prompt") {
        if (typeof globalThis.showRequestPromptV72 === "function") globalThis.showRequestPromptV72(requestId);
        return;
      }
      if (button.dataset.requestAction === "detail") showDetail(requestId);
    });
  }

  function install() {
    if (!requestTable().body) return false;
    installCanonicalHeader();
    installActions();
    globalThis.loadRequests = loadRequestsV93;
    try { loadRequests = loadRequestsV93; } catch (_) {}
    const go = $("rqGo");
    if (go) go.onclick = loadRequestsV93;
    state.owner = "request-history-v93";
    state.owns = ["fetch", "columns", "rows", "request-actions"];
    return true;
  }

  function start() {
    if (!install()) {
      setTimeout(start, 100);
      return;
    }
    if ($("view-requests")?.classList.contains("active")) setTimeout(loadRequestsV93, 0);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
