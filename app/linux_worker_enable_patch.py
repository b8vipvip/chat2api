from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE
from .extension_capacity_control_patch import _request_extension_control
from .timezone_utils import beijing_now_iso


PATCH_VERSION = "0.22.41"
ASSET_PATH = "/assets/chat2api-linux-worker-enable-v46.js"
RUNTIME_ASSET_PATH = "/assets/chat2api-worker-runtime-v61.js"


def install_linux_worker_enable_patch(app: FastAPI) -> FastAPI:
    """Install the reversible Linux Worker master switch.

    Disabling is a two-phase operation: while the Extension WebSocket is still
    usable, ask the Worker to collapse chat2api-managed ChatGPT windows to one
    and wait for its control confirmation. Only after that succeeds do we persist
    the logical Worker switch, disable Extension authentication, and close the
    socket. Re-enabling restores authentication; the Extension reconnect loop can
    then reconnect and Reserve Pool may refill to the configured target.
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

    async def clear_sticky_routes(extension_id: str) -> None:
        if not extension_id:
            return
        async with registry.lock:
            registry.api_key_routes = {
                key_id: client_id
                for key_id, client_id in registry.api_key_routes.items()
                if client_id != extension_id
            }
            await registry.save()

    def persist_switch(worker_id: str, target: bool, *, control: dict[str, Any] | None = None) -> dict[str, Any]:
        with workers._lock:
            worker = workers.data.get("workers", {}).get(worker_id)
            if not worker:
                raise HTTPException(404, "Worker not found")
            worker["enabled"] = target
            worker["enabled_updated_at"] = beijing_now_iso()
            metadata = dict(worker.get("metadata") or {})
            switch = dict(metadata.get("admin_master_switch") or {})
            switch.update({
                "enabled": target,
                "routing_state": "connected" if target else "disconnected",
                "updated_at": worker["enabled_updated_at"],
                "transport_preserved": False,
                "window_policy": "configured-reserve" if target else "keep-one-managed-window",
            })
            if isinstance(control, dict):
                switch["control"] = control
            metadata["admin_master_switch"] = switch
            worker["metadata"] = metadata
            workers._save()
            return workers.public(worker)

    @app.put("/api/admin/linux-workers/{worker_id}/enabled")
    async def set_worker_enabled(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise HTTPException(400, "enabled must be a boolean")

        target = bool(body["enabled"])
        worker, extension_id, currently_enabled = read_worker(worker_id)

        if target:
            if extension_id and extension_id in registry.clients:
                try:
                    await registry.set_connection_enabled(extension_id, True)
                except KeyError as error:
                    raise HTTPException(409, "Bound Chrome extension no longer exists") from error
            public = persist_switch(worker_id, True)
            return {
                "ok": True,
                "enabled": True,
                "routing_state": "connected",
                "transport_preserved": False,
                "connection_enabled": True,
                "reconnect_pending": bool(extension_id and extension_id not in registry.sockets),
                "worker": public,
                "version": PATCH_VERSION,
            }

        if not currently_enabled:
            if extension_id and extension_id in registry.clients:
                await registry.set_connection_enabled(extension_id, False)
            public = persist_switch(worker_id, False)
            await clear_sticky_routes(extension_id)
            return {
                "ok": True,
                "enabled": False,
                "routing_state": "disconnected",
                "transport_preserved": False,
                "connection_enabled": False,
                "already_disconnected": True,
                "worker": public,
                "version": PATCH_VERSION,
            }

        if not extension_id:
            raise HTTPException(409, "Worker has no bound Chrome extension; cannot confirm managed-window collapse")
        item = registry.clients.get(extension_id)
        if not item:
            raise HTTPException(409, "Bound Chrome extension no longer exists")
        if extension_id not in registry.sockets or getattr(item, "connection_enabled", True) is False:
            raise HTTPException(409, "Worker extension is offline; reconnect it before using Disconnect so window cleanup can be confirmed")

        control = await _request_extension_control(
            app,
            extension_id,
            "worker.disable",
            {"keep_windows": 1},
            timeout_seconds=15.0,
        )
        if control.get("ok") is not True:
            detail = str(control.get("error") or "Worker did not confirm managed-window cleanup")
            code = str(control.get("error_code") or "worker_disable_control_failed")
            raise HTTPException(409, f"{detail} [{code}]")

        data = control.get("data") if isinstance(control.get("data"), dict) else {}
        if int(data.get("managed_windows_after") or 0) > 1:
            raise HTTPException(409, "Worker control returned success but more than one managed ChatGPT window remains")

        # The Extension has confirmed cleanup while its socket is still alive.
        # Persist the switch and then disable authentication/close that socket.
        public = persist_switch(worker_id, False, control=control)
        await clear_sticky_routes(extension_id)
        await registry.set_connection_enabled(extension_id, False)

        return {
            "ok": True,
            "enabled": False,
            "routing_state": "disconnected",
            "transport_preserved": False,
            "connection_enabled": False,
            "keep_windows": int(data.get("keep_windows") or 1),
            "closed_window_ids": data.get("closed_window_ids") if isinstance(data.get("closed_window_ids"), list) else [],
            "window_snapshot": data.get("window_snapshot") if isinstance(data.get("window_snapshot"), dict) else {},
            "control": control,
            "worker": public,
            "version": PATCH_VERSION,
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
