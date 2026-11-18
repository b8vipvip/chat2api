"""C-02a membership-only APIs. Not a tenant authorization retrofit for /v1."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .admin_audit import AdminAuditError, AdminAuditStore
from .admin_auth import SESSION_COOKIE, same_origin_admin_request
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


class TenantInvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=5, max_length=254)
    role: str
    expires_in_seconds: int = Field(default=7 * 24 * 3600, ge=300, le=30 * 24 * 3600)


class TenantInvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=32, max_length=256)


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

    def owner_change_allowed(actor: str) -> bool:
        """Resolve the actor's owner-transition privilege for the store transaction."""
        control = getattr(app.state, "control_plane", None)
        if control is not None:
            role = control.role_for(actor)
        else:
            # Lightweight unit-test apps may install membership routes without
            # the production RBAC store; only the configured root is privileged.
            root_name = str(app.state.settings.admin_username or "")
            role = "super_admin" if actor.casefold() == root_name.casefold() else None
        return role == "super_admin"

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
        allow_owner_change = owner_change_allowed(actor)
        audit_write(request, actor, "tenant.member_change_requested", "membership", tenant_id, {
            "user_id": body.user_id, "role": body.role,
        })
        try:
            result = store.upsert_member(
                tenant_id, body.user_id, body.role,
                allow_owner_change=allow_owner_change,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant") from exc
        except TenantMembershipError as exc:
            if str(exc) == "Workspace owner transitions require super_admin":
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_ok = audit_write(request, actor, "tenant.member_change_completed", "membership", tenant_id, {
            "user_id": body.user_id, "role": body.role,
        }, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"membership": result}

    @app.delete("/api/admin/tenants/{tenant_id}/members/{user_id}")
    async def admin_remove_member(tenant_id: str, user_id: str, request: Request, response: Response) -> dict[str, bool]:
        actor = admin_actor(request)
        allow_owner_change = owner_change_allowed(actor)
        audit_write(request, actor, "tenant.member_remove_requested", "membership", tenant_id, {"user_id": user_id})
        try:
            store.remove_member(
                tenant_id, user_id, allow_owner_change=allow_owner_change,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown membership") from exc
        except TenantMembershipError as exc:
            if str(exc) == "Workspace owner transitions require super_admin":
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_ok = audit_write(request, actor, "tenant.member_remove_completed", "membership", tenant_id, {"user_id": user_id}, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"removed": True}

    @app.get("/api/admin/tenants/{tenant_id}/invitations")
    async def admin_tenant_invitations(tenant_id: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        try:
            return {"data": store.list_invitations(tenant_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant") from exc

    @app.post("/api/admin/tenants/{tenant_id}/invitations", status_code=201)
    async def admin_create_tenant_invitation(
        tenant_id: str, body: TenantInvitationCreate, request: Request, response: Response
    ) -> dict[str, Any]:
        actor = admin_actor(request)
        email_hash = hashlib.sha256(body.email.strip().lower().encode("utf-8")).hexdigest()[:16]
        audit_write(request, actor, "tenant.invitation_issue_requested", "tenant_invitation", tenant_id, {
            "email_hash": email_hash, "role": body.role,
            "expires_in_seconds": body.expires_in_seconds,
        })
        try:
            invitation, code = store.create_invitation(
                tenant_id, body.email, body.role, actor,
                expires_in_seconds=body.expires_in_seconds,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant") from exc
        except TenantMembershipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_ok = audit_write(request, actor, "tenant.invitation_issue_completed", "tenant_invitation",
                               invitation["invitation_id"], {"email_hash": email_hash, "role": body.role},
                               required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        response.headers["Cache-Control"] = "no-store"
        # The raw one-time code is returned only in this issue response; the
        # database and audit journal retain only its hash / non-secret metadata.
        return {"invitation": invitation, "code": code}

    @app.delete("/api/admin/tenants/{tenant_id}/invitations/{invitation_id}")
    async def admin_revoke_tenant_invitation(
        tenant_id: str, invitation_id: str, request: Request, response: Response
    ) -> dict[str, bool]:
        actor = admin_actor(request)
        audit_write(request, actor, "tenant.invitation_revoke_requested", "tenant_invitation",
                    invitation_id, {"tenant_id": tenant_id})
        try:
            revoked = store.revoke_invitation(tenant_id, invitation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant invitation") from exc
        except TenantMembershipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_ok = audit_write(request, actor, "tenant.invitation_revoke_completed", "tenant_invitation",
                               invitation_id, {"tenant_id": tenant_id, "revoked": revoked},
                               required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"revoked": revoked}

    @app.post("/api/user/tenants/invitations/accept")
    async def accept_tenant_invitation(
        body: TenantInvitationAccept, request: Request, response: Response
    ) -> dict[str, Any]:
        if not same_origin_admin_request(request):
            raise HTTPException(status_code=403, detail="Workspace invitation acceptance requires a same-origin request")
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            raise HTTPException(status_code=401, detail="User login required")
        user_id = str(user.get("user_id") or "")
        code_hash = hashlib.sha256(body.code.encode("utf-8")).hexdigest()[:16]
        actor = "user:" + user_id
        audit_write(request, actor, "tenant.invitation_accept_requested", "tenant_invitation",
                    code_hash, {"user_id": user_id})
        try:
            membership = store.accept_invitation(
                body.code, user_id, str(user.get("email") or ""),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown tenant invitation") from exc
        except TenantMembershipError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_ok = audit_write(request, actor, "tenant.invitation_accept_completed", "tenant_invitation",
                               code_hash, {"tenant_id": membership["tenant_id"], "user_id": user_id},
                               required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        response.headers["Cache-Control"] = "no-store"
        return {"membership": membership}

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
