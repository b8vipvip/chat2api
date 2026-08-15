from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import v13_patch


PATCH_VERSION = "0.21.4"
MINI_MODEL = "gpt-5.5-mini"
ADMIN_ASSET = "/assets/chat2api-model-contract-v21-4.js"
MODEL_KEYS = {
    "id",
    "model",
    "requested_model",
    "logical_model",
    "effective_model",
    "fallback_model",
}


def canonical_model_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return re.sub(r"\s+", "-", raw)


def _ensure_mini_capabilities(row: dict[str, Any]) -> dict[str, Any]:
    model_id = canonical_model_id(row.get("id") or row.get("model") or "")
    if model_id != MINI_MODEL:
        return row
    capabilities = list(row.get("capabilities") or [])
    for capability in ("text", "vision", "file-understanding"):
        if capability not in capabilities:
            capabilities.append(capability)
    row["capabilities"] = capabilities
    return row


def _normalize_payload_models(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_payload_models(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in MODEL_KEYS and isinstance(item, str) and item.strip().lower().startswith("gpt"):
            normalized[key] = canonical_model_id(item)
        else:
            normalized[key] = _normalize_payload_models(item)
    return _ensure_mini_capabilities(normalized)


async def _response_bytes(response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def install_v21_4_model_contract_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    if getattr(registry, "_chat2api_v21_4_model_contract", False):
        return app

    app.version = PATCH_VERSION

    base_target_from_payload = v13_patch._target_from_payload

    def target_from_payload_v21_4(payload: dict[str, Any]) -> dict[str, Any]:
        value = dict(payload or {})
        if "model" in value:
            value["model"] = canonical_model_id(value.get("model"))
        return base_target_from_payload(value)

    v13_patch._target_from_payload = target_from_payload_v21_4

    base_model_catalog = registry.model_catalog

    def model_catalog_v21_4(online_only: bool = True) -> list[dict[str, Any]]:
        rows = []
        seen: set[str] = set()
        for raw in base_model_catalog(online_only=online_only):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            model_id = canonical_model_id(row.get("id") or "")
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            row["id"] = model_id
            rows.append(_ensure_mini_capabilities(row))
        return rows

    registry.model_catalog = model_catalog_v21_4

    base_send = registry.send

    async def send_with_canonical_model_ids(client_id: str, payload: dict[str, Any]) -> None:
        value = dict(payload or {})
        if str(value.get("type") or "") in {"chat.request", "image.request", "voice.request", "voice.live.start"}:
            value = _normalize_payload_models(value)
        await base_send(client_id, value)

    registry.send = send_with_canonical_model_ids
    registry._chat2api_v21_4_model_contract = True

    @app.get(ADMIN_ASSET)
    async def admin_v21_4_js() -> Response:
        path = Path(__file__).with_name("admin_v21_4.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def v21_4_model_contract_response(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = f'<script src="{ADMIN_ASSET}"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        if "application/json" not in content_type:
            return response
        if not (
            path == "/v1/models"
            or path.startswith("/v1/models/")
            or path == "/api/extensions/model-affinity"
            or path == "/api/admin/overview"
        ):
            return response

        raw = await _response_bytes(response)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return Response(raw, status_code=response.status_code, media_type="application/json")

        normalized = _normalize_payload_models(payload)
        if isinstance(normalized, dict) and path == "/api/admin/overview":
            normalized["version"] = PATCH_VERSION
            capabilities = normalized.setdefault("capabilities", {})
            if isinstance(capabilities, dict):
                capabilities["canonical_hyphenated_model_ids"] = True
                capabilities["gpt_5_5_mini_vision"] = True
                capabilities["gpt_5_5_mini_file_understanding"] = True

        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store"
        return JSONResponse(normalized, status_code=response.status_code, headers=headers)

    return app
