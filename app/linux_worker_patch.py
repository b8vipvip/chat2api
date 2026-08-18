from __future__ import annotations

import secrets
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE
from .linux_workers import ALLOWED_COMMANDS, LinuxWorkerStore


PATCH_VERSION = "0.22.0"


def install_linux_worker_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_control_plane_installed", False):
        return app
    store = LinuxWorkerStore(app.state.settings.data_dir)
    app.state.linux_workers = store
    app.state.worker_sockets = {}
    app.state.linux_worker_control_plane_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

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
        if command not in ALLOWED_COMMANDS:
            raise HTTPException(400, "Command is not in the worker allowlist")
        socket = app.state.worker_sockets.get(worker_id)
        if not socket:
            raise HTTPException(409, "Worker is offline")
        request_id = "cmd_" + uuid.uuid4().hex
        await socket.send_json({"type": "command", "request_id": request_id, "command": command, "arguments": dict(body.get("arguments") or {})})
        return {"accepted": True, "request_id": request_id}

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
                if message.get("type") == "heartbeat":
                    await websocket.send_json({"type": "heartbeat.ack", "worker": store.heartbeat(worker_id, message.get("data") or {})})
        except WebSocketDisconnect:
            pass
        finally:
            if app.state.worker_sockets.get(worker_id) is websocket:
                app.state.worker_sockets.pop(worker_id, None)

    @app.get("/bootstrap/linux-worker.sh", include_in_schema=False)
    async def bootstrap_script() -> Response:
        return Response(Path(__file__).parents[1].joinpath("scripts/bootstrap_linux_worker.sh").read_text(), media_type="text/x-shellscript", headers={"Cache-Control": "public, max-age=300"})

    return app
