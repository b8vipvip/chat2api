"""C-05a product Plugin Registry API: metadata only, execution disabled."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .admin_audit import AdminAuditError, AdminAuditStore
from .plugin_registry import PluginRegistry, PluginRegistryError


def install_plugin_registry(app: FastAPI) -> FastAPI:
    if getattr(app.state, "product_plugin_registry_installed", False):
        return app
    registry = PluginRegistry(app.state.settings.data_dir)
    app.state.product_plugin_registry = registry
    app.state.product_plugin_registry_installed = True

    def record(request: Request, action: str, pid: str, version: str) -> None:
        audit = getattr(app.state, "admin_audit", None)
        if audit is None:
            audit = AdminAuditStore(app.state.settings.data_dir)
            app.state.admin_audit = audit
        sessions = getattr(app.state, "admin_sessions", None)
        token = request.cookies.get("chat2api_admin_session")
        if sessions is None or not sessions.authenticate(token):
            raise HTTPException(status_code=401, detail="Administrator login required")
        # C-05 branches from main independently of C-01 RBAC. Legacy v17
        # sessions authenticate the configured root; the new C-01 identity()
        # method is used only after that parallel PR has been integrated.
        identity = getattr(sessions, "identity", None)
        username = identity(token) if callable(identity) else app.state.settings.admin_username
        try:
            audit.append(
                actor=str(username or app.state.settings.admin_username),
                action=action,
                target_type="plugin_manifest",
                target_id=pid,
                details={"version": version, "execution_enabled": False},
                request_id=str(request.headers.get("x-request-id") or ""),
            )
        except AdminAuditError as exc:
            raise HTTPException(status_code=503, detail="Administrator audit unavailable; registry write denied") from exc

    @app.get("/api/admin/plugins")
    async def list_plugins() -> dict[str, Any]:
        return {"data": registry.list_public_metadata(), "execution_enabled": False}

    @app.post("/api/admin/plugins", status_code=201)
    async def register_manifest(request: Request, manifest: dict[str, Any]) -> dict[str, Any]:
        try:
            # Validate before recording a bounded audit event; the full manifest
            # (including schema descriptions) must never enter audit records.
            from .plugin_registry import validate_manifest
            validated = validate_manifest(manifest)
        except PluginRegistryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        pid, version = validated["id"], validated["version"]
        record(request, "plugin.manifest_requested", pid, version)
        try:
            created = registry.add(validated)
        except PluginRegistryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record(request, "plugin.manifest_completed", pid, version)
        return {"plugin": created}

    @app.get("/api/admin/plugins/{plugin_id}/versions/{version}")
    async def plugin_version(plugin_id: str, version: str) -> dict[str, Any]:
        item = registry.get(plugin_id, version)
        if item is None:
            raise HTTPException(status_code=404, detail="Unknown plugin version")
        return item

    @app.get("/api/client/plugins")
    async def client_plugins() -> dict[str, Any]:
        # No user grant, trust/sandbox or Secret Store approval exists yet.
        return {"data": [], "execution_enabled": False}

    return app
