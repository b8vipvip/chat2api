from __future__ import annotations

import asyncio
import secrets
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE
from .linux_worker_login_sessions import LOGIN_SESSION_IDLE_SECONDS, LoginSessionStore
from .linux_workers import ALLOWED_COMMANDS, LinuxWorkerStore


PATCH_VERSION = "0.22.2"
PROXY_SCHEMES = frozenset({"vless", "vmess", "trojan", "ss"})
MAX_PROXY_LINK_LENGTH = 16384
LOGIN_TICKET_HEADER = "x-chat2api-login-ticket"


def install_linux_worker_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_control_plane_installed", False):
        return app
    store = LinuxWorkerStore(app.state.settings.data_dir)
    login_sessions = LoginSessionStore()
    app.state.linux_workers = store
    app.state.worker_sockets = {}
    app.state.worker_command_waiters = {}
    app.state.worker_login_sessions = login_sessions
    app.state.linux_worker_control_plane_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    def worker_exists(worker_id: str) -> dict[str, Any]:
        worker = store.data["workers"].get(worker_id)
        if not worker:
            raise HTTPException(404, "Worker not found")
        if worker.get("revoked_at"):
            raise HTTPException(409, "Worker is revoked")
        return worker

    def require_login_ticket(worker_id: str, request: Request, *, touch: bool = True) -> str:
        ticket = str(request.headers.get(LOGIN_TICKET_HEADER) or "")
        if not ticket:
            raise HTTPException(403, "Remote login session ticket required")
        try:
            login_sessions.require(worker_id, ticket, touch=touch)
        except KeyError as exc:
            raise HTTPException(403, "Remote login session expired or invalid") from exc
        return ticket

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
        login_sessions.revoke(worker_id)
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

    @app.post("/api/admin/linux-workers/{worker_id}/login-session")
    async def open_worker_login_session(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        worker_exists(worker_id)
        command = await send_worker_command(worker_id, "open_login_session", {}, wait=True, timeout=15)
        result = command["result"]
        if not result.get("ok"):
            raise HTTPException(422, f"Remote login could not start: {str(result.get('error') or 'open_failed')[:120]}")
        ticket = login_sessions.issue(worker_id)
        return {
            "opened": True,
            "ticket": ticket,
            "idle_timeout_seconds": LOGIN_SESSION_IDLE_SECONDS,
            "source_width": int(result.get("source_width") or 1920),
            "source_height": int(result.get("source_height") or 1080),
        }

    @app.get("/api/admin/linux-workers/{worker_id}/login-session/frame")
    async def worker_login_frame(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        ticket = require_login_ticket(worker_id, request)
        worker = worker_exists(worker_id)
        if str(worker.get("chatgpt_status") or "").lower() in {"ready", "logged_in", "authenticated"}:
            login_sessions.revoke(worker_id, ticket)
            await send_worker_command(worker_id, "close_login_session", {}, wait=False)
            return {"ok": True, "complete": True, "chatgpt_status": worker.get("chatgpt_status")}
        command = await send_worker_command(worker_id, "login_session_frame", {}, wait=True, timeout=15)
        result = command["result"]
        if not result.get("ok"):
            raise HTTPException(422, f"Remote frame failed: {str(result.get('error') or 'frame_failed')[:120]}")
        frame = str(result.get("frame") or "")
        if not frame or len(frame) > 2_100_000:
            raise HTTPException(502, "Worker returned an invalid remote frame")
        return {
            "ok": True,
            "complete": False,
            "mime": str(result.get("mime") or "image/jpeg")[:32],
            "frame": frame,
            "source_width": int(result.get("source_width") or 1920),
            "source_height": int(result.get("source_height") or 1080),
            "frame_width": int(result.get("frame_width") or 1280),
            "frame_height": int(result.get("frame_height") or 720),
        }

    @app.post("/api/admin/linux-workers/{worker_id}/login-session/input")
    async def worker_login_input(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        require_login_ticket(worker_id, request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Remote input payload must be an object")
        kind = str(body.get("kind") or "")
        if kind not in {"mouse", "key"}:
            raise HTTPException(400, "Unsupported remote input kind")
        if kind == "key":
            body = {
                "kind": "key",
                "key": str(body.get("key") or "")[:32],
                "modifiers": [str(item)[:16] for item in list(body.get("modifiers") or [])[:4]],
            }
        else:
            body = {
                "kind": "mouse",
                "action": str(body.get("action") or "click")[:32],
                "x": body.get("x"),
                "y": body.get("y"),
                "button": body.get("button"),
                "delta": body.get("delta"),
            }
        command = await send_worker_command(worker_id, "login_session_input", body, wait=True, timeout=8)
        result = command["result"]
        if not result.get("ok"):
            raise HTTPException(422, f"Remote input failed: {str(result.get('error') or 'input_failed')[:120]}")
        return {"ok": True}

    @app.delete("/api/admin/linux-workers/{worker_id}/login-session")
    async def close_worker_login_session(worker_id: str, request: Request) -> dict[str, bool]:
        admin(request)
        ticket = require_login_ticket(worker_id, request, touch=False)
        login_sessions.revoke(worker_id, ticket)
        try:
            await send_worker_command(worker_id, "close_login_session", {}, wait=False)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
        return {"closed": True}

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
            login_sessions.revoke(worker_id)
            for request_id, waiter in list(app.state.worker_command_waiters.items()):
                if waiter[0] == worker_id and not waiter[1].done():
                    waiter[1].set_result({"ok": False, "error": "worker_disconnected"})

    @app.get("/bootstrap/linux-worker.sh", include_in_schema=False)
    async def bootstrap_script() -> Response:
        return Response(Path(__file__).parents[1].joinpath("scripts/bootstrap_linux_worker.sh").read_text(), media_type="text/x-shellscript", headers={"Cache-Control": "public, max-age=300"})

    return app
