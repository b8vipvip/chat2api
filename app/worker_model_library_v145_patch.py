from __future__ import annotations

from typing import Any

from fastapi import FastAPI

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
    values = (metadata or {}).get("models")
    if not isinstance(values, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, dict):
            continue
        if raw.get("validated") is not True:
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
    # Never carry model authority across a bind/enable/connect/restart boundary.
    # The Worker must republish a freshly validated list before it is eligible.
    metadata["models"] = []
    metadata["current_model"] = None
    metadata["model_validation_state"] = "pending"
    metadata["model_validation_trigger"] = trigger
    metadata["model_validation_revision"] = VALIDATION_REVISION


def _patch_console_terms() -> None:
    html = admin_module.ADMIN_HTML
    html = html.replace("模型广场", "模型库").replace("模型目录", "模型库")
    admin_module.ADMIN_HTML = html


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
        # Voice and image generation are the only static exceptions. Every other
        # model row is a union of fresh, validated Worker capability reports.
        catalog = {key: {**value, "clients": []} for key, value in STATIC_SPECIAL_MODELS.items()}
        if online_only:
            client_ids = [
                client_id
                for client_id in registry.online_client_ids()
                if registry.chatgpt_routing_ready(client_id)
            ]
        else:
            client_ids = [
                client_id
                for client_id, item in registry.clients.items()
                if getattr(item, "connection_enabled", False)
            ]
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
                validated_at = model.get("validated_at")
                if validated_at:
                    entry["validated_at"] = validated_at
                if model.get("selected"):
                    entry["selected_on"] = client_id
        special_order = {"gpt-image": 0, "gpt-live": 1, "gpt-live-mini": 2}
        return sorted(
            catalog.values(),
            key=lambda item: (special_order.get(str(item.get("id")), 10), str(item.get("id") or "")),
        )

    def is_library_text_model(model: str) -> bool:
        # default/chatgpt-web are legacy aliases, not discovered models. Treat
        # them as normal text requests so they fail closed unless a concrete
        # validated model is requested. Only voice/image keep static bypasses.
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

    # model_capability_routing_patch resolves these module-globals at request
    # time. Replacing both removes mini/account/empty-catalog fallbacks and makes
    # the live model library the single normal-model dispatch authority.
    model_routing._is_dynamic_text_model = is_library_text_model
    model_routing._compatible = compatible_strict

    # The user console historically listed every configured price row. Keep the
    # price configuration as metadata, but expose only the current model library.
    pricing = getattr(app.state, "user_pricing", None)
    if pricing is not None and callable(getattr(pricing, "public", None)):
        base_pricing_public = pricing.public

        def public_pricing_from_model_library() -> dict[str, Any]:
            payload = dict(base_pricing_public())
            configured = {
                str(row.get("model_id") or "").strip().lower(): dict(row)
                for row in payload.get("models") or []
                if isinstance(row, dict) and str(row.get("model_id") or "").strip()
            }
            rows: list[dict[str, Any]] = []
            for item in model_library(online_only=True):
                model_id = str(item.get("id") or "").strip()
                if not model_id:
                    continue
                price = dict(configured.get(model_id.lower()) or {})
                price.update({
                    "model_id": model_id,
                    "name": str(price.get("name") or item.get("label") or model_id),
                    "enabled": True,
                    "source": item.get("source"),
                    "validated": bool(item.get("validated")),
                    "worker_count": len(item.get("clients") or []),
                })
                price.setdefault("input_usd_per_million", 0.0)
                price.setdefault("cached_input_usd_per_million", 0.0)
                price.setdefault("output_usd_per_million", 0.0)
                rows.append(price)
            payload["models"] = rows
            payload["model_library_revision"] = PATCH_REVISION
            payload["model_library_authority"] = PATCH_ID
            return payload

        pricing.public = public_pricing_from_model_library

    _patch_console_terms()

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
