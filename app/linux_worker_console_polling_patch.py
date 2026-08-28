from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE


PATCH_VERSION = "0.22.31"
BASE_ASSET = "/assets/chat2api-linux-workers.js"
STABLE_ASSET = "/assets/chat2api-linux-worker-stable-table-v22-19.js"
SNAPSHOT_ENDPOINT = "/api/admin/linux-worker-console-snapshot"


async def _response_bytes(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def _patch_base_asset(text: str) -> str:
    load_pattern = re.compile(
        r'  let latestRows = \[\];\n'
        r'  const load = async \(\) => \{\n'
        r'.*?'
        r'\n  \};\n\n  const setProxyBusy',
        re.DOTALL,
    )
    replacement = r'''  let latestRows = [];
  let loadInFlight = null;
  let lastTableHtml = "";
  let lastWorkerRowsSource = "primary";

  const xhrJson = (path, timeoutMs) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("GET", path, true);
    xhr.withCredentials = true;
    xhr.timeout = timeoutMs;
    xhr.setRequestHeader("Accept", "application/json");
    const fail = message => reject(new Error(message));
    xhr.onload = () => {
      let payload = {};
      try { payload = xhr.responseText ? JSON.parse(xhr.responseText) : {}; }
      catch (_) { fail(`Worker 状态响应无法解析（HTTP ${xhr.status || 0}）`); return; }
      if (xhr.status < 200 || xhr.status >= 300) {
        fail(typeof payload.detail === "string" ? payload.detail : `HTTP ${xhr.status}`);
        return;
      }
      resolve(payload);
    };
    xhr.onerror = () => fail("Worker 状态连接失败");
    xhr.onabort = () => fail("Worker 状态请求已中止");
    xhr.ontimeout = () => fail(`Worker 状态请求超过 ${Math.round(timeoutMs / 1000)} 秒`);
    xhr.send();
  });

  const requestWorkerRows = async () => {
    try {
      const payload = await xhrJson("/api/admin/linux-worker-installations", 4500);
      lastWorkerRowsSource = "primary";
      return payload;
    } catch (primaryError) {
      try {
        const payload = await xhrJson("/api/admin/linux-worker-console-snapshot", 2500);
        lastWorkerRowsSource = "snapshot";
        payload.console_fallback_reason = primaryError.message;
        return payload;
      } catch (fallbackError) {
        throw new Error(`${primaryError.message}；备用状态接口失败：${fallbackError.message}`);
      }
    }
  };

  const workerHeartbeatAge = row => {
    const value = Date.parse(String(row?.last_seen_at || ""));
    return Number.isFinite(value) ? Math.max(0, Date.now() - value) : null;
  };
  const publishRows = () => {
    globalThis.__CHAT2API_LINUX_WORKER_ROWS__ = latestRows;
    const workers = latestRows.filter(row => row?.worker_id);
    const online = workers.filter(row => {
      const age = workerHeartbeatAge(row);
      return age !== null && age <= 45000 && !row.revoked_at;
    }).length;
    const summary = document.getElementById("linuxWorkerLiveSummary");
    if (summary) {
      if (!workers.length) {
        summary.textContent = "当前没有已注册 Linux Worker";
      } else {
        const latest = workers.map(workerHeartbeatAge).filter(value => value !== null).sort((a,b) => a-b)[0];
        const latestText = Number.isFinite(latest) ? (latest < 60000 ? `${Math.max(0, Math.round(latest / 1000))} 秒前` : `${Math.round(latest / 60000)} 分钟前`) : "无心跳";
        const sourceText = lastWorkerRowsSource === "snapshot" ? " · 列表接口异常，已切换备用状态" : "";
        summary.textContent = `Worker：${workers.length} · 在线：${online} · 离线：${workers.length - online} · 最近心跳：${latestText}${sourceText}`;
      }
    }
    globalThis.dispatchEvent(new CustomEvent("chat2api:linux-worker-rows", {detail:{rows:latestRows,source:lastWorkerRowsSource}}));
  };
  const renderRows = () => {
    const html = latestRows.map(row => `<tr><td>${esc(row.name)}</td><td>${esc(installLabel(row))}${row.record_type === "installation" ? `<div style="font-size:11px;color:#94a3b8">命令：${row.install_enabled ? "启用" : "停用"}</div>` : ""}</td><td>${progressHtml(row)}</td><td>${commandHtml(row)}</td><td>${esc(row.os_version || row.hostname || "-")}</td><td>${esc(networkLabel(row))}</td><td>${esc(proxyLabel(row))}</td><td>${esc(chatgptLabel(row))}</td><td>${esc(row.chrome_bridge_version || "-")}</td><td>${esc(reserveLabel(row))}</td><td>${esc(row.install_updated_at || row.last_seen_at || row.created_at || "-")}</td><td>${actionsHtml(row)}</td></tr>`).join("") || '<tr><td colspan="12">暂无 Worker</td></tr>';
    if (html === lastTableHtml) return;
    lastTableHtml = html;
    document.getElementById("linuxWorkerRows").innerHTML = html;
  };
  const load = async () => {
    if (loadInFlight) return loadInFlight;
    loadInFlight = (async () => {
      const summary = document.getElementById("linuxWorkerLiveSummary");
      try {
        const payload = await requestWorkerRows();
        latestRows = Array.isArray(payload.data) ? payload.data : [];
        publishRows();
        renderRows();
      } catch (error) {
        if (summary) summary.textContent = `Worker 状态读取失败：${error.message}`;
        const html = `<tr><td colspan="12">${esc(error.message)}</td></tr>`;
        if (html !== lastTableHtml) {
          lastTableHtml = html;
          document.getElementById("linuxWorkerRows").innerHTML = html;
        }
      }
    })();
    try {
      return await loadInFlight;
    } finally {
      loadInFlight = null;
    }
  };
  globalThis.__CHAT2API_LINUX_WORKER_REFRESH__ = load;

  const setProxyBusy'''
    text, count = load_pattern.subn(replacement, text, count=1)
    if count != 1:
        return text

    text = text.replace(
        '<div style="margin:10px 0;color:#94a3b8;font-size:12px;line-height:1.6">生成安装命令后会立即出现在 Worker 列表。',
        '<div id="linuxWorkerLiveSummary" style="margin:10px 0 4px;color:#cbd5e1;font-size:12px;line-height:1.6">正在读取 Worker 心跳…</div><div style="margin:4px 0 10px;color:#94a3b8;font-size:12px;line-height:1.6">生成安装命令后会立即出现在 Worker 列表。',
        1,
    )
    text = text.replace(
        'setInterval(() => { if(section.classList.contains("active")&&!proxyDialog.open&&!loginDialog.open) load(); }, 1000);',
        'setInterval(() => { if(section.classList.contains("active")&&!proxyDialog.open&&!loginDialog.open) load(); }, 2500);',
        1,
    )
    return text


def _patch_stable_asset(text: str) -> str:
    refresh_pattern = re.compile(
        r'    const refreshRows = async \(\) => \{\n'
        r'.*?'
        r'\n    \};\n\n    new MutationObserver',
        re.DOTALL,
    )
    replacement = r'''    const refreshRows = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const sharedRefresh = globalThis.__CHAT2API_LINUX_WORKER_REFRESH__;
        if (typeof sharedRefresh === "function") await sharedRefresh();
        const sharedRows = globalThis.__CHAT2API_LINUX_WORKER_ROWS__;
        if (!Array.isArray(sharedRows)) return;
        rows = sharedRows;
        paint();
      } catch (_) {
      } finally {
        refreshing = false;
      }
    };

    new MutationObserver'''
    text, count = refresh_pattern.subn(replacement, text, count=1)
    if count != 1:
        return text

    old = '''    document.getElementById("refreshLinuxWorkers")?.addEventListener("click", () => setTimeout(refreshRows, 80));
    setInterval(() => { if (section.classList.contains("active") && !pairingDialog.open) refreshRows(); }, 1500);
    refreshRows();'''
    new = '''    globalThis.addEventListener("chat2api:linux-worker-rows", event => {
      const sharedRows = event?.detail?.rows;
      if (!Array.isArray(sharedRows)) return;
      rows = sharedRows;
      paint();
    });
    document.getElementById("refreshLinuxWorkers")?.addEventListener("click", () => setTimeout(refreshRows, 80));
    setInterval(() => { if (section.classList.contains("active") && !pairingDialog.open) paint(); }, 5000);
    if (section.classList.contains("active")) refreshRows();'''
    return text.replace(old, new, 1)


def _fallback_rows(app: FastAPI) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    workers = getattr(app.state, "linux_workers", None)
    if workers is None:
        return rows
    for worker in workers.list_public():
        rows.append({
            **worker,
            "record_type": "worker",
            "install_state": "legacy",
            "install_enabled": False,
        })
    return rows


def install_linux_worker_console_polling_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_console_polling_patch_installed", False):
        return app
    app.state.linux_worker_console_polling_patch_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    @app.get(SNAPSHOT_ENDPOINT)
    async def linux_worker_console_snapshot(request: Request) -> dict[str, Any]:
        admin(request)
        rows = _fallback_rows(app)
        return {
            "object": "chat2api.linux-worker-console-snapshot",
            "version": PATCH_VERSION,
            "data": rows,
            "row_count": len(rows),
        }

    @app.middleware("http")
    async def linux_worker_console_polling(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path not in {BASE_ASSET, STABLE_ASSET}:
            return response

        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        text = _patch_base_asset(text) if path == BASE_ASSET else _patch_stable_asset(text)

        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(
            text,
            status_code=response.status_code,
            media_type="application/javascript",
            headers=headers,
        )

    return app
