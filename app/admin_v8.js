(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.8";

  async function downloadAdmin(path, fallbackName) {
    if (!key()) throw new Error("请先连接管理员 CHAT2API_API_KEY");
    const response = await fetch(path, {
      headers: { Authorization: `Bearer ${key()}` },
      cache: "no-store",
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        message = data.detail || data.error || message;
      } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const matched = disposition.match(/filename="?([^";]+)"?/i);
    const filename = matched?.[1] || fallbackName;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  }

  function ensureDiagnosticControls() {
    const section = document.querySelector("#view-requests");
    if (!section) return;
    const toolbar = section.querySelector(".toolbar");
    if (toolbar && !document.querySelector("#rqDownloadDiagnostics")) {
      const button = document.createElement("button");
      button.className = "action";
      button.id = "rqDownloadDiagnostics";
      button.textContent = "下载诊断日志包";
      button.title = "下载最近请求、失败原因、HTTP Trace、扩展状态和服务端日志；不会包含 API Key、提示词正文或附件 base64";
      button.onclick = async () => {
        try {
          status("正在生成诊断日志包…");
          await downloadAdmin("/api/admin/diagnostics/export?limit=200", "chat2api-diagnostics.zip");
          status("诊断日志包已下载", "ok");
        } catch (error) {
          status(error.message, "bad");
        }
      };
      toolbar.appendChild(button);
    }

    const table = section.querySelector("table");
    const head = table?.querySelector("thead tr");
    if (head && !head.querySelector("th[data-chat2api-log]")) {
      const th = document.createElement("th");
      th.dataset.chat2apiLog = "1";
      th.textContent = "日志";
      head.appendChild(th);
    }

    if (!section.querySelector("[data-diagnostic-hint]")) {
      const hint = document.createElement("div");
      hint.dataset.diagnosticHint = "1";
      hint.className = "footer";
      hint.textContent = "排障建议：单条失败点“下载日志”；多个外部调用一起失败时点“下载诊断日志包”。日志会自动隐藏 API Key、Authorization、配对码、提示词正文和 base64 文件内容。";
      section.appendChild(hint);
    }
  }

  ensureDiagnosticControls();

  loadRequests = async function loadRequestsV8() {
    if (!key()) return;
    ensureDiagnosticControls();
    try {
      const p = new URLSearchParams({ limit: "100" });
      if ($("rqSearch").value.trim()) p.set("q", $("rqSearch").value.trim());
      if ($("rqStatus").value) p.set("status", $("rqStatus").value);
      if ($("rqModel").value.trim()) p.set("model", $("rqModel").value.trim());
      const d = await api("/api/admin/requests?" + p);
      $("rqBody").innerHTML = (d.data || []).map(r => `
        <tr>
          <td>${fmtTime(r.recorded_at)}</td>
          <td>${esc(r.request_type || "text")}</td>
          <td title="${esc(r.error || "")}">${pill(r.status)}</td>
          <td>${esc(r.api_key_name || "-")}</td>
          <td><code>${esc(r.requested_model)}</code></td>
          <td>${r.attachments_count ?? 0}</td>
          <td>${fmtMs(r.timings?.first_token_ms)}</td>
          <td>${fmtMs(r.timings?.total_ms)}</td>
          <td>${r.usage?.total_tokens ?? 0}</td>
          <td><button class="action" data-request-log="${esc(r.request_id)}">下载日志</button></td>
        </tr>`).join("") || '<tr><td colspan="10" class="muted">暂无请求记录</td></tr>';

      document.querySelectorAll("[data-request-log]").forEach(button => {
        button.onclick = async () => {
          const requestId = button.dataset.requestLog;
          try {
            status(`正在导出 ${requestId} 的日志…`);
            await downloadAdmin(`/api/admin/requests/${encodeURIComponent(requestId)}/log`, `chat2api-request-${requestId}.json`);
            status("请求日志已下载", "ok");
          } catch (error) {
            status(error.message, "bad");
          }
        };
      });
    } catch (error) {
      status(error.message, "bad");
    }
  };

  if ($("rqGo")) $("rqGo").onclick = loadRequests;
})();
