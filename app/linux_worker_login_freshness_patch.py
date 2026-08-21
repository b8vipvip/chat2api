from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request

from .admin_auth import SESSION_COOKIE
from .linux_worker_patch import LOGIN_TICKET_HEADER


PATCH_VERSION = "0.22.25"
LOGIN_READY_STATES = frozenset({"ready", "logged_in", "authenticated"})


def _bridge_login_observation(worker: dict[str, Any]) -> dict[str, Any]:
    metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
    bridge = metadata.get("bridge") if isinstance(metadata.get("bridge"), dict) else {}
    try:
        checked_at_ms = max(0, int(bridge.get("login_checked_at_ms") or 0))
    except (TypeError, ValueError):
        checked_at_ms = 0
    return {
        "state": str(bridge.get("login_state") or worker.get("chatgpt_status") or "").strip().lower(),
        "composer_ready": bridge.get("composer_ready") is True,
        "checked_at_ms": checked_at_ms,
    }


def _take_endpoint(app: FastAPI, path: str, method: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    wanted = str(method or "").upper()
    for route in list(app.router.routes):
        methods = set(getattr(route, "methods", set()) or set())
        if getattr(route, "path", None) == path and wanted in methods:
            app.router.routes.remove(route)
            endpoint = getattr(route, "endpoint", None)
            if callable(endpoint):
                return endpoint
            break
    raise RuntimeError(f"Linux Worker route not found: {wanted} {path}")


def install_linux_worker_login_freshness_patch(app: FastAPI) -> FastAPI:
    """Keep remote login open until this session observes a real login transition.

    The base Worker route historically closed a remote session whenever the
    persisted Worker row said ChatGPT was ready. That status can be stale, and
    it also makes it impossible to inspect an already-authenticated browser.
    This narrow patch keeps the existing proxy/open-session control plane but
    replaces completion detection with fresh Bridge telemetry scoped to the
    current remote-login ticket.
    """
    if getattr(app.state, "linux_worker_login_freshness_installed", False):
        return app
    if not getattr(app.state, "linux_worker_control_plane_installed", False):
        raise RuntimeError("Linux Worker control plane must be installed first")

    login_sessions = app.state.worker_login_sessions
    store = app.state.linux_workers
    send_worker_command = app.state.send_linux_worker_command
    open_path = "/api/admin/linux-workers/{worker_id}/login-session"
    frame_path = "/api/admin/linux-workers/{worker_id}/login-session/frame"
    original_open = _take_endpoint(app, open_path, "POST")
    _take_endpoint(app, frame_path, "GET")

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

    def require_login_session(worker_id: str, request: Request):
        ticket = str(request.headers.get(LOGIN_TICKET_HEADER) or "")
        if not ticket:
            raise HTTPException(403, "Remote login session ticket required")
        try:
            session = login_sessions.require(worker_id, ticket)
        except KeyError as exc:
            raise HTTPException(403, "Remote login session expired or invalid") from exc
        return ticket, session

    @app.post(open_path)
    async def open_worker_login_session_fresh(worker_id: str, request: Request) -> dict[str, Any]:
        # Capture the Bridge timestamp before the Worker navigates the browser to
        # the login URL. Telemetry produced by that navigation is therefore fresh
        # for this remote-control session instead of inheriting a persisted ready.
        worker = worker_exists(worker_id)
        baseline = _bridge_login_observation(worker)["checked_at_ms"]
        payload = await original_open(worker_id, request)
        ticket = str(payload.get("ticket") or "")
        if ticket:
            try:
                session = login_sessions.require(worker_id, ticket, touch=False)
                session.baseline_login_checked_at_ms = baseline
                session.last_login_checked_at_ms = baseline
                session.saw_login_required = False
            except KeyError:
                pass
        return payload

    @app.get(frame_path)
    async def worker_login_frame_fresh(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        ticket, session = require_login_session(worker_id, request)
        worker = worker_exists(worker_id)
        observation = _bridge_login_observation(worker)
        if session.observe_login(
            checked_at_ms=observation["checked_at_ms"],
            state=observation["state"],
            composer_ready=observation["composer_ready"],
        ):
            login_sessions.revoke(worker_id, ticket)
            await send_worker_command(worker_id, "close_login_session", {}, wait=False)
            return {
                "ok": True,
                "complete": True,
                "chatgpt_status": observation["state"],
                "login_checked_at_ms": observation["checked_at_ms"],
            }

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
            "login_state": observation["state"],
            "login_checked_at_ms": observation["checked_at_ms"],
        }

    app.state.linux_worker_login_freshness_installed = True
    return app
