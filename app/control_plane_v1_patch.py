"""Production installer for the first C-track RBAC and client policy slice.

Install before v17 so the existing administrator cookie/auth and same-origin
middleware is the single ingress gate. This module owns only its new routes.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .admin_audit import AdminAuditError, AdminAuditStore
from .control_plane import ControlPlaneError, ControlPlaneStore, ROLES


logger = logging.getLogger(__name__)


class AdminCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str
    role: str


class AdminUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str | None = None
    enabled: bool | None = None


class AdminPasswordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str | None = Field(default=None, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class ClientPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_domain: str | None = Field(default=None, max_length=255)
    registration_mode: str | None = None
    web_chat_enabled: bool | None = None
    announcement: str | None = Field(default=None, max_length=500)
    # The plaintext token is accepted only for this request, validated against
    # ApiKeyStore, and reduced to a private key_id before persistence.
    api_key: str | None = Field(default=None, max_length=512)
    clear_api_key: bool | None = None


def install_control_plane_v1(app: FastAPI) -> FastAPI:
    if getattr(app.state, "control_plane_installed", False):
        return app
    store = ControlPlaneStore(app.state.settings.data_dir, app.state.settings.admin_username)
    audit = getattr(app.state, "admin_audit", None) or AdminAuditStore(app.state.settings.data_dir)
    app.state.control_plane = store
    app.state.admin_audit = audit
    app.state.control_plane_installed = True

    def audit_event(
        request: Request, action: str, target_type: str, target_id: str, details: dict, *, required: bool = True
    ) -> bool:
        try:
            audit.append(
                actor=str(getattr(request.state, "admin_username", "") or app.state.settings.admin_username),
                action=action, target_type=target_type, target_id=target_id,
                request_id=str(request.headers.get("x-request-id") or ""),
                details=details,
            )
        except AdminAuditError as exc:
            if required:
                raise HTTPException(status_code=503, detail="Administrator audit unavailable; change denied") from exc
            app.state.admin_audit_completion_pending = True
            logger.error(
                "Admin audit completion append failed after control-plane mutation: action=%s target_type=%s target_id=%s",
                action, target_type, target_id,
            )
            return False
        return True

    @app.get("/api/admin/control/roles")
    async def roles() -> dict:
        return {"policy_version": 1, "roles": {key: sorted(value) for key, value in ROLES.items()}}

    @app.get("/api/admin/control/admins")
    async def list_admins() -> dict:
        return {"data": [
            {"username": app.state.settings.admin_username, "role": "super_admin", "enabled": True, "source": "environment"},
            *store.list_admins(),
        ]}

    @app.post("/api/admin/control/admins", status_code=201)
    async def create_admin(body: AdminCreate, request: Request, response: Response) -> dict:
        audit_event(request, "admin.create_requested", "admin", body.username, {"role": body.role})
        try:
            admin = store.create_admin(body.username, body.password, body.role)
        except ControlPlaneError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_ok = audit_event(request, "admin.create_completed", "admin", body.username, {"role": body.role}, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"admin": admin}

    @app.patch("/api/admin/control/admins/{username}")
    async def update_admin(username: str, body: AdminUpdate, request: Request, response: Response) -> dict:
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(status_code=400, detail="No administrator change supplied")
        audit_event(request, "admin.update_requested", "admin", username, changes)
        try:
            admin = store.update_admin(username, **changes)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown administrator") from exc
        except ControlPlaneError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        revoked = 0
        if body.enabled is False:
            sessions = getattr(app.state, "admin_sessions", None)
            if sessions is not None:
                revoked = sessions.revoke_username(username)
        audit_ok = audit_event(request, "admin.update_completed", "admin", username, {**changes, "revoked_sessions": revoked}, required=False)
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"admin": admin, "revoked_sessions": revoked}

    @app.post("/api/admin/control/admins/{username}/password")
    async def rotate_admin_password(
        username: str, body: AdminPasswordUpdate, request: Request, response: Response
    ) -> dict:
        actor = str(getattr(request.state, "admin_username", "") or "")
        if username.casefold() == app.state.settings.admin_username.casefold():
            raise HTTPException(
                status_code=409,
                detail="The root administrator password is environment-managed",
            )
        actor_role = store.role_for(actor)
        if actor.casefold() != username.casefold():
            if actor_role != "super_admin":
                raise HTTPException(status_code=403, detail="Only the target administrator or root may rotate this password")
        elif not body.current_password or not store.verify_admin(username, body.current_password):
            raise HTTPException(status_code=403, detail="Current administrator password is incorrect")

        audit_event(
            request, "admin.password_rotation_requested", "admin", username,
            {"actor": actor, "self_service": actor.casefold() == username.casefold()},
        )
        try:
            admin = store.set_admin_password(username, body.new_password)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown administrator") from exc
        except ControlPlaneError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sessions = getattr(app.state, "admin_sessions", None)
        revoked = sessions.revoke_username(username) if sessions is not None else 0
        audit_ok = audit_event(
            request, "admin.password_rotation_completed", "admin", username,
            {"revoked_sessions": revoked}, required=False,
        )
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"admin": admin, "revoked_sessions": revoked}

    @app.post("/api/admin/control/self/password")
    async def rotate_own_admin_password(body: AdminPasswordUpdate, request: Request, response: Response) -> dict:
        actor = str(getattr(request.state, "admin_username", "") or "")
        if not actor or actor.casefold() == app.state.settings.admin_username.casefold():
            raise HTTPException(
                status_code=409,
                detail="The environment-managed root password must be rotated through deployment configuration",
            )
        if not body.current_password or not store.verify_admin(actor, body.current_password):
            raise HTTPException(status_code=403, detail="Current administrator password is incorrect")
        audit_event(
            request, "admin.self_password_rotation_requested", "admin", actor,
            {"self_service": True},
        )
        try:
            admin = store.set_admin_password(actor, body.new_password)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown administrator") from exc
        except ControlPlaneError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sessions = getattr(app.state, "admin_sessions", None)
        revoked = sessions.revoke_username(actor) if sessions is not None else 0
        audit_ok = audit_event(
            request, "admin.self_password_rotation_completed", "admin", actor,
            {"revoked_sessions": revoked}, required=False,
        )
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return {"admin": admin, "revoked_sessions": revoked}

    @app.get("/api/admin/control/client")
    async def get_client_policy() -> dict:
        result = store.public_client()
        key_id = store.client_api_key_id()
        key = app.state.api_keys.get_public(key_id) if key_id else None
        return {
            **result,
            "api_key_name": str((key or {}).get("name") or ""),
            "api_key_prefix": str((key or {}).get("prefix") or ""),
            "api_key_enabled": bool((key or {}).get("enabled")),
        }

    @app.put("/api/admin/control/client")
    async def update_client_policy(body: ClientPolicyUpdate, request: Request, response: Response) -> dict:
        supplied = body.model_dump(exclude_none=True, exclude_unset=True)
        token = supplied.pop("api_key", None)
        clear_api_key = bool(supplied.pop("clear_api_key", False))
        if token is not None and clear_api_key:
            raise HTTPException(status_code=400, detail="Cannot set and clear the shared API Key in one update")
        if token is not None:
            normalized_token = str(token).strip()
            if normalized_token:
                api_keys = getattr(app.state, "api_keys", None)
                principal = await api_keys.authenticate(normalized_token) if api_keys is not None else None
                if principal is None or principal.kind != "managed":
                    raise HTTPException(status_code=400, detail="API Key 无效、已停用或已过期")
                key_record = getattr(api_keys, "keys", {}).get(principal.key_id)
                if key_record is None or key_record.internal_owner_user_id:
                    raise HTTPException(status_code=400, detail="只能配置服务端已创建的托管 API Key")
                if "chat" not in principal.scopes or "models" not in principal.scopes:
                    raise HTTPException(status_code=400, detail="统一 API Key 必须具有 chat 和 models 权限")
                if key_record.token_quota is not None:
                    raise HTTPException(status_code=400, detail="统一 API Key 必须设置为不限 Token 额度")
                supplied["api_key_id"] = principal.key_id
        if clear_api_key:
            supplied["api_key_id"] = ""
        if not supplied:
            raise HTTPException(status_code=400, detail="Empty client policy update")

        # Never write the supplied secret or a bearer token into the audit log.
        audit_changes = {key: value for key, value in supplied.items() if key != "api_key_id"}
        if token is not None:
            audit_changes["api_key_configured"] = bool(str(token).strip())
        if clear_api_key:
            audit_changes["api_key_cleared"] = True
        audit_event(request, "client.policy_requested", "client", "default", audit_changes)
        try:
            result = store.update_client(supplied)
        except ControlPlaneError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_ok = audit_event(
            request, "client.policy_completed", "client", "default", audit_changes, required=False
        )
        response.headers["X-Admin-Audit-Status"] = "complete" if audit_ok else "completion-pending"
        return result

    @app.get("/api/client/config")
    async def public_client_config() -> dict:
        # Only public presentation/registration flags. No administrator metadata,
        # billing values, tokens, credential hashes or private tenant settings.
        return store.public_client()

    return app
