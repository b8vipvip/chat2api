"""Admin read/reconcile API for the C-06a reservation journal."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, StrictInt

from .user_commerce import WalletReservationError
from .admin_audit import AdminAuditError, AdminAuditStore
from .admin_auth import SESSION_COOKIE
from .quota_reservations import QuotaReservationError, QuotaReservationStore


class WalletRefundRequest(BaseModel):
    refund_id: str = Field(pattern=r"^rf_[A-Za-z0-9_-]{8,100}$")
    amount_cents: StrictInt = Field(gt=0, le=10_000_000)
    reason: str = Field(min_length=5, max_length=500)


def install_quota_reservation_admin_v1(app: FastAPI) -> FastAPI:
    if getattr(app.state, "quota_reservation_admin_v1_installed", False):
        return app
    store = getattr(app.state, "quota_reservations", None)
    if store is None:
        store = QuotaReservationStore(app.state.settings.data_dir)
        app.state.quota_reservations = store
    api_keys = getattr(app.state, "api_keys", None)
    if api_keys is None:
        raise RuntimeError("Quota reservation admin API requires the existing ApiKeyStore")
    audit = getattr(app.state, "admin_audit", None) or AdminAuditStore(app.state.settings.data_dir)
    app.state.admin_audit = audit
    app.state.quota_reservation_runtime_enabled = bool(
        getattr(app.state, "quota_reservation_runtime_enabled", False)
    )

    def admin_actor(request: Request, *, write: bool = False) -> str:
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

    def audit_event(actor: str, action: str, request: Request, details: dict[str, Any]) -> None:
        try:
            audit.append(
                actor=actor,
                action=action,
                target_type="quota_reservation_journal",
                target_id="global",
                request_id=str(request.headers.get("x-request-id") or ""),
                details=details,
            )
        except AdminAuditError as exc:
            raise HTTPException(status_code=503, detail="Administrator audit unavailable; operation denied") from exc

    @app.get("/api/admin/quota-reservations")
    async def list_quota_reservations(
        request: Request,
        state: str | None = None,
        key_id: str | None = None,
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, Any]:
        admin_actor(request)
        try:
            rows = store.list_reservations(state=state, key_id=key_id, limit=limit)
        except QuotaReservationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        active = sum(
            int(row["reserved_tokens"])
            for row in rows if row["state"] in {"reserved", "committing"}
        )
        return {
            "data": rows,
            "active_reserved_tokens_in_result": active,
            "runtime_integration_enabled": bool(app.state.quota_reservation_runtime_enabled),
            "runtime_coverage": list(getattr(app.state, "quota_reservation_runtime_coverage", [])),
            "accounting_owner": "ApiKeyStore.account_completed_usage",
        }

    @app.post("/api/admin/quota-reservations/reconcile")
    async def reconcile_quota_reservations(request: Request) -> dict[str, int]:
        actor = admin_actor(request, write=True)
        audit_event(actor, "quota.reservation_reconcile_requested", request, {})
        try:
            replayed = await store.recover_committing(getattr(app.state, "telemetry", None), api_keys)
            reconciled = store.reconcile(api_keys)
            count = replayed + reconciled
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Quota reservation reconciliation failed") from exc
        audit_event(actor, "quota.reservation_reconcile_completed", request, {
            "settled": count, "replayed_from_telemetry": replayed, "reconciled_from_api_key_ledger": reconciled,
        })
        return {"settled": count}

    @app.post("/api/admin/billing/charges/{request_id}/refund")
    async def refund_internal_wallet_charge(
        request_id: str, body: WalletRefundRequest, request: Request,
    ) -> dict[str, Any]:
        actor = admin_actor(request, write=True)
        # Only the server-owned BillingStore ledger may credit a wallet.
        # Never accept user_id or a new balance from the caller.
        billing = getattr(app.state, "user_billing", None)
        if billing is None:
            raise HTTPException(status_code=503, detail="Billing ledger is unavailable")
        try:
            audit.append(
                actor=actor, action="billing.wallet_refund_requested",
                target_type="billing_charge", target_id=request_id,
                request_id=str(request.headers.get("x-request-id") or ""),
                details={"refund_id": body.refund_id, "amount_cents": body.amount_cents},
            )
        except AdminAuditError as exc:
            raise HTTPException(status_code=503, detail="Administrator audit unavailable; refund denied") from exc
        try:
            refund = billing.refund_charge(
                request_id, body.refund_id, body.amount_cents, body.reason,
            )
        except WalletReservationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="Wallet refund storage is unavailable") from exc
        try:
            audit.append(
                actor=actor, action="billing.wallet_refund_completed",
                target_type="billing_charge", target_id=request_id,
                request_id=str(request.headers.get("x-request-id") or ""),
                details={
                    "refund_id": body.refund_id,
                    "user_id": refund["user_id"],
                    "amount_cents": refund["amount_cents"],
                },
            )
        except AdminAuditError as exc:
            # The refund is already durable. The caller must retry the exact
            # same refund_id, never generate a new idempotency key.
            raise HTTPException(status_code=503, detail="Refund persisted but completion audit is unavailable; retry the same refund_id") from exc
        return {"ok": True, "refund": refund}

    app.state.quota_reservation_admin_v1_installed = True
    return app
