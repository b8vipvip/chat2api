from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_VERSION = "0.22.22"
STABLE_TABLE_ASSET = "/assets/chat2api-linux-worker-stable-table-v22-19.js"
BOOTSTRAP_PATH = "/bootstrap/linux-worker.sh"


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


def _patch_bootstrap(text: str) -> str:
    proxy_install = 'install -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_proxy_apply.sh" /usr/local/sbin/chat2api-worker-proxy-apply'
    diagnostics_install = proxy_install + '\ninstall -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_diagnostics.sh" /usr/local/sbin/chat2api-worker-diagnostics'
    if "/usr/local/sbin/chat2api-worker-diagnostics" not in text and proxy_install in text:
        text = text.replace(proxy_install, diagnostics_install, 1)

    cleanup = "rm -f /usr/local/sbin/chat2api-linux-worker-watchdog /usr/local/sbin/chat2api-linux-extension-autoreload /usr/local/sbin/chat2api-worker-proxy-apply"
    cleanup_new = cleanup + " /usr/local/sbin/chat2api-worker-diagnostics"
    if cleanup in text and cleanup_new not in text:
        text = text.replace(cleanup, cleanup_new, 1)

    sudo_line = "chat2api ALL=(root) NOPASSWD: /bin/systemctl restart chat2api-chrome.service, /bin/systemctl restart chat2api-xray.service, /bin/systemctl restart chat2api-xvfb.service, /usr/local/sbin/chat2api-worker-proxy-apply"
    sudo_new = sudo_line + ", /usr/local/sbin/chat2api-worker-diagnostics"
    if sudo_line in text and sudo_new not in text:
        text = text.replace(sudo_line, sudo_new, 1)
    return text


def _patch_stable_table(text: str) -> str:
    marker = "data-worker-diagnostics-v2222"
    if marker in text:
        return text

    delete_block = '''        if (row.worker_id && !cells[11].querySelector("[data-worker-delete-v2219]")) {
          const remove = document.createElement("button");'''
    diagnostics_block = '''        if (row.worker_id && !cells[11].querySelector("[data-worker-diagnostics-v2222]")) {
          const diagnostics = document.createElement("button");
          diagnostics.className = "action";
          diagnostics.type = "button";
          diagnostics.textContent = "诊断日志";
          diagnostics.dataset.workerDiagnosticsV2222 = String(row.worker_id);
          diagnostics.dataset.workerName = String(row.name || row.hostname || row.worker_id);
          diagnostics.title = "下载该 Worker 最近 30 分钟的服务状态与运行日志";
          cells[11].appendChild(diagnostics);
        }
        if (row.worker_id && !cells[11].querySelector("[data-worker-delete-v2219]")) {
          const remove = document.createElement("button");'''
    if delete_block in text:
        text = text.replace(delete_block, diagnostics_block, 1)

    listener = '''    tbody.addEventListener("click", event => {
      const pairing = event.target.closest?.("[data-worker-pairing-v2219]");'''
    listener_new = '''    tbody.addEventListener("click", event => {
      const diagnostics = event.target.closest?.("[data-worker-diagnostics-v2222]");
      if (diagnostics) {
        event.preventDefault();
        event.stopImmediatePropagation();
        const workerId = String(diagnostics.dataset.workerDiagnosticsV2222 || "");
        if (!workerId) return;
        const original = diagnostics.textContent;
        diagnostics.disabled = true;
        diagnostics.textContent = "生成中…";
        request(`/api/admin/linux-workers/${encodeURIComponent(workerId)}/commands`, {
          method:"POST",
          body:JSON.stringify({command:"get_logs",arguments:{},wait:true,timeout_seconds:40}),
        }).then(payload => {
          const result = payload?.result || {};
          if (!result.ok) throw new Error(String(result.detail || result.error || "Worker 未返回诊断日志"));
          const logs = String(result.logs || "");
          if (!logs) throw new Error("Worker 返回的诊断日志为空");
          const filename = String(result.filename || `chat2api-worker-${workerId}-diagnostics.log`).replace(/[^0-9A-Za-z._-]/g, "_");
          const blob = new Blob([logs], {type:"text/plain;charset=utf-8"});
          const url = URL.createObjectURL(blob);
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = filename;
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1200);
          diagnostics.textContent = "已下载";
          diagnostics.title = result.truncated ? "日志过大，已保留最近内容后下载" : "最近 Worker 诊断日志已下载";
          setTimeout(() => {
            if (diagnostics.isConnected) {
              diagnostics.disabled = false;
              diagnostics.textContent = original;
            }
          }, 1600);
        }).catch(error => {
          diagnostics.disabled = false;
          diagnostics.textContent = original;
          alert(`诊断日志获取失败：${error.message}`);
        });
        return;
      }
      const pairing = event.target.closest?.("[data-worker-pairing-v2219]");'''
    if listener in text:
        text = text.replace(listener, listener_new, 1)
    return text


def install_linux_worker_diagnostics_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_diagnostics_patch_installed", False):
        return app
    app.state.linux_worker_diagnostics_patch_installed = True

    @app.middleware("http")
    async def linux_worker_diagnostics_ui(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path not in {BOOTSTRAP_PATH, STABLE_TABLE_ASSET}:
            return response

        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        if path == BOOTSTRAP_PATH:
            text = _patch_bootstrap(text)
            media_type = "text/x-shellscript"
        else:
            text = _patch_stable_table(text)
            media_type = "application/javascript"
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type=media_type, headers=headers)

    return app
