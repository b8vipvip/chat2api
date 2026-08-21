from __future__ import annotations

import io
import logging
import zipfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE


PATCH_VERSION = "0.22.22"
STABLE_TABLE_ASSET = "/assets/chat2api-linux-worker-stable-table-v22-19.js"
BOOTSTRAP_PATH = "/bootstrap/linux-worker.sh"
MAX_DIAGNOSTICS_BYTES = 50 * 1024 * 1024
logger = logging.getLogger(__name__)


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
    # The historical bootstrap source still carries the previous Agent label;
    # the served v0.22.22 installer deploys Agent 0.3.3 from the verified bundle.
    text = text.replace('agent_version:"0.3.2"', 'agent_version:"0.3.3"')

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

    # v0.22.20 inserts its copy-progress click handler before the pairing handler.
    # Anchor on the stable pairing line instead of on the beginning of the event
    # listener so this patch works regardless of how many newer handlers precede it.
    pairing_line = '''      const pairing = event.target.closest?.("[data-worker-pairing-v2219]");'''
    handler = '''      const diagnostics = event.target.closest?.("[data-worker-diagnostics-v2222]");
      if (diagnostics) {
        event.preventDefault();
        event.stopImmediatePropagation();
        const workerId = String(diagnostics.dataset.workerDiagnosticsV2222 || "");
        if (!workerId) return;
        const original = diagnostics.textContent;
        diagnostics.disabled = true;
        diagnostics.textContent = "生成中…";
        fetch(`/api/admin/linux-worker/${encodeURIComponent(workerId)}/diagnostics/logs`, {credentials:"same-origin",cache:"no-store"}).then(async response => {
          if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
          const blob = await response.blob();
          const filename = `chat2api-worker-${workerId}-diagnostics.zip`;
          const url = URL.createObjectURL(blob);
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = filename;
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1200);
          diagnostics.textContent = "已下载";
          diagnostics.title = "最近 Worker 诊断 ZIP 已下载";
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
    if pairing_line in text:
        text = text.replace(pairing_line, handler, 1)
    return text


def install_linux_worker_diagnostics_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_diagnostics_patch_installed", False):
        return app
    app.state.linux_worker_diagnostics_patch_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    @app.get("/api/admin/linux-worker/{worker_id}/diagnostics/logs")
    async def download_linux_worker_diagnostics(worker_id: str, request: Request) -> Response:
        admin(request)
        logger.info("[linux-worker] diagnostics requested worker_id=%s", worker_id)
        try:
            command = await app.state.send_linux_worker_command(worker_id, "get_logs", {}, wait=True, timeout=40)
            result = command.get("result") if isinstance(command, dict) else None
            if not isinstance(result, dict) or not result.get("ok"):
                raise HTTPException(502, str((result or {}).get("error") or "Worker diagnostics failed")[:160])
            raw = str(result.get("logs") or "").encode("utf-8")[-MAX_DIAGNOSTICS_BYTES:]
            if not raw:
                raise HTTPException(502, "Worker returned empty diagnostics")
            # The helper emits one redacted, bounded journal stream. Preserve it
            # intact and also provide named views expected by support tooling.
            categories = {
                "worker-runtime.log": ("chat2api-worker-agent",),
                "chrome.log": ("chat2api-chrome",),
                "xvfb.log": ("chat2api-xvfb",),
                "pairing.log": ("pairing", "worker-bind"),
                "extension-sync.log": ("extension", "bridge"),
            }
            text_log = raw.decode("utf-8", errors="replace")
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for filename, needles in categories.items():
                    lines = [line for line in text_log.splitlines() if any(needle in line.lower() for needle in needles)]
                    archive.writestr(filename, "\n".join(lines) + ("\n" if lines else "No matching entries in the bounded diagnostic window.\n"))
                archive.writestr("diagnostics-full.log", raw)
            payload = output.getvalue()
            logger.info("[linux-worker] diagnostics completed worker_id=%s bytes=%d", worker_id, len(payload))
            return Response(payload, media_type="application/zip", headers={
                "Content-Disposition": f'attachment; filename="chat2api-worker-{worker_id}-diagnostics.zip"',
                "Cache-Control": "no-store",
            })
        except HTTPException:
            logger.warning("[linux-worker] diagnostics failed worker_id=%s", worker_id, exc_info=True)
            raise
        except Exception as exc:
            logger.exception("[linux-worker] diagnostics failed worker_id=%s", worker_id)
            raise HTTPException(500, "Unable to collect Worker diagnostics") from exc

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
