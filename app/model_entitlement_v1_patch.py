"""C-03a model entitlement API and enforcement adapter.

The adapter consumes the authenticated API-key routing context and the existing
model request context. It never selects a Worker or asserts served-model truth.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .admin_audit import AdminAuditError, AdminAuditStore
from .admin_auth import SESSION_COOKIE
from .user_commerce import USER_SESSION_COOKIE
from .model_entitlements import (
    ModelEntitlementError,
    ModelEntitlementStore,
    SUBJECT_TYPES,
)
from . import model_capability_routing_patch as model_routing
from . import v13_patch


class ModelEntitlementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effect: str
    models: list[str] = Field(min_length=1, max_length=200)
    enabled: bool = True


def install_model_entitlements(app: FastAPI) -> FastAPI:
    if getattr(app.state, "model_entitlements_installed", False):
        return app
    registry = app.state.registry
    store = ModelEntitlementStore(app.state.settings.data_dir)
    accounts = getattr(app.state, "user_accounts", None)
    tenant_memberships = getattr(app.state, "tenant_memberships", None)
    api_keys = getattr(app.state, "api_keys", None)
    if api_keys is None:
        raise RuntimeError("Model entitlements require the existing ApiKeyStore")
    if accounts is None:
        raise RuntimeError("Model entitlements require the authoritative UserAccountStore")
    if tenant_memberships is None:
        raise RuntimeError("Model entitlements require the authoritative TenantMembershipStore")
    app.state.model_entitlements = store

    audit = getattr(app.state, "admin_audit", None) or AdminAuditStore(app.state.settings.data_dir)
    app.state.admin_audit = audit

    def admin_actor(request: Request) -> str:
        sessions = getattr(app.state, "admin_sessions", None)
        token = request.cookies.get(SESSION_COOKIE)
        if sessions is None or not sessions.authenticate(token):
            raise HTTPException(status_code=401, detail="Administrator login required")
        identity = getattr(sessions, "identity", None)
        username = identity(token) if callable(identity) else app.state.settings.admin_username
        control = getattr(app.state, "control_plane", None)
        if control is not None and not control.permitted(username, request.url.path, request.method):
            raise HTTPException(status_code=403, detail="Administrator permission denied")
        return str(username)

    def audit_event(actor: str, action: str, subject_type: str, subject_id: str, details: dict[str, Any]) -> None:
        try:
            audit.append(
                actor=actor, action=action, target_type="model_entitlement",
                target_id=f"{subject_type}:{subject_id}", request_id="",
                details=details,
            )
        except AdminAuditError as exc:
            raise HTTPException(status_code=503, detail="Administrator audit unavailable; policy change denied") from exc

    def _validate_subject(subject_type: str, subject_id: str) -> None:
        if subject_type not in SUBJECT_TYPES:
            raise ModelEntitlementError("Unsupported subject_type")
        if subject_type == "global":
            if subject_id != "default":
                raise ModelEntitlementError("Global policy subject_id must be 'default'")
            return
        if subject_type == "key":
            try:
                exists = api_keys.get_public(subject_id) is not None
            except Exception:
                exists = subject_id in getattr(api_keys, "keys", {})
            if not exists:
                raise KeyError("Unknown API key")
            return
        if subject_type == "user":
            if accounts is None or accounts.admin_get(subject_id) is None:
                raise KeyError("Unknown user")
            return
        if subject_type == "tenant":
            if tenant_memberships is None or not tenant_memberships.exists(subject_id):
                raise KeyError("Unknown tenant")
        if subject_type == "tenant_role":
            if subject_id not in {"owner", "admin", "member", "viewer"}:
                raise ModelEntitlementError("Unknown tenant membership role")
            return

    def _identity(key_id: str | None) -> str | None:
        if not key_id or key_id == "master":
            return None
        try:
            # An unowned API key is a valid standalone key. A failed ownership
            # lookup is not equivalent to an unowned key: fail closed so a
            # transient account-store error cannot bypass user-level policy.
            owner_id = accounts.owner_for_key(str(key_id))
            if owner_id is None:
                return None
            normalized_owner_id = str(owner_id).strip()
            if not normalized_owner_id:
                raise RuntimeError("API key owner binding is empty")
            return normalized_owner_id
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "model_entitlement_identity_unavailable"},
            ) from exc

    def _tenant_context_for_key(
        key_id: str | None, owner_id: str | None,
    ) -> tuple[str | None, str | None]:
        if not key_id or key_id == "master":
            return None, None
        try:
            raw_tenant_id = tenant_memberships.tenant_for_key(str(key_id))
            if raw_tenant_id is None:
                return None, None
            tenant_id = str(raw_tenant_id).strip()
            if not tenant_id:
                raise RuntimeError("API key tenant binding is empty")
            # The binding stores the owner at the time the administrator
            # attached the key to a workspace. That snapshot must still match
            # the authoritative UserAccountStore owner on every request:
            # otherwise a transferred/deleted key could retain a stale tenant
            # role and inherit that workspace's model allowlist.
            raw_user_id = tenant_memberships.user_for_key(str(key_id))
            bound_user_id = str(raw_user_id or "").strip()
            if not owner_id or not bound_user_id or bound_user_id != owner_id:
                raise RuntimeError("Tenant-bound API key owner does not match its active account owner")
            role = str(tenant_memberships.role_for(tenant_id, owner_id) or "").strip()
            if not role:
                raise RuntimeError("Tenant-bound API key has no active owner membership")
            return tenant_id, role
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "model_entitlement_tenant_context_unavailable"},
            ) from exc

    def _decision(key_id: str | None, model_id: str | None) -> dict[str, Any]:
        if not key_id or key_id == "master":
            return {"allowed": True, "reason": "master_key", "model": model_id, "matched_rules": []}
        owner_id = _identity(key_id)
        tenant_id, tenant_role = _tenant_context_for_key(key_id, owner_id)
        return store.evaluate(
            str(model_id or ""),
            key_id=str(key_id),
            user_id=owner_id,
            tenant_id=tenant_id,
            tenant_role=tenant_role,
        )

    base_catalog = registry.model_catalog

    def model_catalog_with_entitlements(online_only: bool = True) -> list[dict[str, Any]]:
        rows = base_catalog(online_only=online_only)
        key_id = registry.routing_key_context.get()
        if not key_id or key_id == "master":
            return rows
        return [
            row for row in rows
            if _decision(str(key_id), str(row.get("id") or "")).get("allowed") is True
        ]

    registry.model_catalog = model_catalog_with_entitlements

    base_resolve_client = registry.resolve_client

    def resolve_client_with_entitlements(requested: str | None) -> str:
        key_id = registry.routing_key_context.get()
        if key_id and key_id != "master":
            target = model_routing._MODEL_CONTEXT.get() or v13_patch._target_context.get() or {}
            model_id = str(target.get("model") or "")
            decision = _decision(str(key_id), model_id)
            if not decision.get("allowed"):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "model_not_entitled",
                        "message": "API key or owning user is not entitled to the requested model",
                        "reason": decision.get("reason"),
                        "model": decision.get("model"),
                    },
                )
        return base_resolve_client(requested)

    registry.resolve_client = resolve_client_with_entitlements

    async def _response_bytes(response) -> bytes:
        body = getattr(response, "body", None)
        if body is not None:
            return bytes(body)
        chunks: list[bytes] = []
        iterator = getattr(response, "body_iterator", None)
        if iterator is not None:
            async for part in iterator:
                chunks.append(part.encode("utf-8") if isinstance(part, str) else bytes(part))
        return b"".join(chunks)

    @app.middleware("http")
    async def user_model_entitlement_filter(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/api/user/models" or response.status_code != 200 or accounts is None:
            return response
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            return response
        try:
            payload = json.loads((await _response_bytes(response)).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            return response
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return response
        user_id = str(user.get("user_id") or "")
        payload["data"] = [
            row for row in payload["data"]
            if isinstance(row, dict)
            and store.evaluate(
                str(row.get("id") or row.get("model") or ""),
                key_id=None, user_id=user_id,
            ).get("allowed") is True
        ]
        return JSONResponse(payload, status_code=response.status_code, headers={"Cache-Control": "private, no-store"})

    @app.get("/api/admin/model-entitlements")
    async def list_model_entitlements(request: Request) -> dict[str, Any]:
        admin_actor(request)
        return {"data": store.list_rules(), "supported_subject_types": sorted(SUBJECT_TYPES)}

    @app.put("/api/admin/model-entitlements/{subject_type}/{subject_id}")
    async def put_model_entitlement(
        subject_type: str, subject_id: str, body: ModelEntitlementUpdate, request: Request
    ) -> dict[str, Any]:
        actor = admin_actor(request)
        try:
            _validate_subject(subject_type, subject_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ModelEntitlementError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        details = {"effect": body.effect, "models": body.models, "enabled": body.enabled}
        audit_event(actor, "model.entitlement_requested", subject_type, subject_id, details)
        try:
            rule = store.upsert(subject_type, subject_id, body.effect, body.models, enabled=body.enabled)
        except ModelEntitlementError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_event(actor, "model.entitlement_completed", subject_type, subject_id, {
            "effect": rule["effect"], "models": rule["models"], "enabled": rule["enabled"],
        })
        return {"rule": rule}

    @app.delete("/api/admin/model-entitlements/{subject_type}/{subject_id}")
    async def delete_model_entitlement(subject_type: str, subject_id: str, request: Request) -> dict[str, bool]:
        actor = admin_actor(request)
        try:
            _validate_subject(subject_type, subject_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ModelEntitlementError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_event(actor, "model.entitlement_delete_requested", subject_type, subject_id, {})
        try:
            deleted = store.delete(subject_type, subject_id)
        except ModelEntitlementError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_event(actor, "model.entitlement_delete_completed", subject_type, subject_id, {"deleted": deleted})
        return {"deleted": deleted}

    app.state.model_entitlements_installed = True
    return app
