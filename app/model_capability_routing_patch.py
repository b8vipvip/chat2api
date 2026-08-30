from __future__ import annotations

import json
import secrets
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from . import v13_patch


PATCH_ID = "model-capability-routing-v1"
PAID_TEXT_MODELS = {"gpt-5.6-sol", "gpt-5.5"}
MINI_MODEL = "gpt-5.5-mini"
_MODEL_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "chat2api_model_capability_routing_context",
    default=None,
)


def _account_type(registry: Any, client_id: str) -> str:
    item = registry.clients.get(str(client_id))
    metadata = getattr(item, "metadata", None) if item else None
    value = str((metadata or {}).get("account_type") or "unknown").strip().lower()
    return value if value in {"free", "paid"} else "unknown"


def _advertised_models(registry: Any, client_id: str) -> set[str]:
    try:
        values = registry.client_models(client_id)
    except Exception:
        values = []
    return {str(value or "").strip().lower() for value in values if str(value or "").strip()}


def _compatible(registry: Any, client_id: str, model: str) -> bool:
    model = str(model or "").strip().lower()
    account = _account_type(registry, client_id)
    advertised = _advertised_models(registry, client_id)

    if model in PAID_TEXT_MODELS:
        # A Worker explicitly identified as ChatGPT Free cannot expose a paid
        # family that requires the model picker. This is the fail-closed boundary
        # that prevents a 30-second browser wait on a control that cannot exist.
        if account == "free":
            return False
        # New Workers report their concrete model catalog. Respect it exactly.
        # Legacy paid/unknown Workers without a catalog remain compatible so an
        # old bridge is not made unusable merely by upgrading the server.
        return not advertised or model in advertised

    if model == MINI_MODEL:
        if account == "free":
            return True
        # Paid/unknown Workers may serve mini through the documented gpt-5.5
        # instant fallback. A bridge that explicitly advertises neither is not
        # a valid fallback candidate.
        return not advertised or MINI_MODEL in advertised or "gpt-5.5" in advertised

    return True


def _description(registry: Any, client_id: str) -> str:
    account = _account_type(registry, client_id)
    models = sorted(_advertised_models(registry, client_id))
    return f"{client_id}[account={account}, models={','.join(models) or 'unknown'}]"


class _ModelRequestContextMiddleware:
    """Set the final model target in the same ASGI task as the endpoint.

    The historical v13 target middleware is layered through Starlette's
    BaseHTTPMiddleware stack. This direct ASGI middleware deliberately restores
    the request body and sets the target ContextVar immediately around the final
    application call, so model-aware routing does not depend on middleware task
    boundaries or patch ordering.
    """

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
                target = dict(v13_patch._target_from_payload(payload))
                # The final boundary must understand the public model literal
                # even in isolated tests or transitional runtimes where an older
                # target normalizer has not yet been decorated with mini support.
                raw_model = str(payload.get("model") or "").strip().lower()
                if raw_model in PAID_TEXT_MODELS | {MINI_MODEL}:
                    target["model"] = raw_model
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
        if model not in PAID_TEXT_MODELS | {MINI_MODEL}:
            return base_resolve_client(requested)

        if requested:
            selected = base_resolve_client(requested)
            if not _compatible(registry, selected, model):
                raise LookupError(
                    f"Requested Worker is not compatible with {model}: {_description(registry, selected)}"
                )
            return selected

        # Preserve the existing mini resolver, including Free preference,
        # multimodal quota cooldown and paid-account fallback. The ASGI context
        # above makes its historical target lookup deterministic again.
        if model == MINI_MODEL:
            selected = base_resolve_client(None)
            if not _compatible(registry, selected, model):
                raise LookupError(
                    f"Selected Worker is not compatible with {model}: {_description(registry, selected)}"
                )
            return selected

        online = list(registry.online_client_ids())
        if not online:
            raise ConnectionError("No Chrome extension is online. Open Chrome with a paired chat2api extension.")
        idle = [client_id for client_id in online if client_id not in registry.busy_clients]
        if not idle:
            raise LookupError("All online Chrome extensions are busy")

        eligible = [client_id for client_id in idle if _compatible(registry, client_id, model)]
        if not eligible:
            available = "; ".join(_description(registry, client_id) for client_id in idle)
            raise ConnectionError(
                f"No online Worker is compatible with {model}. Available: {available}. "
                f"ChatGPT Free Workers can serve {MINI_MODEL} only; connect a paid/unknown Worker that advertises {model}."
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
