from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import v13_patch


PATCH_VERSION = "0.20.0"
MINI_MODEL = "gpt-5.5-mini"
PAID_TEXT_MODELS = {"gpt-5.6-sol", "gpt-5.5"}


def _account_type(registry, client_id: str) -> str:
    client = registry.clients.get(client_id)
    metadata = getattr(client, "metadata", None) if client else None
    value = str((metadata or {}).get("account_type") or "unknown").strip().lower()
    return value if value in {"free", "paid"} else "unknown"


def _response_bytes_sync_candidate(response):
    return getattr(response, "body", None)


async def _response_bytes(response) -> bytes:
    body = _response_bytes_sync_candidate(response)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def install_v20_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    app.version = PATCH_VERSION

    # v13 owns the canonical OpenAI-compatible model/reasoning normalization.
    # Extend it at runtime instead of duplicating the public endpoints, so Chat
    # Completions, Responses and legacy Completions all gain mini support together.
    if not getattr(v13_patch, "_chat2api_v20_mini_normalizer", False):
        base_normalize_model = v13_patch._normalize_model
        base_target_from_payload = v13_patch._target_from_payload

        def normalize_model_v20(value: Any) -> str:
            raw = str(value or v13_patch.DEFAULT_TEXT_MODEL).strip().lower()
            if raw == MINI_MODEL:
                return MINI_MODEL
            return base_normalize_model(value)

        def target_from_payload_v20(payload: dict[str, Any]) -> dict[str, Any]:
            model = normalize_model_v20(payload.get("model") or v13_patch.DEFAULT_TEXT_MODEL)
            if model == MINI_MODEL:
                # Free accounts expose no selectable family/reasoning controls.
                # The browser bridge therefore uses the account's native default.
                # When routed to a non-Free extension the bridge itself maps this
                # logical model to GPT-5.5 + low/instant reasoning.
                return {
                    "model": MINI_MODEL,
                    "reasoning_level": None,
                    "reasoning_effort": None,
                    "mini_default": True,
                }
            return base_target_from_payload(payload)

        v13_patch._normalize_model = normalize_model_v20
        v13_patch._target_from_payload = target_from_payload_v20
        v13_patch._chat2api_v20_mini_normalizer = True

    base_summaries = registry.summaries
    if not getattr(registry, "_chat2api_v20_summaries", False):
        def summaries_v20() -> list[dict[str, Any]]:
            rows = list(base_summaries())
            for row in rows:
                metadata = row.get("metadata") if isinstance(row, dict) else None
                value = str((metadata or {}).get("account_type") or "unknown").strip().lower()
                row["account_type"] = value if value in {"free", "paid"} else "unknown"
            return rows

        registry.summaries = summaries_v20
        registry._chat2api_v20_summaries = True

    base_model_catalog = registry.model_catalog
    if not getattr(registry, "_chat2api_v20_model_catalog", False):
        def model_catalog_v20(online_only: bool = True) -> list[dict[str, Any]]:
            rows = [dict(row) for row in base_model_catalog(online_only=online_only)]
            client_ids = registry.online_client_ids() if online_only else [
                client_id for client_id, item in registry.clients.items() if item.connection_enabled
            ]
            free_clients = [client_id for client_id in client_ids if _account_type(registry, client_id) == "free"]
            fallback_clients = [client_id for client_id in client_ids if _account_type(registry, client_id) != "free"]
            mini = {
                "id": MINI_MODEL,
                "object": "model",
                "created": 0,
                "owned_by": "chat2api",
                "label": "GPT-5.5 Mini · Free 默认",
                "capabilities": ["text"],
                "reasoning_efforts": [],
                "clients": list(client_ids),
                "native_free_clients": free_clients,
                "fallback_clients": fallback_clients,
                "routing": {
                    "preferred_account_type": "free",
                    "free_ui_selection": False,
                    "fallback_model": "gpt-5.5",
                    "fallback_reasoning_effort": "low",
                    "fallback_reasoning_label": "极速",
                },
            }
            rows = [row for row in rows if str(row.get("id") or "") != MINI_MODEL]
            insert_at = next(
                (index + 1 for index, row in enumerate(rows) if str(row.get("id") or "") == "gpt-5.5"),
                len(rows),
            )
            rows.insert(insert_at, mini)
            return rows

        registry.model_catalog = model_catalog_v20
        registry._chat2api_v20_model_catalog = True

    base_resolve_client = registry.resolve_client
    if not getattr(registry, "_chat2api_v20_free_routing", False):
        def resolve_client_v20(requested: str | None) -> str:
            target = v13_patch._target_context.get()
            target_model = str((target or {}).get("model") or "")
            if target_model not in PAID_TEXT_MODELS | {MINI_MODEL}:
                return base_resolve_client(requested)

            def validate_explicit(client_id: str) -> str:
                if client_id not in registry.clients:
                    raise KeyError("Unknown client_id")
                client = registry.clients[client_id]
                if not client.connection_enabled:
                    raise ConnectionError("Requested Chrome extension is disabled by administrator")
                if client_id not in registry.sockets:
                    raise ConnectionError("Requested Chrome extension is offline")
                if client_id in registry.busy_clients:
                    raise LookupError("Requested Chrome extension is busy")
                if target_model in PAID_TEXT_MODELS and _account_type(registry, client_id) == "free":
                    raise LookupError(
                        f"Requested extension uses a ChatGPT Free account; use {MINI_MODEL} for this extension"
                    )
                return client_id

            if requested:
                return validate_explicit(requested)

            online = registry.online_client_ids()
            if not online:
                raise ConnectionError("No Chrome extension is online. Open Chrome with a paired chat2api extension.")
            idle = [client_id for client_id in online if client_id not in registry.busy_clients]
            if not idle:
                raise LookupError("All online Chrome extensions are busy")

            if target_model == MINI_MODEL:
                free = [client_id for client_id in idle if _account_type(registry, client_id) == "free"]
                if free:
                    # Mini routing is intentionally model-specific and does not
                    # overwrite the API-key sticky route used by paid text models.
                    return secrets.choice(free)
                fallback = [client_id for client_id in idle if _account_type(registry, client_id) != "free"]
                if fallback:
                    return secrets.choice(fallback)
                raise ConnectionError("No compatible Chrome extension is available for gpt-5.5-mini")

            eligible = [client_id for client_id in idle if _account_type(registry, client_id) != "free"]
            if not eligible:
                raise ConnectionError(
                    f"Only ChatGPT Free extensions are online; use {MINI_MODEL} or connect a paid-account extension"
                )
            key_id = registry.routing_key_context.get()
            if key_id:
                previous = registry.api_key_routes.get(key_id)
                if previous in eligible:
                    return previous
            selected = secrets.choice(eligible)
            registry._remember_route(key_id, selected)
            return selected

        registry.resolve_client = resolve_client_v20
        registry._chat2api_v20_free_routing = True

    base_send = registry.send
    if not getattr(registry, "_chat2api_v20_mini_send", False):
        async def send_with_mini_route(client_id: str, payload: dict[str, Any]) -> None:
            target = v13_patch._target_context.get()
            if payload.get("type") == "chat.request" and str((target or {}).get("model") or "") == MINI_MODEL:
                free = _account_type(registry, client_id) == "free"
                options = dict(payload.get("options") or {})
                options.update(
                    {
                        "logical_model": MINI_MODEL,
                        "mini_route": "free-native" if free else "gpt-5.5-instant-fallback",
                        "selected_account_type": _account_type(registry, client_id),
                        "fallback_model": None if free else "gpt-5.5",
                        "fallback_reasoning_level": None if free else "instant",
                        "fallback_reasoning_effort": None if free else "low",
                    }
                )
                payload = {**payload, "options": options}
            await base_send(client_id, payload)

        registry.send = send_with_mini_route
        registry._chat2api_v20_mini_send = True

    @app.get("/assets/chat2api-v20.js")
    async def admin_v20_js() -> Response:
        path = Path(__file__).with_name("admin_v20.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def v20_free_account_console(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type and (
            path in {"/", "/healthz", "/api/admin/overview"}
            or path.startswith("/api/admin/")
        ):
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["chatgpt_free_account_detection"] = True
                        capabilities["gpt_5_5_mini_free_routing"] = True
                        capabilities["gpt_5_5_mini_paid_fallback"] = True
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v20.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
