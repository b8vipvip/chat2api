from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE


PATCH_VERSION = "0.22.42"
logger = logging.getLogger("chat2api.worker-switch")
ASSET_PATH = "/assets/chat2api-linux-worker-enable-v46.js"
RUNTIME_ASSET_PATH = "/assets/chat2api-worker-runtime-v61.js"


def install_linux_worker_enable_patch(app: FastAPI) -> FastAPI:
    """Install the reversible Linux Worker master switch.

    Online disabling is a two-phase operation: while the Extension WebSocket is
    usable, ask the Worker to collapse chat2api-managed ChatGPT windows to one
    and wait for its control confirmation. An offline Worker can still be
    persistently disabled because there is no live control transport to clean up;
    the durable master flag then prevents later authentication/routing revival.
    Re-enabling restores authentication and lets the Extension reconnect loop
    refill Reserve Pool to the configured target.
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
            bridge["master_connection_enabled"] = False
            metadata["bridge"] = bridge
            payload["metadata"] = metadata
        return payload

    workers.public = public_with_enabled

    # Keep a final routing guard in addition to ClientRegistry.connection_enabled.
    # This makes a persisted disabled Worker ineligible even during startup races.
    base_online_client_ids = registry.online_client_ids
    base_resolve_client = registry.resolve_client

    def online_client_ids_without_disabled_workers() -> list[str]:
        blocked = disabled_client_ids()
        return [client_id for client_id in base_online_client_ids() if client_id not in blocked]

    def resolve_client_without_disabled_workers(requested: str | None) -> str:
        requested_id = str(requested or "")
        if requested_id and requested_id in disabled_client_ids():
            raise ConnectionError("Requested Chrome extension belongs to a disconnected Linux Worker")
        return base_resolve_client(requested)

    registry.online_client_ids = online_client_ids_without_disabled_workers
    registry.resolve_client = resolve_client_without_disabled_workers

    def read_worker(worker_id: str) -> tuple[dict[str, Any], str, bool]:
        with workers._lock:
            worker = workers.data.get("workers", {}).get(worker_id)
            if not worker:
                raise HTTPException(404, "Worker not found")
            if worker.get("revoked_at"):
                raise HTTPException(409, "Worker is revoked and cannot be re-enabled")
            return worker, str(worker.get("extension_client_id") or ""), enabled(worker)

    @app.put("/api/admin/linux-workers/{worker_id}/enabled")
    async def set_worker_enabled(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise HTTPException(400, "enabled must be a boolean")
        target = bool(body["enabled"])
        _worker, extension_id, _previous_enabled = read_worker(worker_id)
        # The v62 master switch is the sole durable writer for both admin APIs.
        # No local persistence, control transport, or capacity decision remains
        # in this compatibility endpoint.
        authority = getattr(app.state, "worker_disable_authority", None)
        if not isinstance(authority, dict):
            raise HTTPException(503, "Worker master switch authority is unavailable")
        logger.info(
            "Worker switch HTTP source=linux-worker-enabled-api-v46 worker=%s client=%s target=%s peer=%s",
            worker_id, extension_id or "-", target, getattr(request.client, "host", "-"),
        )
        item = registry.clients.get(extension_id) if extension_id else None
        source = "linux-worker-enabled-api-v46"
        if target:
            if extension_id and item is None:
                raise HTTPException(409, "Bound Chrome extension no longer exists")
            if item is not None:
                result = await authority["enable_client"](extension_id, source=source)
                public = result.get("worker")
                connection_enabled = bool(result.get("connection_enabled"))
                reconnect_pending = bool(result.get("reconnect_pending"))
            else:
                public = authority["persist_worker_switch"](worker_id, True, source=source)
                connection_enabled = False
                reconnect_pending = False
            return {
                "ok": True, "enabled": True, "routing_state": "connected",
                "transport_preserved": False,
                "connection_enabled": connection_enabled,
                "reconnect_pending": reconnect_pending,
                "worker": public, "version": PATCH_VERSION,
            }

        if item is not None:
            result = await authority["disable_client"](extension_id, source=source)
            public = result.get("worker")
            control = result.get("control") or {}
        else:
            reason = "bound-extension-missing" if extension_id else "no-bound-extension"
            control = {"ok": True, "skipped": True, "reason": reason, "data": {"keep_windows": 1}}
            public = authority["persist_worker_switch"](
                worker_id, False, source=source, control=control,
            )
            await authority["clear_sticky_routes"](extension_id)
        data = control.get("data") if isinstance(control.get("data"), dict) else {}
        return {
            "ok": True, "enabled": False, "routing_state": "disconnected",
            "transport_preserved": False, "connection_enabled": False,
            "keep_windows": int(data.get("keep_windows") or 1),
            "closed_window_ids": data.get("closed_window_ids") if isinstance(data.get("closed_window_ids"), list) else [],
            "window_snapshot": data.get("window_snapshot") if isinstance(data.get("window_snapshot"), dict) else {},
            "control": control, "worker": public, "version": PATCH_VERSION,
        }

    @app.get(ASSET_PATH, include_in_schema=False)
    async def linux_worker_enable_asset() -> Response:
        source = Path(__file__).with_name("admin_linux_worker_enable_v46.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get(RUNTIME_ASSET_PATH, include_in_schema=False)
    async def worker_runtime_asset() -> Response:
        source = Path(__file__).with_name("admin_worker_runtime_v61.js").read_text(encoding="utf-8")
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
        for asset in (ASSET_PATH, RUNTIME_ASSET_PATH):
            marker = f'<script src="{asset}"></script>'
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
