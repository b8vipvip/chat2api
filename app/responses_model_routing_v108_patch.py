from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from . import model_capability_routing_patch as model_routing
from . import v13_patch


PATCH_REVISION = 108
DEFAULT_RESPONSES_MODEL = "gpt-5.6-sol"


class _ResponsesModelContextMiddleware:
    """Provide the existing model-capability resolver with a Responses target.

    The historical routing middleware only owns /v1/chat/completions. Responses
    requests still resolve through the same ClientRegistry, so they must carry the
    requested model in the same ContextVar while the endpoint dispatches.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/v1/responses":
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
                model = str(payload.get("model") or DEFAULT_RESPONSES_MODEL).strip().lower()
                target = {"model": model, "needs_multimodal": False}
        except (UnicodeDecodeError, ValueError, TypeError):
            target = None

        local_token = model_routing._MODEL_CONTEXT.set(target)
        historical_token = v13_patch._target_context.set(target)
        try:
            await self.app(scope, replay_receive, send)
        finally:
            v13_patch._target_context.reset(historical_token)
            model_routing._MODEL_CONTEXT.reset(local_token)


def install_responses_model_routing_v108_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_model_routing_v108_installed", False):
        return app
    app.add_middleware(_ResponsesModelContextMiddleware)
    app.state.responses_model_routing_v108_installed = True
    return app
