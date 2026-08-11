from __future__ import annotations

import contextvars
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.12.0"
_routing_context: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "chat2api_routing_context", default=None
)


def install_v12_patch(app: FastAPI) -> FastAPI:
    app.version = PATCH_VERSION
    settings = app.state.settings
    registry = app.state.registry
    api_keys = app.state.api_keys

    if not getattr(registry, "_chat2api_v12_send_wrapped", False):
        base_send = registry.send

        async def send_with_routing(client_id: str, payload: dict[str, Any]):
            routing = _routing_context.get()
            if routing and payload.get("type") in {"chat.request", "image.request", "voice.request"}:
                payload = {**payload, "routing": dict(routing)}
            return await base_send(client_id, payload)

        registry.send = send_with_routing
        registry._chat2api_v12_send_wrapped = True

    def supplied_token(request: Request) -> str:
        supplied = (request.headers.get("x-api-key") or "").strip()
        authorization = request.headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        return supplied

    async def routing_identity(request: Request) -> dict[str, str] | None:
        if not request.url.path.startswith("/v1/"):
            return None
        supplied = supplied_token(request)
        if not supplied:
            return None
        if settings.api_key and secrets.compare_digest(supplied, settings.api_key):
            return {"api_key_id": "master", "api_key_kind": "master"}
        principal = await api_keys.authenticate(supplied)
        if not principal:
            return None
        return {"api_key_id": principal.key_id, "api_key_kind": principal.kind}

    async def response_bytes(response) -> bytes:
        body = getattr(response, "body", None)
        if body is not None:
            return bytes(body)
        chunks: list[bytes] = []
        iterator = getattr(response, "body_iterator", None)
        if iterator is not None:
            async for chunk in iterator:
                chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
        return b"".join(chunks)

    @app.get("/assets/chat2api-v12.js")
    async def admin_v12_js() -> Response:
        path = Path(__file__).with_name("admin_v12.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def v12_routing_context_and_version(request: Request, call_next):
        identity = await routing_identity(request)
        token = _routing_context.set(identity)
        try:
            response = await call_next(request)
        finally:
            _routing_context.reset(token)

        path = request.url.path
        content_type = response.headers.get("content-type", "")
        if path in {"/", "/healthz", "/api/admin/overview"} and "application/json" in content_type:
            raw = await response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["per_api_key_conversation_routing"] = True
                        capabilities["conversation_idle_window_cleanup"] = True
                        capabilities["conversation_load_budget"] = True
            headers = {
                k: v for k, v in response.headers.items()
                if k.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v12.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                k: v for k, v in response.headers.items()
                if k.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
