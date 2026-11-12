"""C-02a membership-only APIs. Not a tenant authorization retrofit for /v1."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .admin_audit import AdminAuditError, AdminAuditStore
from .tenant_membership import TenantMembershipError, TenantMembershipStore
from .user_commerce import USER_SESSION_COOKIE


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    display_name: str


class MembershipUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    role: str


def install_tenant_memberships(app: FastAPI) -> FastAPI:
    if getattr(app.state, "tenant_memberships_installed", False):
        return app
    accounts = getattr(app.state, "user_accounts", None)
    if accounts is None:
        raise RuntimeError("Tenant memberships require the existing UserAccountStore installer")
    store = TenantMembershipStore(app.state.settings.data_dir)
    app.state.tenant_memberships = store
    app.state.tenant_memberships_installed = True

    def audit_write(request: Request, action: str, kind: str, target: str, details: dict) -> None:
        audit = getattr(app.state, "admin_audit", None)
        if audit is None:
            audit = AdminAuditStore(app.state.settings.data_dir)
            app.state.admin_audit = audit
        session = getattr(app.state, "admin_sessions", None)
        cookie = request.cookies.get("chat2api_admin_session")
        if session is None or not session.authenticate(cookie):
            raise HTTPException(status_code=401, detail="Administrator login required")
        identity = getattr(session, "identity", None)
        username = identity(cookie) if callable(identity) else app.state.settings.admin_username
        try:
            audit.append(
                actor=str(username), action=action, target_type=kind, target_id=target,
                request_id=str(request.headers.get("x-request-id") or ""), details=details,
            )
        except AdminAuditError as exc:
            raise HTTPException(status_code=503, detail="Audit unavailable; change denied") from exc

    @app.get("/api/admin/tenants")
    async def admin_tenants() -> dict[str, Any]:
        return {"data": store.list_tenants()}

    @app.post("/api/admin/tenants", status_code=201)
    async def admin_create_tenant(body: TenantCreate, request: Request) -> dict[str, Any]:
        audit_write(request, "tenant.create_requested", "tenant", body.slug, {})
        try:
            tenant = store.create(body.slug, body.display_name)
        except TenantMembershipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_write(request, "tenant.create_completed", "tenant", tenant["tenant_id"], {"slug": body.slug})
        return {"tenant": tenant}

    @app.get("/api/admin/tenants/{tenant_id}/members")
    async def admin_members(tenant_id: str) -> dict[str, Any]:
        try:
            return {"data": store.list_members(tenant_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant") from exc

    @app.put("/api/admin/tenants/{tenant_id}/members")
    async def admin_put_member(tenant_id: str, body: MembershipUpsert, request: Request) -> dict[str, Any]:
        if accounts.admin_get(body.user_id) is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        audit_write(request, "tenant.member_change_requested", "membership", tenant_id, {
            "user_id": body.user_id, "role": body.role,
        })
        try:
            result = store.upsert_member(tenant_id, body.user_id, body.role)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant") from exc
        except TenantMembershipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_write(request, "tenant.member_change_completed", "membership", tenant_id, {
            "user_id": body.user_id, "role": body.role,
        })
        return {"membership": result}

    @app.delete("/api/admin/tenants/{tenant_id}/members/{user_id}")
    async def admin_remove_member(tenant_id: str, user_id: str, request: Request) -> dict[str, bool]:
        audit_write(request, "tenant.member_remove_requested", "membership", tenant_id, {"user_id": user_id})
        try:
            store.remove_member(tenant_id, user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown membership") from exc
        except TenantMembershipError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_write(request, "tenant.member_remove_completed", "membership", tenant_id, {"user_id": user_id})
        return {"removed": True}

    @app.get("/api/user/tenants")
    async def own_workspaces(request: Request) -> dict[str, Any]:
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            raise HTTPException(status_code=401, detail="User login required")
        return {"data": store.list_for_user(str(user["user_id"]))}

    return app
