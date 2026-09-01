from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .admin_auth import SESSION_COOKIE
from .extension_capacity_control_patch import _request_extension_control
from .timezone_utils import beijing_now_iso


PATCH_VERSION = "0.22.42"
ASSET_PATH = "/assets/chat2api-worker-disable-authority-v62.js"
EXTENSION_ACTION_RE = re.compile(r"^/api/admin/extensions/([^/]+)/(disconnect|enable)$")
logger = logging.getLogger("chat2api.worker-disable-authority")


def install_worker_disable_authority_patch(app: FastAPI) -> FastAPI:
    """Make the administrator enabled/disabled flag authoritative everywhere.

    Linux Workers have two durable identities: the Agent record and the bound
    Chrome extension record. Older pairing reconciliation could set the extension
    connection back to enabled even after the Agent record had been disabled.
    This final layer makes the Linux Worker record authoritative, intercepts the
    generic Worker-management toggle, and prevents binding/token refresh paths
    from reviving a disabled Worker.
    """

    if getattr(app.state, "worker_disable_authority_patch_installed", False):
        return app

    workers = app.state.linux_workers
    registry = app.state.registry
    app.state.worker_disable_authority_patch_installed = True

    def admin_ok(request: Request) -> bool:
        sessions = getattr(app.state, "admin_sessions", None)
        return bool(sessions and sessions.authenticate(request.cookies.get(SESSION_COOKIE)))

    def worker_for_client(client_id: str) -> dict[str, Any] | None:
        wanted = str(client_id or "")
        if not wanted:
            return None
        with workers._lock:
            for worker in workers.data.get("workers", {}).values():
                if worker.get("revoked_at"):
                    continue
                direct = str(worker.get("extension_client_id") or "")
                metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
                bridge = metadata.get("bridge") if isinstance(metadata.get("bridge"), dict) else {}
                bridged = str(bridge.get("extension_id") or bridge.get("client_id") or "")
                if wanted in {direct, bridged}:
                    return worker
        return None

    def worker_id_for_client(client_id: str) -> str:
        worker = worker_for_client(client_id)
        return str(worker.get("worker_id") or "") if worker else ""

    def disabled_linux_client_ids() -> set[str]:
        blocked: set[str] = set()
        with workers._lock:
            for worker in workers.data.get("workers", {}).values():
                if worker.get("revoked_at") or worker.get("enabled") is not False:
                    continue
                client_id = str(worker.get("extension_client_id") or "")
                if client_id:
                    blocked.add(client_id)
                metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
                bridge = metadata.get("bridge") if isinstance(metadata.get("bridge"), dict) else {}
                bridged = str(bridge.get("extension_id") or bridge.get("client_id") or "")
                if bridged:
                    blocked.add(bridged)
        return blocked

    def persist_worker_switch(
        worker_id: str,
        target: bool,
        *,
        source: str,
        control: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with workers._lock:
            worker = workers.data.get("workers", {}).get(worker_id)
            if not worker:
                raise HTTPException(404, "Worker not found")
            if worker.get("revoked_at"):
                raise HTTPException(409, "Worker is revoked")
            worker["enabled"] = bool(target)
            worker["enabled_updated_at"] = beijing_now_iso()
            metadata = dict(worker.get("metadata") or {})
            switch = dict(metadata.get("admin_master_switch") or {})
            switch.update(
                {
                    "enabled": bool(target),
                    "state": "enabled" if target else "disabled",
                    "routing_state": "connected" if target else "disconnected",
                    "source": source,
                    "updated_at": worker["enabled_updated_at"],
                    "transport_preserved": False,
                    "window_policy": "configured-reserve" if target else "keep-one-managed-window",
                }
            )
            if isinstance(control, dict):
                switch["control"] = deepcopy(control)
            metadata["admin_master_switch"] = switch
            bridge = dict(metadata.get("bridge") or {})
            bridge["master_connection_enabled"] = bool(target)
            bridge["routing_enabled"] = bool(target)
            if not target:
                bridge["logical_online"] = False
                bridge["connection_enabled"] = False
                bridge["online"] = False
            metadata["bridge"] = bridge
            worker["metadata"] = metadata
            workers._save()
            return workers.public(worker)

    async def clear_sticky_routes(client_id: str) -> None:
        if not client_id:
            return
        async with registry.lock:
            next_routes = {
                key_id: routed_client
                for key_id, routed_client in registry.api_key_routes.items()
                if routed_client != client_id
            }
            if next_routes == registry.api_key_routes:
                return
            registry.api_key_routes = next_routes
            await registry.save()

    # ClientRegistry.save() is the last durable boundary reached by token rotation,
    # pairing reconciliation and most binding refresh paths. Enforce the Linux
    # Worker master flag immediately before every registry persistence so none of
    # those background paths can revive a disabled extension identity.
    if not getattr(registry, "_chat2api_worker_disable_authority_save_v62", False):
        base_save = registry.save

        async def save_with_worker_disable_authority() -> None:
            for client_id in disabled_linux_client_ids():
                item = registry.clients.get(client_id)
                if item is not None:
                    item.connection_enabled = False
            await base_save()

        registry.save = save_with_worker_disable_authority
        registry._chat2api_worker_disable_authority_save_v62 = True

    # Do not treat every registry setter call as administrator intent. Pairing,
    # token refresh and compatibility patches may legitimately call this method.
    # A disabled Linux Worker is allowed to reopen transport only after an admin
    # flow has first persisted worker.enabled=True. This keeps the durable Worker
    # master flag authoritative even when an older background path asks for True.
    if not getattr(registry, "_chat2api_worker_disable_authority_set_v62", False):
        base_set_connection_enabled = registry.set_connection_enabled

        async def set_connection_enabled_authoritative(client_id: str, enabled: bool):
            worker = worker_for_client(client_id)
            if bool(enabled) and worker and worker.get("enabled") is False:
                if client_id not in registry.clients:
                    raise KeyError("Unknown client_id")
                # Reject the attempted revival through the real disable boundary,
                # not a metadata-only save. This also closes any stale WebSocket
                # left behind by an older reconnect race.
                return await base_set_connection_enabled(client_id, False)
            return await base_set_connection_enabled(client_id, bool(enabled))

        registry.set_connection_enabled = set_connection_enabled_authoritative
        registry._chat2api_worker_disable_authority_set_v62 = True

    # The generic Worker list is backed by registry.summaries(). Annotate it from
    # the same Linux Worker master record so presentation never diverges even if a
    # stale socket object is still being detached asynchronously.
    if not getattr(registry, "_chat2api_worker_disable_authority_summaries_v62", False):
        base_summaries = registry.summaries

        def summaries_with_worker_authority() -> list[dict[str, Any]]:
            rows = base_summaries()
            for row in rows:
                client_id = str(row.get("client_id") or "")
                worker = worker_for_client(client_id)
                if not worker:
                    row["admin_enabled"] = row.get("connection_enabled") is not False
                    continue
                is_enabled = worker.get("enabled") is not False and not worker.get("revoked_at")
                row["admin_enabled"] = is_enabled
                row["linux_worker_id"] = str(worker.get("worker_id") or "")
                if not is_enabled:
                    row["connection_enabled"] = False
                    row["online"] = False
                    row["busy"] = False
            return rows

        registry.summaries = summaries_with_worker_authority
        registry._chat2api_worker_disable_authority_summaries_v62 = True

    async def collapse_managed_windows(client_id: str) -> dict[str, Any]:
        item = registry.clients.get(client_id)
        if not item:
            raise HTTPException(404, "Unknown extension ID")
        if getattr(item, "connection_enabled", True) is False:
            return {"ok": True, "skipped": True, "reason": "already-disabled", "data": {"keep_windows": 1}}
        if client_id not in registry.sockets:
            return {"ok": True, "skipped": True, "reason": "offline-no-control", "data": {"keep_windows": 1}}
        control = await _request_extension_control(
            app,
            client_id,
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
            raise HTTPException(409, "Worker confirmed disable but more than one managed ChatGPT window remains")
        return control

    async def disable_client(client_id: str) -> dict[str, Any]:
        client_id = str(client_id or "")
        if client_id not in registry.clients:
            raise HTTPException(404, "Unknown extension ID")
        control = await collapse_managed_windows(client_id)
        worker_id = worker_id_for_client(client_id)
        worker_public = None
        if worker_id:
            worker_public = persist_worker_switch(
                worker_id,
                False,
                source="worker-management-disable-v62",
                control=control,
            )
        await clear_sticky_routes(client_id)
        item = await registry.set_connection_enabled(client_id, False)
        return {
            "ok": True,
            "client_id": client_id,
            "enabled": False,
            "state": "disabled",
            "connection_enabled": item.connection_enabled,
            "worker": worker_public,
            "control": control,
            "version": PATCH_VERSION,
        }

    async def enable_client(client_id: str) -> dict[str, Any]:
        client_id = str(client_id or "")
        if client_id not in registry.clients:
            raise HTTPException(404, "Unknown extension ID")
        worker_id = worker_id_for_client(client_id)
        worker_public = None
        if worker_id:
            worker_public = persist_worker_switch(
                worker_id,
                True,
                source="worker-management-enable-v62",
            )
        item = await registry.set_connection_enabled(client_id, True)
        return {
            "ok": True,
            "client_id": client_id,
            "enabled": True,
            "state": "enabled",
            "connection_enabled": item.connection_enabled,
            "reconnect_pending": client_id not in registry.sockets,
            "worker": worker_public,
            "version": PATCH_VERSION,
        }

    app.state.worker_disable_authority = {
        "version": 62,
        "disable_client": disable_client,
        "enable_client": enable_client,
        "disabled_linux_client_ids": disabled_linux_client_ids,
        "persist_worker_switch": persist_worker_switch,
    }

    @app.get(ASSET_PATH, include_in_schema=False)
    async def worker_disable_authority_asset() -> Response:
        source = Path(__file__).with_name("admin_worker_disable_authority_v62.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def worker_disable_authority_middleware(request: Request, call_next):
        path = request.url.path
        if request.method == "POST" and path == "/api/workers/extension-binding-ticket":
            worker_id = str(request.headers.get("x-worker-id") or "").strip()
            worker = workers.data.get("workers", {}).get(worker_id)
            if worker and worker.get("enabled") is False and not worker.get("revoked_at"):
                return JSONResponse({"detail": "Worker is disabled by administrator", "state": "disabled"}, status_code=409)

        if request.method == "POST" and path == "/api/extensions/worker-bind":
            try:
                body = await request.body()
                payload = json.loads(body.decode("utf-8")) if body else {}
                ticket = str(payload.get("ticket") or "") if isinstance(payload, dict) else ""
                ticket_store = getattr(app.state, "worker_bridge_binding_tickets", None)
                binding = ticket_store.require(ticket) if ticket_store and ticket else None
                worker = workers.data.get("workers", {}).get(str(getattr(binding, "worker_id", ""))) if binding else None
                if worker and worker.get("enabled") is False and not worker.get("revoked_at"):
                    return JSONResponse({"detail": "Worker is disabled by administrator", "state": "disabled"}, status_code=409)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                pass

        match = EXTENSION_ACTION_RE.match(path) if request.method == "POST" else None
        if match:
            if not admin_ok(request):
                return JSONResponse({"detail": "Administrator login required"}, status_code=401)
            client_id, action = match.groups()
            try:
                result = await (enable_client(client_id) if action == "enable" else disable_client(client_id))
            except HTTPException as error:
                return JSONResponse({"detail": str(error.detail)}, status_code=error.status_code)
            except Exception as error:
                logger.exception("Worker master switch failed client=%s action=%s", client_id, action)
                return JSONResponse({"detail": str(error)}, status_code=500)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})

        response = await call_next(request)
        if path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
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
