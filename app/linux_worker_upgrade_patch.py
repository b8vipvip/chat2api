from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from . import linux_worker_patch as worker_control
from .admin_auth import SESSION_COOKIE
from .linux_workers import iso, utcnow
from .runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION


PATCH_VERSION = "0.22.33"
TARGET_AGENT_VERSION = "0.3.7"
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

    # Never write an unvalidated sudoers fragment directly into /etc/sudoers.d.
    # The bootstrap is assembled by several response-layer patches; validating a
    # temporary file first prevents any future transformation bug from breaking
    # sudo globally on the Worker host. A successful run atomically replaces a
    # previously malformed chat2api-worker fragment as part of repair/upgrade.
    direct_header = "cat >/etc/sudoers.d/chat2api-worker <<'SUDO'"
    temp_header = 'SUDOERS_TMP="$(mktemp)"\ncat >"$SUDOERS_TMP" <<\'SUDO\''
    if direct_header in text and "SUDOERS_TMP=\"$(mktemp)\"" not in text:
        text = text.replace(direct_header, temp_header, 1)
        direct_footer = "SUDO\nchmod 440 /etc/sudoers.d/chat2api-worker\nvisudo -cf /etc/sudoers.d/chat2api-worker"
        temp_footer = (
            'SUDO\nchmod 440 "$SUDOERS_TMP"\nvisudo -cf "$SUDOERS_TMP"\n'
            'install -o root -g root -m 440 "$SUDOERS_TMP" /etc/sudoers.d/chat2api-worker\n'
            'rm -f "$SUDOERS_TMP"'
        )
        if direct_footer in text:
            text = text.replace(direct_footer, temp_footer, 1)

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
        with store._lock:
            item = worker(worker_id)
            metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
            previous = metadata.get("worker_upgrade") if isinstance(metadata.get("worker_upgrade"), dict) else {}
            history = [] if reset else list(previous.get("history") or [])[-79:]
            entry = {
                "at": now,
                "state": state,
                "stage": stage,
                "percent": max(0, min(int(percent), 100)),
                "message": str(message or "")[:700],
            }
            history.append(entry)
            payload = {
                "state": state,
                "stage": stage,
                "percent": entry["percent"],
                "message": entry["message"],
                "started_at": now if reset or not previous.get("started_at") else str(previous.get("started_at")),
                "updated_at": now,
                "completed_at": now if state in TERMINAL_STATES else "",
                "target_server_runtime": SERVER_RUNTIME_VERSION,
                "target_agent_version": TARGET_AGENT_VERSION,
                "target_chrome_bridge_version": CHROME_BRIDGE_BUNDLE_VERSION,
                "history": history[-80:],
            }
            metadata["worker_upgrade"] = payload
            item["metadata"] = metadata
            store._save()
            return dict(payload)

    @app.get(ASSET_PATH, include_in_schema=False)
    async def worker_upgrade_asset() -> Response:
        path = Path(__file__).with_name("admin_linux_worker_upgrade_v44.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.get(BOOTSTRAP_PATH, include_in_schema=False)
    async def worker_bootstrap_script() -> Response:
        path = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_linux_worker.sh"
        text = _patch_bootstrap(path.read_text(encoding="utf-8"))
        return Response(
            text,
            media_type="text/x-shellscript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.post("/api/admin/linux-workers/{worker_id}/upgrade")
    async def upgrade_worker(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        item = worker(worker_id)
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        previous = metadata.get("worker_upgrade") if isinstance(metadata.get("worker_upgrade"), dict) else {}
        if str(previous.get("state") or "") in {"queued", "running"}:
            return {
                "scheduled": True,
                "already_running": True,
                "upgrade": dict(previous),
                "target_agent_version": TARGET_AGENT_VERSION,
                "target_chrome_bridge_version": CHROME_BRIDGE_BUNDLE_VERSION,
            }

        write_state(
            worker_id,
            state="queued",
            stage="queued",
            message="已提交 Worker 在线更新任务",
            percent=0,
            reset=True,
        )
        send = getattr(app.state, "send_linux_worker_command", None)
        if not callable(send):
            write_state(
                worker_id,
                state="failed",
                stage="schedule",
                message="Worker 控制通道不可用",
                percent=100,
            )
            raise HTTPException(503, "Worker command channel is unavailable")
        try:
            command = await send(worker_id, "upgrade_worker", {}, wait=True, timeout=20)
        except HTTPException as exc:
            message = str(exc.detail or "Worker 更新命令发送失败")[:500]
            write_state(
                worker_id,
                state="failed",
                stage="schedule",
                message=message,
                percent=100,
            )
            raise
        result = command.get("result") if isinstance(command.get("result"), dict) else {}
        if not result.get("ok"):
            error = str(result.get("error") or "upgrade_schedule_failed")[:200]
            write_state(
                worker_id,
                state="failed",
                stage="schedule",
                message=f"Worker 无法启动在线更新：{error}",
                percent=100,
            )
            raise HTTPException(422, f"Worker upgrade could not start: {error}")
        payload = write_state(
            worker_id,
            state="queued",
            stage="scheduled",
            message="Worker 已接受在线更新任务，等待 root updater 接管",
            percent=1,
        )
        return {
            "scheduled": True,
            "already_running": bool(result.get("already_running")),
            "unit": str(result.get("unit") or "")[:160],
            "upgrade": payload,
            "target_agent_version": TARGET_AGENT_VERSION,
            "target_chrome_bridge_version": CHROME_BRIDGE_BUNDLE_VERSION,
        }

    @app.get("/api/admin/linux-workers/{worker_id}/upgrade")
    async def worker_upgrade_status(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        item = worker(worker_id)
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        upgrade = metadata.get("worker_upgrade") if isinstance(metadata.get("worker_upgrade"), dict) else {}
        return {
            "worker_id": worker_id,
            "upgrade": dict(upgrade),
            "agent_version": str(item.get("agent_version") or ""),
            "chrome_bridge_version": str(item.get("chrome_bridge_version") or ""),
            "target_agent_version": TARGET_AGENT_VERSION,
            "target_chrome_bridge_version": CHROME_BRIDGE_BUNDLE_VERSION,
        }

    @app.post("/api/workers/{worker_id}/upgrade-progress")
    async def worker_upgrade_progress(worker_id: str, request: Request) -> dict[str, Any]:
        item = worker(worker_id)
        worker_id_header = str(request.headers.get("X-Worker-ID") or "")
        worker_token = str(request.headers.get("X-Worker-Token") or "")
        if worker_id_header != worker_id or not worker_token or not store.authenticate(worker_id, worker_token):
            raise HTTPException(401, "Invalid Worker credentials")
        body = await request.json()
        state = str(body.get("state") or "running").lower()
        if state not in {"queued", "running", "succeeded", "failed", "unsupported"}:
            state = "running"
        stage = str(body.get("stage") or "running")[:120]
        message = str(body.get("message") or "")[:700]
        try:
            percent = max(0, min(int(body.get("percent") or 0), 100))
        except (TypeError, ValueError):
            percent = 0
        payload = write_state(
            worker_id,
            state=state,
            stage=stage,
            message=message,
            percent=percent,
        )
        return {"ok": True, "upgrade": payload}

    @app.middleware("http")
    async def linux_worker_upgrade_console(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
            return response
        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        marker = f'<script src="{ASSET_PATH}"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
