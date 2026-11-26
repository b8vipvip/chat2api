"""C-05a manifest metadata registry plus the fixed OAuth provider catalogue."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response

from .admin_audit import AdminAuditError, AdminAuditStore
from .admin_auth import SESSION_COOKIE
from .plugin_registry import PluginRegistry, PluginRegistryError


logger = logging.getLogger(__name__)


def install_plugin_registry(app: FastAPI) -> FastAPI:
    if getattr(app.state, "product_plugin_registry_installed", False):
        return app
    registry = PluginRegistry(app.state.settings.data_dir)
    app.state.product_plugin_registry = registry
    app.state.product_plugin_registry_installed = True

    def admin_actor(request: Request) -> str:
        sessions = getattr(app.state, "admin_sessions", None)
        token = request.cookies.get(SESSION_COOKIE)
        if sessions is None or not sessions.authenticate(token):
            raise HTTPException(status_code=401, detail="Administrator login required")
        identity = getattr(sessions, "identity", None)
        username = identity(token) if callable(identity) else app.state.settings.admin_username
        return str(username or app.state.settings.admin_username)

    def record(
        request: Request, actor: str, action: str, pid: str, version: str, *, required: bool = True
    ) -> bool:
        audit = getattr(app.state, "admin_audit", None)
        if audit is None:
            audit = AdminAuditStore(app.state.settings.data_dir)
            app.state.admin_audit = audit
        try:
            audit.append(
                actor=actor,
                action=action,
                target_type="plugin_manifest",
                target_id=pid,
                details={"version": version, "execution_enabled": False},
                request_id=str(request.headers.get("x-request-id") or ""),
            )
        except AdminAuditError as exc:
            if required:
                raise HTTPException(status_code=503, detail="Administrator audit unavailable; registry write denied") from exc
            app.state.admin_audit_completion_pending = True
            logger.error(
                "Admin audit completion append failed after plugin manifest registration: plugin_id=%s version=%s",
                pid, version,
            )
            return False
        return True

    @app.get("/api/admin/plugins")
    async def list_plugins() -> dict[str, Any]:
        return {"data": registry.list_public_metadata(), "execution_enabled": False}

    @app.post("/api/admin/plugins", status_code=201)
    async def register_manifest(request: Request, manifest: dict[str, Any], response: Response) -> dict[str, Any]:
        try:
            # Validate before recording a bounded audit event; the full manifest
            # (including schema descriptions) must never enter audit records.
            from .plugin_registry import validate_manifest
            validated = validate_manifest(manifest)
        except PluginRegistryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        pid, version = validated["id"], validated["version"]
        actor = admin_actor(request)
        record(request, actor, "plugin.manifest_requested", pid, version)
        try:
            created = registry.add(validated)
        except PluginRegistryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_ok = record(request, actor, "plugin.manifest_completed", pid, version, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"plugin": created}

    @app.get("/api/admin/plugins/{plugin_id}/versions/{version}")
    async def plugin_version(plugin_id: str, version: str) -> dict[str, Any]:
        item = registry.get(plugin_id, version)
        if item is None:
            raise HTTPException(status_code=404, detail="Unknown plugin version")
        return item

    @app.get("/api/client/plugins")
    async def client_plugins() -> dict[str, Any]:
        # Only fixed providers with an administrator-configured OAuth app are
        # discoverable. User-specific grants are returned by /api/user/plugins.
        connections = getattr(app.state, "plugin_oauth_store", None)
        if connections is None:
            return {"data": [], "execution_enabled": False}
        catalog = connections.public_catalog()
        return {"data": catalog, "execution_enabled": bool(catalog)}

    return app
