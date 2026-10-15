"""Backwards-compatible default-model routing for old gpt-5.5-mini clients.

The legacy ID is NOT an actual paid ChatGPT picker option. Use the currently
advertised, verified lowest default text model instead of rejecting the request.
Free accounts without a picker may still use their true automatic default.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import FastAPI

LEGACY_ID = "gpt-5.5-mini"
TEXT_ENDPOINTS = {"/v1/chat/completions", "/v1/completions", "/v1/responses"}
logger = logging.getLogger("chat2api.legacy_model_alias")


def current_default_model(registry: Any) -> str | None:
    """Select only a READY Worker's reported concrete model.

    Prefer an instant/non-reasoning entry to a dedicated Sol/Pro model. This
    selection is dynamic: no unavailable historical model is hardcoded as a
    replacement. The Free no-picker default is last resort.
    """
    try:
        catalog = registry.model_catalog(online_only=True)
    except Exception:
        catalog = []
    verified: list[str] = []
    free_fallback = False
    seen = set()
    for row in catalog or []:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "").strip().lower()
        if model_id == LEGACY_ID:
            free_fallback = bool(row.get("clients"))
            continue
        if (not row.get("clients") or row.get("validated") is not True
            or not re.fullmatch(r"gpt-[a-z0-9][a-z0-9.-]{0,80}", model_id)
            or model_id in seen):
            continue
        seen.add(model_id)
        verified.append(model_id)

    def order(model: str):
        # GPT-6 is the current instant picker entry in the supplied TX03
        # evidence. Discover replacements from live catalog, not a static
        # pretend capability for old 5.5-mini.
        if model == "gpt-6":
            return (0, model)
        if not any(part in model for part in ("-sol", "-pro", "-astra", "-high", "-thinking")):
            return (1, model)
        return (2, model)

    if verified:
        return min(verified, key=order)
    # Free no-picker Workers already route their actual current default via
    # existing free-default-compatibility-unverified handler.
    return LEGACY_ID if free_fallback else None


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
                        "message": "No online ChatGPT-ready Worker reports a current default text model; run Worker model discovery",
                        "type": "service_unavailable",
                        "code": "current_default_model_unavailable",
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
