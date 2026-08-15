from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import live_voice_patch, v21_patch


PATCH_VERSION = "0.21.1"
DEFAULT_MAX_CONCURRENCY = 3
MIN_MAX_CONCURRENCY = 1
MAX_MAX_CONCURRENCY = 32
CONFIG_FILENAME = "concurrency.json"
ROUTED_REQUEST_TYPES = {"chat.request", "image.request", "voice.request", "voice.live.start"}


class ConcurrencyUpdate(BaseModel):
    max_concurrency: int = Field(
        default=DEFAULT_MAX_CONCURRENCY,
        ge=MIN_MAX_CONCURRENCY,
        le=MAX_MAX_CONCURRENCY,
    )


def _normalize_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENCY
    return max(MIN_MAX_CONCURRENCY, min(MAX_MAX_CONCURRENCY, parsed))


def _load_limit(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return DEFAULT_MAX_CONCURRENCY
    if not isinstance(payload, dict):
        return DEFAULT_MAX_CONCURRENCY
    return _normalize_limit(payload.get("max_concurrency"))


def _save_limit(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "unified",
                "max_concurrency": int(value),
                "request_weight": 1,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


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


def _config_payload(limit: int) -> dict[str, Any]:
    return {
        "max_concurrency": int(limit),
        "default_max_concurrency": DEFAULT_MAX_CONCURRENCY,
        "min_max_concurrency": MIN_MAX_CONCURRENCY,
        "max_max_concurrency": MAX_MAX_CONCURRENCY,
        "mode": "unified",
        "request_weight": 1,
        "applies_to": ["text", "vision", "file", "image", "voice", "gpt-live"],
        "version": PATCH_VERSION,
    }


def install_v21_1_patch(app: FastAPI) -> FastAPI:
    settings = app.state.settings
    broker = app.state.broker
    registry = app.state.registry
    config_path = Path(settings.data_dir) / CONFIG_FILENAME
    runtime = {
        "max_concurrency": _load_limit(config_path),
        "config_path": str(config_path),
    }
    app.state.concurrency_config = runtime
    app.version = PATCH_VERSION

    def apply_limit(value: int) -> int:
        limit = _normalize_limit(value)
        runtime["max_concurrency"] = limit

        # v21's broker closures resolve these module globals at request time. Keep
        # the stable v21 broker implementation while changing its policy from
        # weighted capacity to a single configurable request count.
        v21_patch.CAPACITY_UNITS = limit
        v21_patch.request_weight = lambda _request_id: 1
        live_voice_patch.LIVE_CAPACITY_WEIGHT = 1
        broker.capacity_units = limit
        broker.max_concurrency = limit
        broker.concurrency_mode = "unified"
        return limit

    apply_limit(runtime["max_concurrency"])

    # v21's can_accept still accepts a legacy `weight` argument. From v0.21.1 all
    # request types count as one request, including callers that pass the former
    # Live weight of two.
    base_can_accept = broker.can_accept

    def can_accept_unified(client_id: str, _weight: int = 1) -> bool:
        return base_can_accept(client_id, 1)

    broker.can_accept = can_accept_unified
    broker._chat2api_v211_unified_concurrency = True

    base_send = registry.send
    if not getattr(registry, "_chat2api_v211_concurrency_routing", False):
        async def send_with_concurrency_limit(client_id: str, payload: dict[str, Any]) -> None:
            value = dict(payload or {})
            if str(value.get("type") or "") in ROUTED_REQUEST_TYPES:
                routing = dict(value.get("routing") or {})
                routing["worker_limit"] = int(runtime["max_concurrency"])
                routing["concurrency_mode"] = "unified"
                value["routing"] = routing
            await base_send(client_id, value)

        registry.send = send_with_concurrency_limit
        registry._chat2api_v211_concurrency_routing = True

    @app.get("/api/admin/concurrency")
    async def get_concurrency_config() -> dict[str, Any]:
        return _config_payload(int(runtime["max_concurrency"]))

    @app.put("/api/admin/concurrency")
    async def update_concurrency_config(body: ConcurrencyUpdate) -> dict[str, Any]:
        limit = _normalize_limit(body.max_concurrency)
        # Persist first. If disk persistence fails, do not silently apply a value
        # that would disappear after restart.
        _save_limit(config_path, limit)
        condition = getattr(broker, "_chat2api_v21_condition", None)
        if condition is not None:
            async with condition:
                apply_limit(limit)
                condition.notify_all()
        else:
            apply_limit(limit)
        return _config_payload(limit)

    @app.get("/assets/chat2api-v21-1.js")
    async def admin_v21_1_js() -> Response:
        path = Path(__file__).with_name("admin_v21_1.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def v21_1_configurable_concurrency(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type and (
            path in {"/", "/healthz", "/api/admin/overview", "/api/admin/concurrency"}
            or path.startswith("/api/admin/requests/")
        ):
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if "server_version" in payload or path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities.update({
                            "extension_weighted_concurrency": False,
                            "extension_configurable_concurrency": True,
                            "extension_concurrency_mode": "unified",
                            "extension_max_concurrency": int(runtime["max_concurrency"]),
                            "extension_capacity_units": int(runtime["max_concurrency"]),
                            "text_request_weight": 1,
                            "image_request_weight": 1,
                            "voice_request_weight": 1,
                            "live_voice_request_weight": 1,
                        })
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
            marker = '<script src="/assets/chat2api-v21-1.js"></script>'
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
