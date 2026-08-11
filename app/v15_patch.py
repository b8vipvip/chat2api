from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import v13_patch


PATCH_VERSION = "0.15.0"
DEFAULT_REASONING_LEVEL = "medium"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_REASONING_LABEL = "中"
TEXT_MODELS = {"gpt-5.6-sol", "gpt-5.5"}


def _is_supplied(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _target_from_payload_v15(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize OpenAI-compatible model/reasoning selection.

    Omitted reasoning is intentionally deterministic: every text request defaults
    to medium instead of inheriting whatever reasoning state happens to be visible
    in the current ChatGPT composer.
    """

    model = v13_patch._normalize_model(payload.get("model") or v13_patch.DEFAULT_TEXT_MODEL)
    direct = payload.get("reasoning_effort")
    nested = payload.get("reasoning")
    nested_value = nested.get("effort") if isinstance(nested, dict) else None

    direct_supplied = _is_supplied(direct)
    nested_supplied = _is_supplied(nested_value)
    if direct_supplied and nested_supplied:
        direct_pair = v13_patch._normalize_reasoning(direct)
        nested_pair = v13_patch._normalize_reasoning(nested_value)
        if direct_pair != nested_pair:
            raise ValueError("reasoning_effort and reasoning.effort specify different values")
        reasoning_level, reasoning_effort = direct_pair
    elif nested_supplied:
        reasoning_level, reasoning_effort = v13_patch._normalize_reasoning(nested_value)
    elif direct_supplied:
        reasoning_level, reasoning_effort = v13_patch._normalize_reasoning(direct)
    else:
        reasoning_level, reasoning_effort = DEFAULT_REASONING_LEVEL, DEFAULT_REASONING_EFFORT

    return {
        "model": model,
        "reasoning_level": reasoning_level,
        "reasoning_effort": reasoning_effort,
    }


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


def install_v15_patch(app: FastAPI) -> FastAPI:
    app.version = PATCH_VERSION

    # v13 routes and middleware resolve this function from their module globals
    # at request time, so replacing it here upgrades Chat Completions, Responses
    # and legacy Completions consistently without duplicating those endpoints.
    v13_patch._target_from_payload = _target_from_payload_v15

    registry = app.state.registry
    if not getattr(registry, "_chat2api_v15_catalog_wrapped", False):
        base_catalog = registry.model_catalog

        def model_catalog_v15(online_only: bool = True) -> list[dict[str, Any]]:
            rows = [dict(row) for row in base_catalog(online_only=online_only)]
            for row in rows:
                if str(row.get("id") or "") in TEXT_MODELS:
                    row["default_reasoning_effort"] = DEFAULT_REASONING_EFFORT
                    row["default_reasoning_label"] = DEFAULT_REASONING_LABEL
            return rows

        registry.model_catalog = model_catalog_v15
        registry._chat2api_v15_catalog_wrapped = True

    @app.get("/assets/chat2api-v15.js")
    async def admin_v15_js() -> Response:
        path = Path(__file__).with_name("admin_v15.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def v15_default_reasoning_and_version(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type and (
            path in {"/", "/healthz", "/api/admin/overview"}
            or (path.startswith("/api/admin/requests/") and path.endswith("/log"))
        ):
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                if path in {"/", "/healthz", "/api/admin/overview"}:
                    payload["version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["default_reasoning_effort"] = DEFAULT_REASONING_EFFORT
                        capabilities["default_reasoning_label"] = DEFAULT_REASONING_LABEL
                        capabilities["omitted_reasoning_is_deterministic"] = True
                if path.startswith("/api/admin/requests/") and path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION
            headers = {
                key: value for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v15.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
