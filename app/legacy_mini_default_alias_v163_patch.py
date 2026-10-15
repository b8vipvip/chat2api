"""Backwards-compatible default-model routing for old gpt-5.5-mini clients.

The legacy ID is NOT an actual paid ChatGPT picker option. Use the currently
advertised, verified lowest default text model instead of rejecting the request.
Free accounts without a picker may still use their true automatic default.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI

LEGACY_ID = "gpt-5.5-mini"
TEXT_ENDPOINTS = {"/v1/chat/completions", "/v1/completions", "/v1/responses"}
logger = logging.getLogger("chat2api.legacy_model_alias")


def current_default_model(registry: Any) -> str | None:
    """Only the administrator's explicit minimum model can resolve the legacy ID.

    Model availability is read from the existing managed Worker catalog. No
    model ranking, plan guessing, or alternative fallback is permitted here.
    """
    getter = getattr(registry, "legacy_minimum_model", None)
    selected = getter() if callable(getter) else None
    if not selected:
        return None
    try:
        catalog = registry.model_catalog(online_only=True)
    except Exception:
        return None
    return selected if any(
        isinstance(row, dict)
        and row.get("id") == selected
        and row.get("clients")
        and row.get("source") != "special-static"
        for row in catalog or []
    ) else None

class LegacyMiniDefaultAliasMiddleware:
    def __init__(self, app, registry):
        self.app = app
        self.registry = registry

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or scope.get("method") != "POST"
            or scope.get("path") not in TEXT_ENDPOINTS):
            await self.app(scope, receive, send)
            return
        raw_chunks = []
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                # Do not replay a partially read body as a new request.
                return
            raw_chunks.append(bytes(message.get("body") or b""))
            if not message.get("more_body", False):
                break
        raw = b"".join(raw_chunks)
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError, UnicodeError):
            payload = None
        remapped = False
        if isinstance(payload, dict) and str(payload.get("model") or "").strip().lower() == LEGACY_ID:
            target = current_default_model(self.registry)
            if target and target != LEGACY_ID:
                payload["model"] = target
                raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                scope["chat2api_legacy_requested_model"] = LEGACY_ID
                scope["chat2api_effective_model"] = target
                remapped = True
                logger.info("Remapped retired %s to current Worker default %s", LEGACY_ID, target)
            elif not target:
                data = json.dumps({
                    "error": {
                        "message": "No administrator-configured minimum text model is currently available on a ready Worker; configure it under Text Model Management",
                        "type": "service_unavailable",
                        "code": "configured_minimum_model_unavailable",
                    }
                }).encode("utf-8")
                await send({"type": "http.response.start", "status": 503,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
                await send({"type": "http.response.body", "body": data})
                return
        if remapped:
            scope["headers"] = [
                (key, str(len(raw)).encode("ascii")) if key.lower() == b"content-length" else (key, val)
                for key, val in scope.get("headers", [])
            ]
        forwarded = False

        async def replay():
            nonlocal forwarded
            if not forwarded:
                forwarded = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def install_legacy_mini_default_alias(app: FastAPI) -> FastAPI:
    if getattr(app.state, "legacy_mini_default_alias_installed", False):
        return app
    app.add_middleware(LegacyMiniDefaultAliasMiddleware, registry=app.state.registry)
    app.state.legacy_mini_default_alias_installed = True
    return app
