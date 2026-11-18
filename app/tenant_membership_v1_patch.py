"""C-02a membership-only APIs. Not a tenant authorization retrofit for /v1."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from .admin_audit import AdminAuditError, AdminAuditStore
from .admin_auth import SESSION_COOKIE
from .tenant_membership import TenantMembershipError, TenantMembershipStore
from .user_commerce import USER_SESSION_COOKIE


logger = logging.getLogger(__name__)


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    display_name: str


class MembershipUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    role: str


class TenantKeyBind(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str


def install_tenant_memberships(app: FastAPI) -> FastAPI:
    if getattr(app.state, "tenant_memberships_installed", False):
        return app
    accounts = getattr(app.state, "user_accounts", None)
    if accounts is None:
        raise RuntimeError("Tenant memberships require the existing UserAccountStore installer")
    api_keys = getattr(app.state, "api_keys", None)
    if api_keys is None:
        raise RuntimeError("Tenant memberships require the existing ApiKeyStore installer")
    store = TenantMembershipStore(app.state.settings.data_dir)
    app.state.tenant_memberships = store
    app.state.tenant_memberships_installed = True

    def admin_actor(request: Request) -> str:
        session = getattr(app.state, "admin_sessions", None)
        cookie = request.cookies.get(SESSION_COOKIE)
        if session is None or not session.authenticate(cookie):
            raise HTTPException(status_code=401, detail="Administrator login required")
        identity = getattr(session, "identity", None)
        username = identity(cookie) if callable(identity) else app.state.settings.admin_username
        return str(username)

    def audit_write(
        request: Request, actor: str, action: str, kind: str, target: str, details: dict, *, required: bool = True
    ) -> bool:
        audit = getattr(app.state, "admin_audit", None)
        if audit is None:
            audit = AdminAuditStore(app.state.settings.data_dir)
            app.state.admin_audit = audit
        try:
            audit.append(
                actor=actor, action=action, target_type=kind, target_id=target,
                request_id=str(request.headers.get("x-request-id") or ""), details=details,
            )
        except AdminAuditError as exc:
            if required:
                raise HTTPException(status_code=503, detail="Audit unavailable; change denied") from exc
            # The mutation has already committed. Do not return a false failure
            # that would invite a duplicate retry; surface the missing completion
            # record and keep a process-level degraded marker for operators.
            app.state.admin_audit_completion_pending = True
            logger.error(
                "Admin audit completion append failed after tenant mutation: action=%s target_type=%s target_id=%s",
                action, kind, target,
            )
            return False
        return True

    @app.get("/api/admin/tenants")
    async def admin_tenants() -> dict[str, Any]:
        return {"data": store.list_tenants()}

    @app.post("/api/admin/tenants", status_code=201)
    async def admin_create_tenant(body: TenantCreate, request: Request, response: Response) -> dict[str, Any]:
        actor = admin_actor(request)
        audit_write(request, actor, "tenant.create_requested", "tenant", body.slug, {})
        try:
            tenant = store.create(body.slug, body.display_name)
        except TenantMembershipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_ok = audit_write(request, actor, "tenant.create_completed", "tenant", tenant["tenant_id"], {"slug": body.slug}, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"tenant": tenant}

    @app.get("/api/admin/tenants/{tenant_id}/members")
    async def admin_members(tenant_id: str) -> dict[str, Any]:
        try:
            return {"data": store.list_members(tenant_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant") from exc

    @app.put("/api/admin/tenants/{tenant_id}/members")
    async def admin_put_member(tenant_id: str, body: MembershipUpsert, request: Request, response: Response) -> dict[str, Any]:
        if accounts.admin_get(body.user_id) is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        actor = admin_actor(request)
        audit_write(request, actor, "tenant.member_change_requested", "membership", tenant_id, {
            "user_id": body.user_id, "role": body.role,
        })
        try:
            result = store.upsert_member(tenant_id, body.user_id, body.role)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant") from exc
        except TenantMembershipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_ok = audit_write(request, actor, "tenant.member_change_completed", "membership", tenant_id, {
            "user_id": body.user_id, "role": body.role,
        }, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"membership": result}

    @app.delete("/api/admin/tenants/{tenant_id}/members/{user_id}")
    async def admin_remove_member(tenant_id: str, user_id: str, request: Request, response: Response) -> dict[str, bool]:
        actor = admin_actor(request)
        audit_write(request, actor, "tenant.member_remove_requested", "membership", tenant_id, {"user_id": user_id})
        try:
            store.remove_member(tenant_id, user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown membership") from exc
        except TenantMembershipError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_ok = audit_write(request, actor, "tenant.member_remove_completed", "membership", tenant_id, {"user_id": user_id}, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"removed": True}

    @app.get("/api/admin/tenants/{tenant_id}/keys")
    async def admin_tenant_keys(tenant_id: str) -> dict[str, Any]:
        try:
            return {"data": store.keys_for_tenant(tenant_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant") from exc

    @app.put("/api/admin/tenants/{tenant_id}/keys/{key_id}")
    async def admin_bind_tenant_key(
        tenant_id: str, key_id: str, body: TenantKeyBind, request: Request, response: Response
    ) -> dict[str, Any]:
        # A key is bound only through its actual UserAccountStore owner and an
        # existing tenant membership. No caller-supplied tenant header is used.
        if accounts.admin_get(body.user_id) is None or not accounts.owns_key(body.user_id, key_id):
            raise HTTPException(status_code=404, detail="User-owned API key not found")
        public_key = api_keys.get_public(key_id)
        if public_key is None:
            raise HTTPException(status_code=404, detail="API key not found")
        if not public_key.get("enabled") or public_key.get("revoked_at"):
            raise HTTPException(status_code=409, detail="Only active, non-revoked API keys can be bound")
        if not store.exists(tenant_id):
            raise HTTPException(status_code=404, detail="Unknown tenant")
        actor = admin_actor(request)
        audit_write(request, actor, "tenant.api_key_bind_requested", "tenant_api_key", tenant_id, {
            "user_id": body.user_id, "key_id": key_id,
        })
        try:
            result = store.bind_key(tenant_id, body.user_id, key_id)
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="User must be a member of the tenant before binding a key") from exc
        except TenantMembershipError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_ok = audit_write(request, actor, "tenant.api_key_bind_completed", "tenant_api_key", tenant_id, {
            "user_id": body.user_id, "key_id": key_id,
        }, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"binding": result}

    @app.delete("/api/admin/tenants/{tenant_id}/keys/{key_id}")
    async def admin_unbind_tenant_key(
        tenant_id: str, key_id: str, request: Request, response: Response
    ) -> dict[str, bool]:
        actor = admin_actor(request)
        audit_write(request, actor, "tenant.api_key_unbind_requested", "tenant_api_key", tenant_id, {
            "key_id": key_id,
        })
        try:
            store.unbind_key(tenant_id, key_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant API key binding") from exc
        audit_ok = audit_write(request, actor, "tenant.api_key_unbind_completed", "tenant_api_key", tenant_id, {
            "key_id": key_id,
        }, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"unbound": True}

    @app.get("/api/user/tenants")
    async def own_workspaces(request: Request) -> dict[str, Any]:
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            raise HTTPException(status_code=401, detail="User login required")
        return {"data": store.list_for_user(str(user["user_id"]))}

    return app
