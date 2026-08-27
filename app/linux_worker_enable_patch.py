from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE
from .timezone_utils import beijing_now_iso


PATCH_VERSION = "0.22.26"
ASSET_PATH = "/assets/chat2api-linux-worker-enable-v46.js"


def install_linux_worker_enable_patch(app: FastAPI) -> FastAPI:
    """Make Worker disable/enable a reversible routing decision.

    A disabled Worker must keep its Agent/Bridge transport alive so heartbeats,
    diagnostics and a later re-enable do not require a process restart.  The
    server therefore excludes its bound extension from request routing instead
    of revoking credentials or closing sockets.
    """

    if getattr(app.state, "linux_worker_enable_patch_installed", False):
        return app

    workers = app.state.linux_workers
    registry = app.state.registry
    app.state.linux_worker_enable_patch_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    def enabled(worker: dict[str, Any] | None) -> bool:
        return bool(worker) and worker.get("enabled") is not False and not worker.get("revoked_at")

    def disabled_client_ids() -> set[str]:
        with workers._lock:
            return {
                str(worker.get("extension_client_id") or "")
                for worker in workers.data.get("workers", {}).values()
                if worker.get("enabled") is False
                and not worker.get("revoked_at")
                and str(worker.get("extension_client_id") or "")
            }

    # Keep the persistent worker schema backward compatible: old rows that do
    # not have an `enabled` key are enabled.  Public status is deliberately
    # logical/routing status; physical heartbeats may continue while disabled.
    base_public = workers.public

    def public_with_enabled(worker: dict[str, Any]) -> dict[str, Any]:
        payload = base_public(worker)
        is_enabled = enabled(worker)
        payload["enabled"] = is_enabled
        payload["routing_state"] = "connected" if is_enabled else "disconnected"
        if not is_enabled:
            payload["status"] = "offline"
            payload["chatgpt_status"] = "offline"
            metadata = deepcopy(payload.get("metadata") or {})
            bridge = deepcopy(metadata.get("bridge") or {})
            bridge["routing_enabled"] = False
            bridge["logical_online"] = False
            metadata["bridge"] = bridge
            payload["metadata"] = metadata
        return payload

    workers.public = public_with_enabled

    # Request routing is the enforcement boundary.  Do not mutate
    # ClientRegistry.connection_enabled here: that field also gates extension
    # authentication/reconnect and would turn a harmless admin toggle into a
    # transport outage.  Filtering online_client_ids covers automatic/sticky
    # routing, while the resolve wrapper also protects explicit client_id calls.
    base_online_client_ids = registry.online_client_ids
    base_resolve_client = registry.resolve_client

    def online_client_ids_without_disabled_workers() -> list[str]:
        blocked = disabled_client_ids()
        return [client_id for client_id in base_online_client_ids() if client_id not in blocked]

    def resolve_client_without_disabled_workers(requested: str | None) -> str:
        requested_id = str(requested or "")
        if requested_id and requested_id in disabled_client_ids():
            raise ConnectionError("Requested Chrome extension belongs to a disabled Linux Worker")
        return base_resolve_client(requested)

    registry.online_client_ids = online_client_ids_without_disabled_workers
    registry.resolve_client = resolve_client_without_disabled_workers

    @app.put("/api/admin/linux-workers/{worker_id}/enabled")
    async def set_worker_enabled(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise HTTPException(400, "enabled must be a boolean")

        target = bool(body["enabled"])
        with workers._lock:
            worker = workers.data.get("workers", {}).get(worker_id)
            if not worker:
                raise HTTPException(404, "Worker not found")
            if worker.get("revoked_at"):
                raise HTTPException(409, "Worker is revoked and cannot be re-enabled")
            worker["enabled"] = target
            worker["enabled_updated_at"] = beijing_now_iso()
            metadata = dict(worker.get("metadata") or {})
            control = dict(metadata.get("admin_routing_control") or {})
            control.update({
                "enabled": target,
                "routing_state": "connected" if target else "disconnected",
                "updated_at": worker["enabled_updated_at"],
                "transport_preserved": True,
            })
            metadata["admin_routing_control"] = control
            worker["metadata"] = metadata
            workers._save()
            extension_id = str(worker.get("extension_client_id") or "")

        # Remove sticky API-key affinity when disabling so the next request can
        # immediately select another extension.  The live WebSocket is left
        # untouched on purpose.
        if not target and extension_id:
            async with registry.lock:
                registry.api_key_routes = {
                    key_id: client_id
                    for key_id, client_id in registry.api_key_routes.items()
                    if client_id != extension_id
                }
                await registry.save()

        with workers._lock:
            current = workers.data["workers"][worker_id]
            public = workers.public(current)
        return {
            "ok": True,
            "enabled": target,
            "routing_state": "connected" if target else "disconnected",
            "transport_preserved": True,
            "worker": public,
            "version": PATCH_VERSION,
        }

    @app.get(ASSET_PATH, include_in_schema=False)
    async def linux_worker_enable_asset() -> Response:
        source = Path(__file__).with_name("admin_linux_worker_enable_v46.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def inject_linux_worker_enable_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
            return response
        body = getattr(response, "body", None)
        if body is None:
            chunks: list[bytes] = []
            iterator = getattr(response, "body_iterator", None)
            if iterator is not None:
                async for chunk in iterator:
                    chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
            body = b"".join(chunks)
        text = bytes(body).decode("utf-8", errors="replace")
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
