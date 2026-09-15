from __future__ import annotations

import json
import secrets
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from . import mini_multimodal_quota_patch as mini_quota
from . import v13_patch


PATCH_ID = "model-capability-routing-v3-dynamic"
# Compatibility constants are kept for older patches/tests. Routing is no longer
# limited to this static set: every text model advertised by a ready Worker is a
# first-class route target.
PAID_TEXT_MODELS = {"gpt-5.6-sol", "gpt-5.5"}
MINI_MODEL = "gpt-5.5-mini"
SPECIAL_MODELS = {"default", "chatgpt-web", "gpt-image", "gpt-live", "gpt-live-mini"}
_MODEL_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "chat2api_model_capability_routing_context",
    default=None,
)


def _account_type(registry: Any, client_id: str) -> str:
    item = registry.clients.get(str(client_id))
    metadata = getattr(item, "metadata", None) if item else None
    value = str((metadata or {}).get("account_type") or "unknown").strip().lower()
    if value in {"plus", "pro"}:
        return value
    if value == "paid":
        # Historical Bridges reported all subscriptions as paid. Treat that as
        # Plus for routing/presentation compatibility until the Bridge can
        # distinguish a Pro account explicitly.
        return "plus"
    return value if value == "free" else "unknown"


def _advertised_models(registry: Any, client_id: str) -> set[str]:
    try:
        values = registry.client_models(client_id)
    except Exception:
        values = []
    result: set[str] = set()
    for value in values or []:
        if isinstance(value, dict):
            raw = value.get("id") or value.get("model") or value.get("family") or ""
        else:
            raw = value
        model_id = str(raw or "").strip().lower()
        if model_id:
            result.add(model_id)
    return result


def _is_dynamic_text_model(model: str) -> bool:
    value = str(model or "").strip().lower()
    return bool(value) and value not in SPECIAL_MODELS


def _compatible(registry: Any, client_id: str, model: str) -> bool:
    checker = getattr(registry, "chatgpt_routing_ready", None)
    if callable(checker):
        try:
            if not bool(checker(client_id)):
                return False
        except Exception:
            return False
    else:
        item = getattr(registry, "clients", {}).get(str(client_id))
        metadata = getattr(item, "metadata", None) if item else None
        from .login_readiness import chatgpt_routing_ready
        if not chatgpt_routing_ready(metadata):
            return False

    model = str(model or "").strip().lower()
    account = _account_type(registry, client_id)
    advertised = _advertised_models(registry, client_id)

    if model == MINI_MODEL:
        if model in advertised:
            return True
        if account == "free":
            return not advertised or "gpt-5.5" in advertised
        return not advertised or "gpt-5.5" in advertised

    if _is_dynamic_text_model(model):
        # The browser's live model picker is the capability authority. A Worker
        # that explicitly advertises a model may serve it even if an older plan
        # detector still says free/paid. This prevents stale account metadata
        # from rejecting a real Plus/Pro capability and makes future models work
        # without a server release.
        if advertised:
            return model in advertised
        # No catalog means an old Bridge. Preserve the historical paid-family
        # fallback only for models we already knew; unknown future models require
        # positive discovery so they cannot be sent blindly.
        if model in PAID_TEXT_MODELS:
            return account != "free"
        return False

    return True


def _description(registry: Any, client_id: str) -> str:
    account = _account_type(registry, client_id)
    models = sorted(_advertised_models(registry, client_id))
    return f"{client_id}[account={account}, models={','.join(models) or 'unknown'}]"


def _idle_compatible(registry: Any, model: str) -> list[str]:
    online = list(registry.online_client_ids())
    if not online:
        raise ConnectionError("No Chrome extension is online. Open Chrome with a paired chat2api extension.")
    idle = [client_id for client_id in online if client_id not in registry.busy_clients]
    if not idle:
        raise LookupError("All online Chrome extensions are busy")
    return [client_id for client_id in idle if _compatible(registry, client_id, model)]


def _select_mini(registry: Any, *, needs_multimodal: bool) -> str:
    eligible = _idle_compatible(registry, MINI_MODEL)
    if not eligible:
        raise ConnectionError("No compatible Chrome extension is available for gpt-5.5-mini")
    free = [client_id for client_id in eligible if _account_type(registry, client_id) == "free"]
    fallback = [client_id for client_id in eligible if _account_type(registry, client_id) != "free"]
    if not needs_multimodal:
        candidates = free or fallback
        if candidates:
            return secrets.choice(candidates)
        raise ConnectionError("No compatible Chrome extension is available for gpt-5.5-mini")
    free_ready = [client_id for client_id in free if mini_quota._multimodal_available(registry, client_id)]
    if free_ready:
        return secrets.choice(free_ready)
    if fallback:
        return secrets.choice(fallback)
    cooling_free = [client_id for client_id in free if not mini_quota._multimodal_available(registry, client_id)]
    if cooling_free:
        restore_times = sorted(
            value for value in (mini_quota._cooldown_until_ms(registry, client_id) for client_id in cooling_free) if value > 0
        )
        restore = mini_quota._iso_from_ms(restore_times[0]) if restore_times else None
        raise ConnectionError(
            "All available ChatGPT Free gpt-5.5-mini vision/file quotas are cooling down"
            + (f" until {restore}" if restore else "")
        )
    raise ConnectionError("No compatible Chrome extension is available for gpt-5.5-mini multimodal input")


class _ModelRequestContextMiddleware:
    """Own the final model target around the actual API endpoint call."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/v1/chat/completions":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                await self.app(scope, receive, send)
                return
            chunks.append(bytes(message.get("body") or b""))
            if not message.get("more_body", False):
                break
        raw = b"".join(chunks)
        sent_body = False

        async def replay_receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        target: dict[str, Any] | None = None
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            if isinstance(payload, dict):
                raw_model = str(payload.get("model") or "").strip().lower()
                try:
                    target = dict(v13_patch._target_from_payload(payload))
                except (ValueError, TypeError, KeyError):
                    target = {}
                if raw_model:
                    target["model"] = raw_model
                target["needs_multimodal"] = bool(target.get("needs_multimodal")) or mini_quota._needs_multimodal(payload)
        except (UnicodeDecodeError, ValueError, TypeError):
            target = None
        local_token = _MODEL_CONTEXT.set(target)
        historical_token = v13_patch._target_context.set(target)
        try:
            await self.app(scope, replay_receive, send)
        finally:
            v13_patch._target_context.reset(historical_token)
            _MODEL_CONTEXT.reset(local_token)


def install_model_capability_routing_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "model_capability_routing_patch_installed", False):
        return app
    registry = app.state.registry
    base_resolve_client = registry.resolve_client

    def resolve_client_with_model_capability(requested: str | None) -> str:
        target = _MODEL_CONTEXT.get() or v13_patch._target_context.get() or {}
        model = str(target.get("model") or "").strip().lower()
        if not _is_dynamic_text_model(model):
            return base_resolve_client(requested)
        if requested:
            selected = base_resolve_client(requested)
            if not _compatible(registry, selected, model):
                raise LookupError(f"Requested Worker is not compatible with {model}: {_description(registry, selected)}")
            if model == MINI_MODEL and bool(target.get("needs_multimodal")):
                if _account_type(registry, selected) == "free" and not mini_quota._multimodal_available(registry, selected):
                    until_ms = mini_quota._cooldown_until_ms(registry, selected)
                    raise LookupError(
                        "Requested ChatGPT Free extension has gpt-5.5-mini vision/file quota cooling down"
                        + (f" until {mini_quota._iso_from_ms(until_ms)}" if until_ms else "")
                    )
            return selected
        if model == MINI_MODEL:
            return _select_mini(registry, needs_multimodal=bool(target.get("needs_multimodal")))
        eligible = _idle_compatible(registry, model)
        if not eligible:
            online_idle = [client_id for client_id in registry.online_client_ids() if client_id not in registry.busy_clients]
            available = "; ".join(_description(registry, client_id) for client_id in online_idle)
            raise ConnectionError(
                f"No online Worker advertises requested model {model}. Available: {available or 'none'}. "
                "Refresh/login the Worker so its live ChatGPT model catalog is reported."
            )
        key_id = registry.routing_key_context.get()
        if key_id:
            previous = registry.api_key_routes.get(key_id)
            if previous in eligible:
                return previous
        selected = secrets.choice(eligible)
        registry._remember_route(key_id, selected)
        return selected

    registry.resolve_client = resolve_client_with_model_capability
    app.add_middleware(_ModelRequestContextMiddleware)
    app.state.model_capability_routing_patch_installed = True
    app.state.model_capability_routing_patch_id = PATCH_ID
    return app
