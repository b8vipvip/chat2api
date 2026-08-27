from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from . import linux_worker_patch as worker_control
from .admin_auth import SESSION_COOKIE
from .linux_workers import iso, utcnow
from .runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION


PATCH_VERSION = "0.22.25"
TARGET_AGENT_VERSION = "0.3.6"
ASSET_PATH = "/assets/chat2api-linux-worker-upgrade-v44.js"
BOOTSTRAP_PATH = "/bootstrap/linux-worker.sh"
TERMINAL_STATES = frozenset({"succeeded", "failed", "unsupported"})


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
    # v44 keeps the v43 initialization wrapper and adds the online updater.
    text = text.replace(
        "${WORKER_DIR}/scripts/linux_worker_agent_v43.py",
        "${WORKER_DIR}/scripts/linux_worker_agent_v44.py",
    )

    initialize_anchor = (
        'install -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_initialize.sh" '
        "/usr/local/sbin/chat2api-worker-initialize"
    )
    proxy_anchor = (
        'install -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_proxy_apply.sh" '
        "/usr/local/sbin/chat2api-worker-proxy-apply"
    )
    upgrade_install = (
        'install -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_upgrade.sh" '
        "/usr/local/sbin/chat2api-worker-upgrade"
    )
    if upgrade_install not in text:
        anchor = initialize_anchor if initialize_anchor in text else proxy_anchor
        if anchor in text:
            text = text.replace(anchor, anchor + "\n" + upgrade_install, 1)

    patched_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("rm -f /usr/local/sbin/chat2api-linux-worker-watchdog") and "/usr/local/sbin/chat2api-worker-upgrade" not in line:
            line += " /usr/local/sbin/chat2api-worker-upgrade"
        if line.startswith("chat2api ALL=(root) NOPASSWD:") and "/usr/local/sbin/chat2api-worker-upgrade" not in line:
            line += ", /usr/local/sbin/chat2api-worker-upgrade"
        patched_lines.append(line)
    text = "\n".join(patched_lines) + ("\n" if text.endswith("\n") else "")

    bridge_line = 'echo "Chrome Bridge: $(jq -r .version "$WORKER_DIR/chrome_extension/manifest.json") (自动加载 / 自动配对，无需手工配对码)"'
    agent_line = f'echo "Worker Agent: {TARGET_AGENT_VERSION} (支持后台一键更新 / 实时进度)"'
    if bridge_line in text and agent_line not in text:
        text = text.replace(bridge_line, agent_line + "\n" + bridge_line, 1)
    return text


def install_linux_worker_upgrade_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_upgrade_patch_installed", False):
        return app
    app.state.linux_worker_upgrade_patch_installed = True

    store = app.state.linux_workers
    worker_control.ALLOWED_COMMANDS = frozenset(set(worker_control.ALLOWED_COMMANDS) | {"upgrade_worker"})

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    def worker(worker_id: str) -> dict[str, Any]:
        item = store.data["workers"].get(worker_id)
        if not item:
            raise HTTPException(404, "Worker not found")
        if item.get("revoked_at"):
            raise HTTPException(409, "Worker is revoked")
        return item

    def write_state(
        worker_id: str,
        *,
        state: str,
        stage: str,
        message: str,
        percent: int,
        reset: bool = False,
    ) -> dict[str, Any]:
        now = iso(utcnow())
        safe_state = str(state or "running")[:32]
        safe_stage = str(stage or "running")[:80]
        safe_message = str(message or "").replace("\r", " ").replace("\n", " ").strip()[:700]
        safe_percent = max(0, min(int(percent or 0), 100))
        with store._lock:
            item = store.data["workers"].get(worker_id)
            if not item:
                raise KeyError(worker_id)
            metadata = dict(item.get("metadata") or {})
            previous = metadata.get("worker_upgrade") if isinstance(metadata.get("worker_upgrade"), dict) else {}
            history = [] if reset else list(previous.get("history") or [])[-79:]
            last = history[-1] if history else {}
            if last.get("stage") != safe_stage or last.get("message") != safe_message or last.get("state") != safe_state:
                history.append({
                    "at": now,
                    "state": safe_state,
                    "stage": safe_stage,
                    "percent": safe_percent,
                    "message": safe_message,
                })
            started_at = now if reset or not previous.get("started_at") else str(previous.get("started_at"))
            current = {
                "state": safe_state,
                "stage": safe_stage,
                "percent": safe_percent,
                "message": safe_message,
                "started_at": started_at,
                "updated_at": now,
                "completed_at": now if safe_state in TERMINAL_STATES else "",
                "target_server_runtime": SERVER_RUNTIME_VERSION,
                "target_agent_version": TARGET_AGENT_VERSION,
                "target_chrome_bridge_version": CHROME_BRIDGE_BUNDLE_VERSION,
                "history": history[-80:],
            }
            metadata["worker_upgrade"] = current
            item["metadata"] = metadata
            store._save()
            return dict(current)

    def public_status(worker_id: str) -> dict[str, Any]:
        item = worker(worker_id)
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        upgrade = metadata.get("worker_upgrade") if isinstance(metadata.get("worker_upgrade"), dict) else {}
        return {
            "worker_id": worker_id,
            "name": str(item.get("name") or item.get("hostname") or worker_id),
            "online": worker_id in app.state.worker_sockets,
            "current": {
                "agent_version": str(item.get("agent_version") or ""),
                "chrome_bridge_version": str(item.get("chrome_bridge_version") or ""),
            },
            "target": {
                "server_runtime": SERVER_RUNTIME_VERSION,
                "agent_version": TARGET_AGENT_VERSION,
                "chrome_bridge_version": CHROME_BRIDGE_BUNDLE_VERSION,
            },
            "upgrade": dict(upgrade),
        }

    @app.post("/api/workers/{worker_id}/upgrade-progress")
    async def worker_upgrade_progress(worker_id: str, request: Request) -> dict[str, bool]:
        header_id = str(request.headers.get("x-worker-id") or "")
        token = str(request.headers.get("x-worker-token") or "")
        if header_id != worker_id or not store.authenticate(worker_id, token):
            raise HTTPException(401, "Worker authentication required")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Upgrade progress must be an object")
        write_state(
            worker_id,
            state=str(body.get("state") or "running"),
            stage=str(body.get("stage") or "running"),
            message=str(body.get("message") or ""),
            percent=int(body.get("percent") or 0),
        )
        return {"ok": True}

    @app.get("/api/admin/linux-workers/{worker_id}/upgrade-status")
    async def admin_upgrade_status(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        return public_status(worker_id)

    @app.post("/api/admin/linux-workers/{worker_id}/upgrade")
    async def admin_upgrade_worker(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        worker(worker_id)
        write_state(
            worker_id,
            state="queued",
            stage="queued",
            message="中心服务器已接收更新请求，正在通知 Worker",
            percent=1,
            reset=True,
        )
        try:
            command = await app.state.send_linux_worker_command(
                worker_id,
                "upgrade_worker",
                {},
                wait=True,
                timeout=20,
            )
        except HTTPException as exc:
            write_state(
                worker_id,
                state="failed",
                stage="control-plane",
                message=str(exc.detail),
                percent=1,
            )
            raise

        result = command.get("result") if isinstance(command, dict) else None
        if isinstance(result, dict) and result.get("ok"):
            write_state(
                worker_id,
                state="running",
                stage="scheduled",
                message="Worker 已接受在线更新任务",
                percent=2,
            )
            return {
                "accepted": True,
                "scheduled": bool(result.get("scheduled")),
                "unit": str(result.get("unit") or "")[:160],
                **public_status(worker_id),
            }

        error = str((result or {}).get("error") or "") if isinstance(result, dict) else "unknown_error"
        if error in {"command_not_allowed", "not_implemented", "upgrade_helper_missing"}:
            server = app.state.settings.resolved_public_url(str(request.base_url)).rstrip("/")
            command_text = f"curl -fsSL {server}/bootstrap/linux-worker.sh | sudo bash -s -- --server {server} --upgrade"
            write_state(
                worker_id,
                state="unsupported",
                stage="one-time-enable",
                message="当前 Worker Agent 尚未安装在线更新能力，需要最后执行一次幂等升级来启用更新按钮",
                percent=0,
            )
            return {
                "accepted": False,
                "needs_bootstrap_once": True,
                "bootstrap_command": command_text,
                "error": error,
                **public_status(worker_id),
            }

        write_state(
            worker_id,
            state="failed",
            stage="schedule",
            message=f"Worker 无法启动在线更新：{error}",
            percent=1,
        )
        raise HTTPException(502, f"Worker 无法启动在线更新：{error}")

    @app.get(ASSET_PATH, include_in_schema=False)
    async def linux_worker_upgrade_asset() -> Response:
        source = Path(__file__).with_name("admin_linux_worker_upgrade_v44.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def linux_worker_upgrade_runtime(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == BOOTSTRAP_PATH and "text" in response.headers.get("content-type", ""):
            raw = await _response_bytes(response)
            text = _patch_bootstrap(raw.decode("utf-8", errors="replace"))
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/x-shellscript", headers=headers)

        if path == "/admin" and "text/html" in response.headers.get("content-type", ""):
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = f'<script src="{ASSET_PATH}"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)
        return response

    return app
