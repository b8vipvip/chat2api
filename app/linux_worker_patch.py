from __future__ import annotations

import asyncio
import secrets
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE
from .linux_workers import ALLOWED_COMMANDS, LinuxWorkerStore


PATCH_VERSION = "0.22.1"
PROXY_SCHEMES = frozenset({"vless", "vmess", "trojan", "ss"})
MAX_PROXY_LINK_LENGTH = 16384


def install_linux_worker_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_control_plane_installed", False):
        return app
    store = LinuxWorkerStore(app.state.settings.data_dir)
    app.state.linux_workers = store
    app.state.worker_sockets = {}
    app.state.worker_command_waiters = {}
    app.state.linux_worker_control_plane_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    def worker_exists(worker_id: str) -> None:
        worker = store.data["workers"].get(worker_id)
        if not worker:
            raise HTTPException(404, "Worker not found")
        if worker.get("revoked_at"):
            raise HTTPException(409, "Worker is revoked")

    async def send_worker_command(
        worker_id: str,
        command: str,
        arguments: dict[str, Any],
        *,
        wait: bool,
        timeout: float = 75.0,
    ) -> dict[str, Any]:
        if command not in ALLOWED_COMMANDS:
            raise HTTPException(400, "Command is not in the worker allowlist")
        worker_exists(worker_id)
        socket = app.state.worker_sockets.get(worker_id)
        if not socket:
            raise HTTPException(409, "Worker is offline")
        request_id = "cmd_" + uuid.uuid4().hex
        if not wait:
            await socket.send_json({"type": "command", "request_id": request_id, "command": command, "arguments": arguments})
            return {"accepted": True, "request_id": request_id}

        future = asyncio.get_running_loop().create_future()
        app.state.worker_command_waiters[request_id] = (worker_id, future)
        try:
            await socket.send_json({"type": "command", "request_id": request_id, "command": command, "arguments": arguments})
            try:
                result = await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise HTTPException(504, "Worker command timed out") from exc
            if not isinstance(result, dict):
                raise HTTPException(502, "Worker returned an invalid command result")
            return {"accepted": True, "request_id": request_id, "result": result}
        finally:
            app.state.worker_command_waiters.pop(request_id, None)

    @app.post("/api/admin/linux-workers/enrollments")
    async def create_enrollment(request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        enrollment = store.create_enrollment(body.get("name", "Linux Worker"), int(body.get("ttl_minutes", 30)))
        server = app.state.settings.resolved_public_url(str(request.base_url))
        enrollment["install_command"] = f"curl -fsSL {server}/bootstrap/linux-worker.sh | sudo bash -s -- --server {server} --enroll-code {enrollment['code']}"
        return enrollment

    @app.get("/api/admin/linux-workers")
    async def list_workers(request: Request) -> dict[str, Any]:
        admin(request)
        return {"data": store.list_public(), "version": PATCH_VERSION}

    @app.delete("/api/admin/linux-workers/{worker_id}")
    async def revoke_worker(worker_id: str, request: Request) -> dict[str, bool]:
        admin(request)
        if worker_id not in store.data["workers"]:
            raise HTTPException(404, "Worker not found")
        store.revoke(worker_id)
        socket = app.state.worker_sockets.pop(worker_id, None)
        if socket:
            await socket.close(code=4003, reason="Worker revoked")
        return {"revoked": True}

    @app.post("/api/admin/linux-workers/{worker_id}/commands")
    async def worker_command(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        command = str(body.get("command") or "")
        arguments = body.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise HTTPException(400, "Command arguments must be an object")
        return await send_worker_command(
            worker_id,
            command,
            dict(arguments),
            wait=bool(body.get("wait")),
            timeout=min(max(float(body.get("timeout_seconds") or 75), 1), 120),
        )

    @app.post("/api/admin/linux-workers/{worker_id}/proxy")
    async def apply_worker_proxy(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        share_link = str(body.get("share_link") or "").strip()
        if not share_link or len(share_link) > MAX_PROXY_LINK_LENGTH or "\n" in share_link or "\r" in share_link:
            raise HTTPException(400, "Proxy share link is empty, too long, or not a single line")
        scheme = share_link.split(":", 1)[0].lower()
        if scheme not in PROXY_SCHEMES:
            raise HTTPException(400, "Supported proxy links: VLESS, VMess, Trojan, Shadowsocks")
        command = await send_worker_command(
            worker_id,
            "apply_proxy_config",
            {"share_link": share_link},
            wait=True,
            timeout=90,
        )
        result = command["result"]
        if not result.get("ok"):
            error = str(result.get("error") or "proxy_apply_failed")[:120]
            suffix = " (rolled back)" if result.get("rolled_back") else ""
            raise HTTPException(422, f"Proxy apply failed: {error}{suffix}")
        summary = result.get("proxy") if isinstance(result.get("proxy"), dict) else {}
        worker = store.record_proxy_success(worker_id, summary)
        return {
            "applied": True,
            "proxy": worker.get("metadata", {}).get("proxy_summary", {}),
            "test": result.get("test") if isinstance(result.get("test"), dict) else {},
        }

    @app.post("/api/admin/linux-workers/{worker_id}/proxy/test")
    async def test_worker_proxy(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        command = await send_worker_command(worker_id, "test_proxy", {}, wait=True, timeout=35)
        result = command["result"]
        return {
            "ok": bool(result.get("ok")),
            "http_status": str(result.get("http_status") or "")[:8],
            "error": None if result.get("ok") else str(result.get("error") or "proxy_test_failed")[:120],
        }

    @app.post("/api/workers/enroll")
    async def enroll_worker(request: Request) -> dict[str, str]:
        body = await request.json()
        try:
            credentials = store.enroll(body.get("enroll_code", ""), body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        base = app.state.settings.resolved_public_url(str(request.base_url))
        ws = ("wss://" if base.startswith("https://") else "ws://") + base.split("://", 1)[-1]
        return {**credentials, "websocket_url": ws.rstrip("/") + "/api/workers/connect"}

    @app.websocket("/api/workers/connect")
    async def worker_connect(websocket: WebSocket) -> None:
        worker_id = websocket.headers.get("x-worker-id", "")
        token = websocket.headers.get("x-worker-token", "")
        if not store.authenticate(worker_id, token):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        app.state.worker_sockets[worker_id] = websocket
        try:
            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                if message_type == "heartbeat":
                    await websocket.send_json({"type": "heartbeat.ack", "worker": store.heartbeat(worker_id, message.get("data") or {})})
                elif message_type == "command.result":
                    request_id = str(message.get("request_id") or "")
                    waiter = app.state.worker_command_waiters.get(request_id)
                    if waiter and waiter[0] == worker_id and not waiter[1].done():
                        waiter[1].set_result(message.get("result") or {})
        except WebSocketDisconnect:
            pass
        finally:
            if app.state.worker_sockets.get(worker_id) is websocket:
                app.state.worker_sockets.pop(worker_id, None)
            for request_id, waiter in list(app.state.worker_command_waiters.items()):
                if waiter[0] == worker_id and not waiter[1].done():
                    waiter[1].set_result({"ok": False, "error": "worker_disconnected"})

    @app.get("/bootstrap/linux-worker.sh", include_in_schema=False)
    async def bootstrap_script() -> Response:
        return Response(Path(__file__).parents[1].joinpath("scripts/bootstrap_linux_worker.sh").read_text(), media_type="text/x-shellscript", headers={"Cache-Control": "public, max-age=300"})

    return app
