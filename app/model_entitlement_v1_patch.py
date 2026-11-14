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
from .user_subscriptions import UserSubscriptionError, UserSubscriptionStore
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


class UserSystemRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_role: str = Field(min_length=1, max_length=64)


class SubscriptionPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True


class UserSubscriptionAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=1, max_length=64)
    expires_at: str | None = Field(default=None, max_length=64)


def install_model_entitlements(app: FastAPI) -> FastAPI:
    if getattr(app.state, "model_entitlements_installed", False):
        return app
    registry = app.state.registry
    store = ModelEntitlementStore(app.state.settings.data_dir)
    accounts = getattr(app.state, "user_accounts", None)
    subscriptions = UserSubscriptionStore(app.state.settings.data_dir)
    app.state.user_subscriptions = subscriptions
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

    def audit_event(
        actor: str, action: str, subject_type: str, subject_id: str,
        details: dict[str, Any], *, target_type: str = "model_entitlement",
    ) -> None:
        try:
            audit.append(
                actor=actor, action=action, target_type=target_type,
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
        if subject_type == "user_role":
            try:
                accounts.validate_system_role(subject_id)
            except (AttributeError, ValueError) as exc:
                raise ModelEntitlementError("Unknown end-user system role") from exc
            return
        if subject_type == "subscription_plan":
            try:
                exists = subscriptions.plan_exists(subject_id)
            except UserSubscriptionError as exc:
                raise ModelEntitlementError("Invalid subscription plan ID") from exc
            if not exists:
                raise KeyError("Unknown subscription plan")
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

    def _user_policy_context(user_id: str | None) -> tuple[str | None, str | None]:
        if not user_id:
            return None, None
        try:
            system_role = accounts.system_role_for(str(user_id))
            subscription = subscriptions.current_plan_for_user(str(user_id))
            plan_id = str(subscription.get("plan_id") or "")
            if not plan_id:
                raise RuntimeError("User subscription has no effective plan")
            return system_role, plan_id
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "model_entitlement_user_policy_context_unavailable"},
            ) from exc

    def _decision(key_id: str | None, model_id: str | None) -> dict[str, Any]:
        if not key_id or key_id == "master":
            return {"allowed": True, "reason": "master_key", "model": model_id, "matched_rules": []}
        owner_id = _identity(key_id)
        tenant_id, tenant_role = _tenant_context_for_key(key_id, owner_id)
        user_role, subscription_plan = _user_policy_context(owner_id)
        return store.evaluate(
            str(model_id or ""),
            key_id=str(key_id),
            user_id=owner_id,
            tenant_id=tenant_id,
            tenant_role=tenant_role,
            user_role=user_role,
            subscription_plan=subscription_plan,
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
            return JSONResponse(
                {"detail": "User session is unavailable"},
                status_code=401,
                headers={"Cache-Control": "private, no-store"},
            )
        try:
            payload = json.loads((await _response_bytes(response)).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            return JSONResponse(
                {"detail": {"code": "model_entitlement_catalog_unavailable"}},
                status_code=503,
                headers={"Cache-Control": "private, no-store"},
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return JSONResponse(
                {"detail": {"code": "model_entitlement_catalog_unavailable"}},
                status_code=503,
                headers={"Cache-Control": "private, no-store"},
            )

        user_id = str(user.get("user_id") or "")
        try:
            user_role, subscription_plan = _user_policy_context(user_id)
        except HTTPException as exc:
            return JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers={"Cache-Control": "private, no-store"},
            )
        selected_key_id = str(request.query_params.get("key_id") or "").strip()
        if selected_key_id:
            try:
                owns_selected_key = accounts.owns_key(user_id, selected_key_id)
            except Exception:
                owns_selected_key = False
            if not owns_selected_key:
                return JSONResponse(
                    {"detail": "API key not found"},
                    status_code=404,
                    headers={"Cache-Control": "private, no-store"},
                )

        filtered: list[dict[str, Any]] = []
        try:
            for row in payload["data"]:
                if not isinstance(row, dict):
                    continue
                # The production pricing catalog uses model_id. Keep id/model
                # aliases for compatible test and older catalog providers.
                model_id = str(
                    row.get("model_id") or row.get("id") or row.get("model") or ""
                ).strip()
                if not model_id:
                    continue
                decision = (
                    _decision(selected_key_id, model_id)
                    if selected_key_id
                    else store.evaluate(
                        model_id, key_id=None, user_id=user_id,
                        user_role=user_role, subscription_plan=subscription_plan,
                    )
                )
                if decision.get("allowed") is True:
                    filtered.append(row)
        except HTTPException as exc:
            return JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers={"Cache-Control": "private, no-store"},
            )
        except Exception:
            return JSONResponse(
                {"detail": {"code": "model_entitlement_policy_unavailable"}},
                status_code=503,
                headers={"Cache-Control": "private, no-store"},
            )

        payload["data"] = filtered
        return JSONResponse(
            payload,
            status_code=response.status_code,
            headers={"Cache-Control": "private, no-store"},
        )

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

    @app.get("/api/admin/subscription-plans")
    async def list_subscription_plans(request: Request) -> dict[str, Any]:
        admin_actor(request)
        return {"data": subscriptions.list_plans()}

    @app.put("/api/admin/subscription-plans/{plan_id}")
    async def put_subscription_plan(
        plan_id: str, body: SubscriptionPlanUpdate, request: Request,
    ) -> dict[str, Any]:
        actor = admin_actor(request)
        audit_event(
            actor, "subscription.plan_update_requested", "subscription_plan", plan_id,
            {"name": body.name, "enabled": body.enabled}, target_type="subscription_plan",
        )
        try:
            plan = subscriptions.upsert_plan(plan_id, body.name, enabled=body.enabled)
        except UserSubscriptionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_event(
            actor, "subscription.plan_update_completed", "subscription_plan", plan_id,
            {"name": plan["name"], "enabled": plan["enabled"]},
            target_type="subscription_plan",
        )
        return {"plan": plan}

    @app.put("/api/admin/users/{user_id}/role")
    async def put_user_system_role(
        user_id: str, body: UserSystemRoleUpdate, request: Request,
    ) -> dict[str, Any]:
        actor = admin_actor(request)
        if accounts.admin_get(user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            role = accounts.validate_system_role(body.system_role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            previous_role = accounts.system_role_for(user_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="User role authority unavailable") from exc
        audit_event(
            actor, "user.system_role_update_requested", "user", user_id,
            {"previous_system_role": previous_role, "system_role": role},
            target_type="user",
        )
        try:
            user = accounts.set_system_role(user_id, role)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="User not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_event(
            actor, "user.system_role_update_completed", "user", user_id,
            {"previous_system_role": previous_role, "system_role": role},
            target_type="user",
        )
        return {"user": user}

    @app.get("/api/admin/users/{user_id}/subscription")
    async def get_user_subscription(user_id: str, request: Request) -> dict[str, Any]:
        admin_actor(request)
        if accounts.admin_get(user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "current": subscriptions.current_plan_for_user(user_id),
            "history": subscriptions.history_for_user(user_id),
        }

    @app.put("/api/admin/users/{user_id}/subscription")
    async def put_user_subscription(
        user_id: str, body: UserSubscriptionAssign, request: Request,
    ) -> dict[str, Any]:
        actor = admin_actor(request)
        if accounts.admin_get(user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            previous_plan = subscriptions.current_plan_for_user(user_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Subscription authority unavailable") from exc
        details = {
            "previous_plan_id": previous_plan.get("plan_id"),
            "plan_id": body.plan_id,
            "expires_at": body.expires_at,
        }
        audit_event(
            actor, "subscription.assignment_requested", "user_subscription", user_id,
            details, target_type="user_subscription",
        )
        try:
            subscription = subscriptions.assign(
                user_id, body.plan_id, expires_at=body.expires_at, assigned_by=actor,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except UserSubscriptionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_event(
            actor, "subscription.assignment_completed", "user_subscription", user_id,
            {
                "subscription_id": subscription["subscription_id"],
                "previous_plan_id": previous_plan.get("plan_id"),
                "plan_id": subscription["plan_id"],
            },
            target_type="user_subscription",
        )
        return {"subscription": subscription}

    @app.delete("/api/admin/users/{user_id}/subscription")
    async def delete_user_subscription(user_id: str, request: Request) -> dict[str, Any]:
        actor = admin_actor(request)
        if accounts.admin_get(user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            previous_plan = subscriptions.current_plan_for_user(user_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Subscription authority unavailable") from exc
        audit_event(
            actor, "subscription.cancellation_requested", "user_subscription", user_id,
            {"previous_plan_id": previous_plan.get("plan_id")},
            target_type="user_subscription",
        )
        cancelled = subscriptions.cancel_active(user_id)
        audit_event(
            actor, "subscription.cancellation_completed", "user_subscription", user_id,
            {"cancelled": cancelled, "previous_plan_id": previous_plan.get("plan_id")},
            target_type="user_subscription",
        )
        return {"cancelled": bool(cancelled)}

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
