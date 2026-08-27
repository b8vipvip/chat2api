from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from . import linux_worker_patch as worker_control
from .admin_auth import SESSION_COOKIE


PATCH_VERSION = "0.22.24"
ASSET_PATH = "/assets/chat2api-linux-worker-initialize-v43.js"
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
    # Run the v43 Agent shim after an idempotent Worker repair/upgrade. The shim
    # keeps the existing Agent implementation and only adds initialize_worker.
    text = text.replace(
        "${WORKER_DIR}/scripts/linux_worker_agent.py",
        "${WORKER_DIR}/scripts/linux_worker_agent_v43.py",
    )

    old_autoreload = 'install -m 755 "$WORKER_DIR/scripts/linux_extension_autoreload.sh" /usr/local/sbin/chat2api-linux-extension-autoreload'
    new_autoreload = 'install -m 755 "$WORKER_DIR/scripts/linux_extension_autoreload_v43.sh" /usr/local/sbin/chat2api-linux-extension-autoreload'
    text = text.replace(old_autoreload, new_autoreload)

    anchor = 'install -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_proxy_apply.sh" /usr/local/sbin/chat2api-worker-proxy-apply'
    install_initialize = anchor + '\ninstall -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_initialize.sh" /usr/local/sbin/chat2api-worker-initialize'
    if "/usr/local/sbin/chat2api-worker-initialize" not in text and anchor in text:
        text = text.replace(anchor, install_initialize, 1)

    # Later diagnostics patch revisions may already have extended this cleanup
    # line. Append rather than replace a fixed historical string.
    cleanup_needle = "rm -f /usr/local/sbin/chat2api-linux-worker-watchdog /usr/local/sbin/chat2api-linux-extension-autoreload /usr/local/sbin/chat2api-worker-proxy-apply"
    if cleanup_needle in text and "/usr/local/sbin/chat2api-worker-initialize" not in text[text.find(cleanup_needle): text.find(cleanup_needle) + 400]:
        text = text.replace(cleanup_needle, cleanup_needle + " /usr/local/sbin/chat2api-worker-initialize", 1)

    sudo_marker = "chat2api ALL=(root) NOPASSWD: "
    for line in text.splitlines():
        if line.startswith(sudo_marker) and "/usr/local/sbin/chat2api-worker-initialize" not in line:
            text = text.replace(line, line + ", /usr/local/sbin/chat2api-worker-initialize", 1)
            break

    # A repair upgrade must also recover a profile that has already entered the
    # Chromium DidStartWorkerFail loop. Only Service Worker runtime state is
    # disposable; authenticated ChatGPT cookies/storage remain untouched.
    restart_anchor = "systemctl restart chat2api-chrome.service"
    reset = 'rm -rf "$PROFILE_DIR/Default/Service Worker" 2>/dev/null || true\n' + restart_anchor
    if restart_anchor in text and 'PROFILE_DIR/Default/Service Worker' not in text:
        text = text.replace(restart_anchor, reset, 1)
    return text


def install_linux_worker_initialize_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_initialize_patch_installed", False):
        return app
    app.state.linux_worker_initialize_patch_installed = True

    # send_worker_command resolves this module global at runtime, so extending
    # the fixed allowlist here keeps the older control-plane code unchanged.
    worker_control.ALLOWED_COMMANDS = frozenset(set(worker_control.ALLOWED_COMMANDS) | {"initialize_worker"})

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    async def send(worker_id: str, command: str, *, timeout: float = 35.0) -> dict[str, Any]:
        return await app.state.send_linux_worker_command(worker_id, command, {}, wait=True, timeout=timeout)

    @app.post("/api/admin/linux-workers/{worker_id}/initialize")
    async def initialize_worker(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        command = await send(worker_id, "initialize_worker", timeout=20)
        result = command.get("result") if isinstance(command, dict) else None
        if isinstance(result, dict) and result.get("ok"):
            return {
                "accepted": True,
                "mode": "full",
                "scheduled": bool(result.get("scheduled")),
                "unit": str(result.get("unit") or "")[:160],
                "message": "完整初始化已安排：将重启 Xray/Xvfb、Chrome、Worker Agent，并保留一个 ChatGPT 初始化窗口。",
            }

        error = str((result or {}).get("error") or "") if isinstance(result, dict) else ""
        if error not in {"command_not_allowed", "not_implemented", "initialize_helper_missing"}:
            raise HTTPException(502, f"Worker 初始化启动失败：{error or 'unknown_error'}")

        # Compatibility path for Workers that have not yet run the one-time v43
        # repair upgrade. It can recover the old browser/control services using
        # the existing allowlist, but the old Agent cannot safely restart itself.
        fallback: list[dict[str, Any]] = []
        for name in ("restart_xray", "restart_xvfb", "restart_chrome"):
            item = await send(worker_id, name, timeout=40)
            value = item.get("result") if isinstance(item, dict) else None
            fallback.append({"command": name, "ok": bool(isinstance(value, dict) and value.get("ok"))})
        return {
            "accepted": True,
            "mode": "compatibility",
            "needs_worker_upgrade": True,
            "fallback": fallback,
            "message": "当前 Worker Agent 还是旧版，已执行兼容恢复（Xray/Xvfb/Chrome）。请在该 Worker 原服务器运行一次修复安装后，初始化按钮即可同时重启 Agent 并执行 Service Worker 深度恢复。",
        }

    @app.get(ASSET_PATH, include_in_schema=False)
    async def linux_worker_initialize_asset() -> Response:
        source = Path(__file__).with_name("admin_linux_worker_initialize_v43.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def linux_worker_initialize_runtime(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == BOOTSTRAP_PATH and "text" in response.headers.get("content-type", ""):
            raw = await _response_bytes(response)
            text = _patch_bootstrap(raw.decode("utf-8", errors="replace"))
            headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/plain", headers=headers)

        if path == "/admin" and "text/html" in response.headers.get("content-type", ""):
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = f'<script src="{ASSET_PATH}"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)
        return response

    return app
