from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_VERSION = "0.22.31"
BASE_ASSET = "/assets/chat2api-linux-workers.js"
STABLE_ASSET = "/assets/chat2api-linux-worker-stable-table-v22-19.js"


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
        summary.textContent = `Worker：${workers.length} · 在线：${online} · 离线：${workers.length - online} · 最近心跳：${latestText}`;
      }
    }
    globalThis.dispatchEvent(new CustomEvent("chat2api:linux-worker-rows", {detail:{rows:latestRows}}));
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
      try {
        const payload = await request("/api/admin/linux-worker-installations");
        latestRows = Array.isArray(payload.data) ? payload.data : [];
        publishRows();
        renderRows();
      } catch (error) {
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
        if (Array.isArray(sharedRows)) {
          rows = sharedRows;
          paint();
          return;
        }
        const payload = await request("/api/admin/linux-worker-installations");
        rows = Array.isArray(payload.data) ? payload.data : [];
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


def install_linux_worker_console_polling_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_console_polling_patch_installed", False):
        return app
    app.state.linux_worker_console_polling_patch_installed = True

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
