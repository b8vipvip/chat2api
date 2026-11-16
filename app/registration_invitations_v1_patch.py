"""Admin API for email-bound, single-use registration invitations (C-04)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .admin_audit import AdminAuditError, AdminAuditStore
from .admin_auth import same_origin_admin_request
from .registration_invitations import InvitationError, InvitationStore


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    expires_in_seconds: int = Field(default=7 * 24 * 60 * 60, ge=300, le=30 * 24 * 60 * 60)


def install_registration_invitations(app: FastAPI) -> FastAPI:
    if getattr(app.state, "registration_invitations_installed", False):
        return app
    store = InvitationStore(app.state.settings.data_dir)
    audit = getattr(app.state, "admin_audit", None) or AdminAuditStore(app.state.settings.data_dir)
    app.state.invitations = store
    app.state.admin_audit = audit
    app.state.registration_invitations_installed = True

    def audit_requested(request: Request, action: str, target_id: str, details: dict) -> None:
        try:
            audit.append(
                actor=str(getattr(request.state, "admin_username", "") or app.state.settings.admin_username),
                action=action + "_requested",
                target_type="registration_invitation",
                target_id=target_id,
                request_id=str(request.headers.get("x-request-id") or ""),
                details=details,
            )
        except AdminAuditError as exc:
            raise HTTPException(
                status_code=503,
                detail="Administrator audit unavailable; invitation change denied",
            ) from exc

    @app.get("/api/admin/control/invitations")
    async def list_invitations(limit: int = Query(default=100, ge=1, le=200)) -> dict:
        return {"data": store.list_invitations(limit=limit)}

    @app.post("/api/admin/control/invitations", status_code=201)
    async def create_invitation(body: InvitationCreate, request: Request) -> dict:
        if not same_origin_admin_request(request):
            raise HTTPException(status_code=403, detail="Invitation changes require a same-origin request")
        control = getattr(app.state, "control_plane", None)
        if control is None or control.public_client()["registration_mode"] != "invite_only":
            raise HTTPException(status_code=409, detail="Set registration mode to invite_only before issuing invitations")
        actor = str(getattr(request.state, "admin_username", "") or "")
        audit_requested(
            request, "invitation.create", body.email.casefold(),
            {"email": body.email.casefold(), "expires_in_seconds": body.expires_in_seconds},
        )
        try:
            result = store.create(
                body.email, actor,
                expires_in_seconds=body.expires_in_seconds,
            )
        except InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

    @app.post("/api/admin/control/invitations/{invitation_id}/revoke")
    async def revoke_invitation(invitation_id: str, request: Request) -> dict:
        if not same_origin_admin_request(request):
            raise HTTPException(status_code=403, detail="Invitation changes require a same-origin request")
        audit_requested(request, "invitation.revoke", invitation_id, {})
        if not store.revoke(invitation_id):
            raise HTTPException(status_code=404, detail="Invitation is unavailable or already terminal")
        return {"ok": True, "invitation_id": invitation_id, "status": "revoked"}

    return app
