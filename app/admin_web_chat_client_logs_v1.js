/* Web Chat client diagnostic log viewer and ZIP export. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const endpoint = "/api/admin/diagnostics/web-chat-client-logs";

  function setStatus(message, bad = false) {
    const node = $("webChatClientLogStatus");
    if (!node) return;
    node.textContent = message;
    node.className = bad ? "bad" : "muted";
  }

  async function api(path) {
    const response = await fetch(path, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      let detail = "HTTP " + response.status;
      try {
        const payload = await response.json();
        detail = String(payload.detail || detail);
      } catch {}
      throw new Error(detail);
    }
    return response;
  }

  function filters() {
    const params = new URLSearchParams();
    const userId = $("webChatClientLogUser")?.value.trim() || "";
    if (userId) params.set("user_id", userId);
    const sinceValue = $("webChatClientLogSince")?.value || "";
    const untilValue = $("webChatClientLogUntil")?.value || "";
    if (sinceValue || untilValue) {
      if (!sinceValue || !untilValue) throw new Error("按时间范围筛选时，开始和结束时间都必须填写。");
      const since = new Date(sinceValue);
      const until = new Date(untilValue);
      if (!Number.isFinite(since.getTime()) || !Number.isFinite(until.getTime()) || until <= since) {
        throw new Error("时间范围无效。");
      }
      params.set("since", since.toISOString());
      params.set("until", until.toISOString());
      return { params, timeRange: true };
    }
    params.set("limit", $("webChatClientLogLimit")?.value || "200");
    return { params, timeRange: false };
  }

  function addCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = value == null || value === "" ? "-" : String(value);
    row.appendChild(cell);
    return cell;
  }

  function renderRows(rows) {
    const root = $("webChatClientLogRows");
    if (!root) return;
    root.replaceChildren();
    if (!rows.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 9;
      cell.textContent = "当前筛选条件下没有客户端日志。";
      row.appendChild(cell);
      root.appendChild(row);
      return;
    }
    for (const item of rows) {
      const row = document.createElement("tr");
      addCell(row, item.recorded_at);
      addCell(row, item.user_id);
      addCell(row, item.event);
      addCell(row, item.level);
      addCell(row, item.model_id);
      addCell(row, item.phase);
      addCell(row, item.http_status);
      addCell(row, item.duration_ms == null ? "-" : item.duration_ms + " ms");
      const details = addCell(row, item.error_name || item.error_message || "");
      const detail = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "详情";
      const pre = document.createElement("pre");
      pre.textContent = JSON.stringify({
        request_id: item.request_id,
        client_event_id: item.client_event_id,
        client_occurred_at: item.client_occurred_at,
        details: item.details,
        error_message: item.error_message,
      }, null, 2);
      detail.append(summary, pre);
      details.replaceChildren(detail);
      root.appendChild(row);
    }
  }

  async function load() {
    if (!$("view-client-logs")?.classList.contains("active")) return;
    try {
      const { params } = filters();
      const response = await api(endpoint + "?" + params.toString());
      const payload = await response.json();
      const rows = Array.isArray(payload.data) ? payload.data : [];
      renderRows(rows);
      setStatus("已加载 " + rows.length + " 条；服务端保留 " + Number(payload.retained || 0) + " 条。");
    } catch (error) {
      setStatus("读取客户端日志失败：" + error.message, true);
    }
  }

  async function exportBundle(mode) {
    try {
      const { params, timeRange } = filters();
      if (mode === "count" && timeRange) {
        params.delete("since");
        params.delete("until");
        params.set("limit", $("webChatClientLogLimit")?.value || "500");
      } else if (mode === "range" && !timeRange) {
        throw new Error("请先填写开始时间和结束时间。");
      } else if (mode === "range") {
        params.delete("limit");
      }
      const response = await api(endpoint + "/export?" + params.toString());
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "chat2api-web-chat-diagnostics.zip";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus("诊断日志包已生成，包含 " + Math.round(blob.size / 1024) + " KB。");
    } catch (error) {
      setStatus("导出失败：" + error.message, true);
    }
  }

  $("webChatClientLogLoad")?.addEventListener("click", () => { void load(); });
  $("webChatClientLogExportCount")?.addEventListener("click", () => { void exportBundle("count"); });
  $("webChatClientLogExportRange")?.addEventListener("click", () => { void exportBundle("range"); });
  window.Chat2APIWebChatClientLogsV1 = { load };
  window.addEventListener("DOMContentLoaded", () => {
    if (location.hash === "#client-logs" || $("view-client-logs")?.classList.contains("active")) void load();
  });
  window.addEventListener("hashchange", () => {
    if (location.hash === "#client-logs") void load();
  });
  window.addEventListener("chat2api-admin-session-changed", () => {
    if (location.hash === "#client-logs") void load();
  });
})();
