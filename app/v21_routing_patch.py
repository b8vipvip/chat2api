from __future__ import annotations

from typing import Any

from fastapi import FastAPI


ROUTED_REQUEST_TYPES = {"chat.request", "image.request", "voice.request", "voice.live.start"}


def install_v21_routing_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    if getattr(registry, "_chat2api_v21_routing_payload", False):
        return app

    base_send = registry.send

    async def send_with_request_routing(client_id: str, payload: dict[str, Any]) -> None:
        value = dict(payload or {})
        if str(value.get("type") or "") in ROUTED_REQUEST_TYPES:
            routing = dict(value.get("routing") or {})
            key_id = str(routing.get("api_key_id") or registry.routing_key_context.get() or "").strip()
            if key_id:
                routing["api_key_id"] = key_id
                value["routing"] = routing
        await base_send(client_id, value)

    registry.send = send_with_request_routing
    registry._chat2api_v21_routing_payload = True
    return app
