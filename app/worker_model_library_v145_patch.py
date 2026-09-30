from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from starlette.routing import request_response

from . import admin as admin_module
from . import model_capability_routing_patch as model_routing
from .model_catalog import normalize_concrete_model_id

PATCH_ID = "worker-model-library-v145"
PATCH_REVISION = 145
VALIDATION_REVISION = 145
STATIC_SPECIAL_MODELS: dict[str, dict[str, Any]] = {
    "gpt-image": {
        "id": "gpt-image",
        "object": "model",
        "created": 0,
        "owned_by": "chat2api",
        "label": "ChatGPT Images browser route",
        "capabilities": ["image-generation", "image-reference"],
        "clients": [],
        "source": "special-static",
    },
    "gpt-live": {
        "id": "gpt-live",
        "object": "model",
        "created": 0,
        "owned_by": "chat2api",
        "label": "ChatGPT Voice / GPT-Live route",
        "capabilities": ["voice-generation", "voice-conversation", "text"],
        "clients": [],
        "source": "special-static",
    },
    "gpt-live-mini": {
        "id": "gpt-live-mini",
        "object": "model",
        "created": 0,
        "owned_by": "chat2api",
        "label": "ChatGPT Voice / GPT-Live mini route",
        "capabilities": ["voice-generation", "voice-conversation", "text"],
        "clients": [],
        "source": "special-static",
    },
}


def _model_id(raw: Any) -> str | None:
    if isinstance(raw, dict):
        raw = raw.get("id") or raw.get("model") or raw.get("family")
    return normalize_concrete_model_id(raw)


def _validated_model_rows(registry: Any, client_id: str) -> list[dict[str, Any]]:
    client = getattr(registry, "clients", {}).get(str(client_id))
    metadata = getattr(client, "metadata", None) if client else None
    metadata = metadata if isinstance(metadata, dict) else {}
    try:
        validation_revision = int(metadata.get("model_validation_revision") or 0)
    except (TypeError, ValueError):
        validation_revision = 0
    if metadata.get("model_validation_state") != "validated" or validation_revision < VALIDATION_REVISION:
        return []
    values = metadata.get("models")
    if not isinstance(values, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, dict) or raw.get("validated") is not True:
            continue
        try:
            revision = int(raw.get("validation_revision") or raw.get("validation_version") or 0)
        except (TypeError, ValueError):
            revision = 0
        if revision < VALIDATION_REVISION:
            continue
        model_id = _model_id(raw)
        if not model_id or model_id in STATIC_SPECIAL_MODELS or model_id in seen:
            continue
        seen.add(model_id)
        item = dict(raw)
        item["id"] = model_id
        item.setdefault("label", model_id)
        item.setdefault("capabilities", ["text", "vision", "file-understanding"])
        item["source"] = "worker-validated"
        result.append(item)
    return result


def _mark_pending(registry: Any, client_id: str, trigger: str) -> None:
    client = getattr(registry, "clients", {}).get(str(client_id))
    if not client:
        return
    metadata = getattr(client, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        client.metadata = metadata
    metadata["models"] = []
    metadata["current_model"] = None
    metadata["model_validation_state"] = "pending"
    metadata["model_validation_trigger"] = trigger
    metadata["model_validation_revision"] = VALIDATION_REVISION
    metadata["model_validation_error"] = None


def _patch_console_terms() -> None:
    html = admin_module.ADMIN_HTML
    html = html.replace("模型广场", "模型库").replace("模型目录", "模型库")
    admin_module.ADMIN_HTML = html


def _replace_route_endpoint(route: APIRoute, endpoint: Callable[..., Any]) -> None:
    route.endpoint = endpoint
    route.dependant.call = endpoint
    route.app = request_response(route.get_route_handler())


def _pricing_by_model(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict):
            continue
        model_id = normalize_concrete_model_id(raw.get("model_id") or raw.get("id"))
        if model_id:
            result[model_id] = dict(raw)
    return result


def _public_user_library(registry: Any, pricing_payload: dict[str, Any]) -> list[dict[str, Any]]:
    prices = _pricing_by_model(pricing_payload)
    rows: list[dict[str, Any]] = []
    for model in registry.model_catalog(online_only=True):
        if not isinstance(model, dict):
            continue
        model_id = str(model.get("id") or "").strip().lower()
        if not model_id:
            continue
        price = prices.get(model_id, {})
        capabilities = list(model.get("capabilities") or [])
        special = model_id in STATIC_SPECIAL_MODELS
        rows.append({
            "model_id": model_id,
            "name": str(price.get("name") or model.get("label") or model_id),
            "enabled": True,
            "input_usd_per_million": float(price.get("input_usd_per_million") or 0),
            "cached_input_usd_per_million": float(price.get("cached_input_usd_per_million") or 0),
            "output_usd_per_million": float(price.get("output_usd_per_million") or 0),
            "price_configured": bool(price),
            "source": str(model.get("source") or ("special-static" if special else "worker-validated")),
            "capabilities": capabilities,
            "available_workers": len(model.get("clients") or []),
            "validated": bool(model.get("validated")) if not special else None,
            "validated_at": model.get("validated_at"),
            "playground_eligible": bool(not special and "text" in capabilities),
        })
    return rows


def _patch_user_console_routes(app: FastAPI, registry: Any) -> None:
    for route in list(app.routes):
        if not isinstance(route, APIRoute):
            continue
        methods = set(route.methods or set())
        if route.path == "/api/user/models" and "GET" in methods:
            base_endpoint = route.endpoint

            async def user_models_from_worker_library(request: Request, _base=base_endpoint):
                payload = _base(request)
                if inspect.isawaitable(payload):
                    payload = await payload
                if not isinstance(payload, dict):
                    return payload
                result = dict(payload)
                result["data"] = _public_user_library(registry, payload)
                result["library_authority"] = PATCH_ID
                result["library_revision"] = PATCH_REVISION
                result["normal_model_source"] = "worker-discovery-and-validation"
                return result

            _replace_route_endpoint(route, user_models_from_worker_library)

        if route.path == "/assets/chat2api-user-console-v104.js" and "GET" in methods:
            base_endpoint = route.endpoint

            async def user_console_js_model_library(_base=base_endpoint):
                response = _base()
                if inspect.isawaitable(response):
                    response = await response
                if not isinstance(response, Response):
                    return response
                text = bytes(getattr(response, "body", b"") or b"").decode("utf-8", errors="replace")
                text = text.replace("模型广场", "模型库")
                text = text.replace("可用模型与当前价格", "Worker 验证可用模型与当前价格")
                old = '$("playModel").innerHTML = state.models.length ? state.models.map((row) =>'
                new = 'const playModels = state.models.filter((row) => row.playground_eligible !== false);\n    $("playModel").innerHTML = playModels.length ? playModels.map((row) =>'
                text = text.replace(old, new)
                text = text.replace("每 1M Token · 管理员可调整", "每 1M Token · 模型可用性由 Worker 验证")
                headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() not in {"content-length", "content-type"}
                }
                headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                return Response(text, status_code=response.status_code, media_type="application/javascript", headers=headers)

            _replace_route_endpoint(route, user_console_js_model_library)


def install_worker_model_library_v145_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "worker_model_library_v145_installed", False):
        return app

    registry = app.state.registry
    base_attach = registry.attach
    base_register = registry.register
    base_set_connection_enabled = registry.set_connection_enabled

    async def attach_with_validation_reset(client_id: str, websocket: Any) -> None:
        _mark_pending(registry, client_id, "connect")
        await base_attach(client_id, websocket)

    async def register_with_validation_reset(*args: Any, **kwargs: Any):
        client_id, token = await base_register(*args, **kwargs)
        _mark_pending(registry, client_id, "bind")
        await registry.save()
        return client_id, token

    async def set_connection_enabled_with_validation_reset(client_id: str, enabled: bool):
        if enabled:
            _mark_pending(registry, client_id, "enable")
        item = await base_set_connection_enabled(client_id, enabled)
        if enabled:
            await registry.save()
        return item

    def client_models_validated(client_id: str) -> list[dict[str, Any]]:
        return _validated_model_rows(registry, client_id)

    def supports_model_strict(client_id: str, model_id: str) -> bool:
        model = normalize_concrete_model_id(model_id)
        if not model:
            return False
        if model in STATIC_SPECIAL_MODELS:
            return True
        return any(str(item.get("id") or "") == model for item in _validated_model_rows(registry, client_id))

    def model_library(online_only: bool = True) -> list[dict[str, Any]]:
        catalog = {key: {**value, "clients": []} for key, value in STATIC_SPECIAL_MODELS.items()}
        if online_only:
            client_ids = [client_id for client_id in registry.online_client_ids() if registry.chatgpt_routing_ready(client_id)]
        else:
            client_ids = [client_id for client_id, item in registry.clients.items() if getattr(item, "connection_enabled", False)]
        for client_id in client_ids:
            for special in catalog.values():
                if client_id not in special["clients"]:
                    special["clients"].append(client_id)
            for model in _validated_model_rows(registry, client_id):
                model_id = str(model["id"])
                entry = catalog.setdefault(
                    model_id,
                    {
                        "id": model_id,
                        "object": "model",
                        "created": 0,
                        "owned_by": "chat2api",
                        "label": model.get("label") or model_id,
                        "capabilities": model.get("capabilities") or ["text", "vision", "file-understanding"],
                        "family": model.get("family") or model_id,
                        "reasoning": model.get("reasoning"),
                        "reasoning_efforts": model.get("reasoning_efforts") or [],
                        "clients": [],
                        "source": "worker-validated",
                        "validated": True,
                    },
                )
                if client_id not in entry["clients"]:
                    entry["clients"].append(client_id)
                if model.get("validated_at"):
                    entry["validated_at"] = model.get("validated_at")
                if model.get("selected"):
                    entry["selected_on"] = client_id
        special_order = {"gpt-image": 0, "gpt-live": 1, "gpt-live-mini": 2}
        return sorted(catalog.values(), key=lambda item: (special_order.get(str(item.get("id")), 10), str(item.get("id") or "")))

    def is_library_text_model(model: str) -> bool:
        value = str(model or "").strip().lower()
        return bool(value) and value not in STATIC_SPECIAL_MODELS

    def compatible_strict(registry_obj: Any, client_id: str, model: str) -> bool:
        checker = getattr(registry_obj, "chatgpt_routing_ready", None)
        if callable(checker):
            try:
                if not bool(checker(client_id)):
                    return False
            except Exception:
                return False
        value = str(model or "").strip().lower()
        if value in STATIC_SPECIAL_MODELS:
            return True
        return supports_model_strict(client_id, value)

    registry.attach = attach_with_validation_reset
    registry.register = register_with_validation_reset
    registry.set_connection_enabled = set_connection_enabled_with_validation_reset
    registry.client_models = client_models_validated
    registry.supports_model = supports_model_strict
    registry.model_catalog = model_library
    model_routing._is_dynamic_text_model = is_library_text_model
    model_routing._compatible = compatible_strict
    _patch_console_terms()
    _patch_user_console_routes(app, registry)

    app.state.worker_model_library_v145_installed = True
    app.state.worker_model_library_revision = PATCH_REVISION
    app.state.worker_model_library_authority = PATCH_ID
    return app


__all__ = [
    "PATCH_ID",
    "PATCH_REVISION",
    "VALIDATION_REVISION",
    "STATIC_SPECIAL_MODELS",
    "_validated_model_rows",
    "_mark_pending",
    "install_worker_model_library_v145_patch",
]
