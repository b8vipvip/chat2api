"""Session-bound Web Chat identity backed by one server-managed shared API key.

The shared key authorizes the Web Chat service. A per-account key ID remains the
internal ownership namespace for files, request history, usage and wallet
reservations; no API secret is ever sent to the browser.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .api_keys import ApiPrincipal
from .user_commerce import USER_SESSION_COOKIE


def _private_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers={"Cache-Control": "private, no-store", "Vary": "Cookie"},
    )


async def resolve_web_chat_principal(
    request: Any,
    requested_user_key_id: str | None = None,
) -> tuple[dict[str, Any], ApiPrincipal]:
    """Authenticate the user session and resolve the configured shared key.

    requested_user_key_id is an opaque, non-secret per-user ownership ID used
    to keep old conversations and their attachments bound to their original
    owner namespace. It is never treated as an authentication credential.
    """
    app = request.app
    accounts = getattr(app.state, "user_accounts", None)
    api_keys = getattr(app.state, "api_keys", None)
    control_plane = getattr(app.state, "control_plane", None)
    if accounts is None or api_keys is None or control_plane is None:
        raise _private_error(503, "Web Chat 服务尚未完成服务端配置")

    user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
    if not user:
        raise _private_error(401, "请先登录 Chat2API 账户")
    client_policy = control_plane.public_client()
    if client_policy.get("web_chat_enabled") is not True:
        raise _private_error(403, "Web Chat 当前已由管理员停用")
    if user.get("is_guest") and client_policy.get("guest_enabled") is not True:
        raise _private_error(403, "游客使用已关闭，请登录账户")

    configured_key_id = control_plane.client_api_key_id()
    if not configured_key_id:
        raise _private_error(503, "管理员尚未配置 Web Chat 统一 API Key")

    shared = await api_keys.authenticate_key_id(configured_key_id)
    if shared is None:
        raise _private_error(503, "Web Chat 统一 API Key 已失效或不可用，请联系管理员")
    # The shared Mode 1 service key must be server-managed, not a key created
    # under any end-user account. This check also protects installations whose
    # client policy predates the stricter configuration validation.
    shared_record = getattr(api_keys, "keys", {}).get(configured_key_id)
    if (
        shared_record is None
        or shared_record.internal_owner_user_id
        or accounts.owner_for_key(configured_key_id)
    ):
        raise _private_error(
            503,
            "Web Chat 统一 API Key 必须是未绑定用户账户的服务端托管 Key；用户创建的 API Key 仅用于 Mode 2",
        )
    if "chat" not in shared.scopes or "models" not in shared.scopes:
        raise _private_error(503, "Web Chat 统一 API Key 缺少 chat 或 models 权限")

    user_id = str(user.get("user_id") or "")
    requested = str(requested_user_key_id or "").strip()
    if requested:
        if not accounts.owns_key(user_id, requested):
            raise _private_error(404, "Web Chat 账户身份不存在")
        requested_record = getattr(api_keys, "keys", {}).get(requested)
        if requested_record is None:
            raise _private_error(404, "Web Chat 账户身份不存在")
        if requested_record.internal_owner_user_id and requested_record.internal_owner_user_id != user_id:
            raise _private_error(404, "Web Chat 账户身份不存在")
        identity_key_id = requested
    else:
        try:
            internal = await api_keys.ensure_web_chat_user_key(user_id)
            accounts.attach_key(user_id, internal.key_id)
        except (KeyError, ValueError, OSError) as error:
            raise _private_error(503, "无法初始化 Web Chat 账户身份") from error
        identity_key_id = internal.key_id

    # A hidden Web Chat identity must inherit the account's workspace binding;
    # otherwise model-entitlement evaluation would see it as an unbound key and
    # could omit tenant-specific restrictions. If the user belongs to multiple
    # workspaces, use the first stable (slug-sorted) membership as the default
    # Web Chat workspace. Existing conversations keep their own bound key IDs.
    identity_record = getattr(api_keys, "keys", {}).get(identity_key_id)
    if identity_record is not None and identity_record.internal_owner_user_id == user_id:
        tenant_memberships = getattr(app.state, "tenant_memberships", None)
        if tenant_memberships is not None:
            try:
                bound_tenant = tenant_memberships.tenant_for_key(identity_key_id)
                if bound_tenant:
                    if tenant_memberships.user_for_key(identity_key_id) != user_id:
                        raise ValueError("Web Chat identity tenant owner mismatch")
                    if not identity_record.tenant_binding_initialized:
                        async with api_keys.lock:
                            identity_record.tenant_binding_initialized = True
                            await api_keys.save()
                elif not identity_record.tenant_binding_initialized:
                    memberships = tenant_memberships.list_for_user(user_id)
                    if memberships:
                        tenant_memberships.bind_key(
                            str(memberships[0]["tenant_id"]), user_id, identity_key_id
                        )
                        async with api_keys.lock:
                            identity_record.tenant_binding_initialized = True
                            await api_keys.save()
            except Exception as error:
                raise _private_error(503, "无法初始化 Web Chat 工作区身份") from error

    principal = ApiPrincipal(
        key_id=identity_key_id,
        name="Web Chat",
        kind="web_chat",
        scopes=tuple(shared.scopes),
        auth_key_id=shared.key_id,
    )
    registry = getattr(app.state, "registry", None)
    if registry is not None:
        # Match the normal managed-key authentication path: routing and
        # account/tenant entitlements follow the user identity, while
        # service-key-specific model restrictions are evaluated separately.
        registry.set_routing_key(identity_key_id)
        shared_key_context = getattr(registry, "shared_api_key_context", None)
        if shared_key_context is not None:
            shared_key_context.set(shared.key_id)
    return user, principal
