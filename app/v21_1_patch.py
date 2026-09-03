from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import live_voice_patch, v21_patch
from .broker import RequestState


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


def _load_config(path: Path) -> tuple[int, dict[str, int]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return DEFAULT_MAX_CONCURRENCY, {}
    if not isinstance(payload, dict):
        return DEFAULT_MAX_CONCURRENCY, {}

    # Migrate the historical v1 global setting into the v2 default. Existing
    # installations therefore keep their configured value until an extension is
    # given an explicit override from the extension-management table.
    default = _normalize_limit(
        payload.get("default_max_concurrency", payload.get("max_concurrency"))
    )
    raw_clients = payload.get("clients")
    clients: dict[str, int] = {}
    if isinstance(raw_clients, dict):
        for client_id, value in raw_clients.items():
            client_id = str(client_id or "").strip()
            if client_id:
                clients[client_id] = _normalize_limit(value)
    return default, clients


def _save_config(path: Path, default_limit: int, client_limits: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "version": 2,
                "mode": "per-extension",
                "default_max_concurrency": int(default_limit),
                # Keep this compatibility alias for older tooling that only knows
                # the v1 global configuration shape.
                "max_concurrency": int(default_limit),
                "clients": {
                    str(client_id): int(value)
                    for client_id, value in sorted(client_limits.items())
                },
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


def _config_payload(default_limit: int, client_limits: dict[str, int]) -> dict[str, Any]:
    return {
        "max_concurrency": int(default_limit),
        "default_max_concurrency": int(default_limit),
        "client_limits": dict(client_limits),
        "min_max_concurrency": MIN_MAX_CONCURRENCY,
        "max_max_concurrency": MAX_MAX_CONCURRENCY,
        "mode": "per-extension",
        "request_weight": 1,
        "applies_to": ["text", "vision", "file", "image", "voice", "gpt-live"],
        "version": PATCH_VERSION,
    }


def install_v21_1_patch(app: FastAPI) -> FastAPI:
    settings = app.state.settings
    broker = app.state.broker
    registry = app.state.registry
    config_path = Path(settings.data_dir) / CONFIG_FILENAME
    default_limit, loaded_client_limits = _load_config(config_path)
    runtime: dict[str, Any] = {
        "max_concurrency": default_limit,  # compatibility alias
        "default_max_concurrency": default_limit,
        "client_limits": loaded_client_limits,
        "config_path": str(config_path),
    }
    app.state.concurrency_config = runtime
    app.version = PATCH_VERSION

    def limit_for(client_id: str) -> int:
        client_id = str(client_id or "")
        value = runtime["client_limits"].get(client_id, runtime["default_max_concurrency"])
        return _normalize_limit(value)

    def limit_source(client_id: str) -> str:
        return "extension" if str(client_id or "") in runtime["client_limits"] else "default"

    runtime["limit_for"] = limit_for

    def apply_default(value: int) -> int:
        limit = _normalize_limit(value)
        runtime["default_max_concurrency"] = limit
        runtime["max_concurrency"] = limit

        # Preserve historical module globals for old call sites, while broker
        # admission below is authoritative and evaluates each extension separately.
        v21_patch.CAPACITY_UNITS = limit
        v21_patch.request_weight = lambda _request_id: 1
        live_voice_patch.LIVE_CAPACITY_WEIGHT = 1
        broker.capacity_units = limit
        broker.max_concurrency = limit
        broker.concurrency_mode = "per-extension"
        return limit

    apply_default(default_limit)

    def used_units(client_id: str) -> int:
        active = getattr(broker, "client_active_requests", {}).get(str(client_id), {})
        return sum(int(weight or 1) for weight in active.values()) if isinstance(active, dict) else 0

    def can_accept(client_id: str, _weight: int = 1) -> bool:
        return used_units(client_id) + 1 <= limit_for(client_id)

    def capacity_snapshot(client_id: str) -> dict[str, Any]:
        client_id = str(client_id)
        active = getattr(broker, "client_active_requests", {}).get(client_id, {})
        if not isinstance(active, dict):
            active = {}
        used = used_units(client_id)
        limit = limit_for(client_id)
        return {
            "limit_units": limit,
            "used_units": used,
            "available_units": max(0, limit - used),
            "active_requests": len(active),
            "request_weights": dict(active),
            "limit_source": limit_source(client_id),
        }

    broker.client_used_units = used_units
    broker.can_accept = can_accept
    broker.capacity_snapshot = capacity_snapshot
    broker._chat2api_v211_unified_concurrency = True
    broker._chat2api_v211_per_extension_concurrency = True

    # v21's create() captured the old global CAPACITY_UNITS in its closure. Replace
    # only admission with an equivalent implementation that evaluates limit_for()
    # on every attempt. v21's release() remains valid and still notifies the same
    # condition when requests finish.
    async def create_per_extension(request_id: str, client_id: str):
        request_id = str(request_id)
        client_id = str(client_id)
        started = time.perf_counter()
        condition = broker._chat2api_v21_condition
        deadline = asyncio.get_running_loop().time() + v21_patch.CAPACITY_WAIT_SECONDS

        async with condition:
            while not can_accept(client_id, 1):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    snapshot = capacity_snapshot(client_id)
                    raise RuntimeError(
                        "extension capacity exhausted "
                        f"(used={snapshot['used_units']}/{snapshot['limit_units']}, requested_weight=1)"
                    )
                try:
                    await asyncio.wait_for(condition.wait(), timeout=remaining)
                except asyncio.TimeoutError as error:
                    snapshot = capacity_snapshot(client_id)
                    raise RuntimeError(
                        "extension capacity exhausted "
                        f"(used={snapshot['used_units']}/{snapshot['limit_units']}, requested_weight=1)"
                    ) from error

            if request_id in broker.requests:
                raise RuntimeError(f"Duplicate request_id: {request_id}")
            loop = asyncio.get_running_loop()
            state = RequestState(
                request_id=request_id,
                client_id=client_id,
                final_future=loop.create_future(),
            )
            before = used_units(client_id)
            limit = limit_for(client_id)
            broker.requests[request_id] = state
            broker.client_active_requests.setdefault(client_id, {})[request_id] = 1
            broker.client_requests.setdefault(client_id, request_id)
            state.diagnostics.update({
                "extension_capacity_limit_units": limit,
                "extension_capacity_weight": 1,
                "extension_capacity_used_before": before,
                "extension_capacity_used_after": before + 1,
                "extension_capacity_wait_ms": round((time.perf_counter() - started) * 1000, 1),
                "extension_concurrency_v21": True,
                "extension_concurrency_per_client": True,
                "extension_concurrency_limit_source": limit_source(client_id),
            })

        # Preserve v19's non-stream disconnect tracker, which was also preserved by
        # v21's original concurrent create implementation.
        tracked = getattr(broker, "_chat2api_v19_tracked_states", None)
        if isinstance(tracked, dict):
            try:
                from .v19_patch import _http_request_marker
                marker = _http_request_marker.get()
                if marker:
                    tracked[marker] = state
            except Exception:
                pass
        return state

    broker.create = create_per_extension

    base_send = registry.send
    if not getattr(registry, "_chat2api_v211_concurrency_routing", False):
        async def send_with_concurrency_limit(client_id: str, payload: dict[str, Any]) -> None:
            value = dict(payload or {})
            if str(value.get("type") or "") in ROUTED_REQUEST_TYPES:
                routing = dict(value.get("routing") or {})
                routing["worker_limit"] = limit_for(client_id)
                routing["concurrency_mode"] = "per-extension"
                value["routing"] = routing
            await base_send(client_id, value)

        registry.send = send_with_concurrency_limit
        registry._chat2api_v211_concurrency_routing = True

    base_summaries = registry.summaries
    if not getattr(registry, "_chat2api_v211_per_extension_summaries", False):
        def summaries_with_limits() -> list[dict[str, Any]]:
            rows = base_summaries()
            for row in rows:
                client_id = str(row.get("client_id") or "")
                snapshot = capacity_snapshot(client_id)
                row["busy"] = snapshot["used_units"] > 0
                row["capacity"] = snapshot
                row["max_concurrency"] = snapshot["limit_units"]
                row["concurrency_limit_source"] = snapshot["limit_source"]
            return rows

        registry.summaries = summaries_with_limits
        registry._chat2api_v211_per_extension_summaries = True

    async def persist_and_notify() -> None:
        _save_config(
            config_path,
            int(runtime["default_max_concurrency"]),
            dict(runtime["client_limits"]),
        )
        condition = getattr(broker, "_chat2api_v21_condition", None)
        if condition is not None:
            async with condition:
                condition.notify_all()

    @app.get("/api/admin/concurrency")
    async def get_concurrency_config() -> dict[str, Any]:
        return _config_payload(
            int(runtime["default_max_concurrency"]),
            dict(runtime["client_limits"]),
        )

    @app.put("/api/admin/concurrency")
    async def update_concurrency_config(body: ConcurrencyUpdate) -> dict[str, Any]:
        # Compatibility endpoint: this now changes only the default inherited by
        # extensions without an explicit per-ID override.
        previous = int(runtime["default_max_concurrency"])
        apply_default(body.max_concurrency)
        try:
            await persist_and_notify()
        except Exception:
            apply_default(previous)
            raise
        return _config_payload(
            int(runtime["default_max_concurrency"]),
            dict(runtime["client_limits"]),
        )

    def ensure_client(client_id: str) -> str:
        client_id = str(client_id or "").strip()
        if client_id not in registry.clients:
            raise HTTPException(status_code=404, detail="Unknown extension ID")
        return client_id

    def client_payload(client_id: str) -> dict[str, Any]:
        client_id = ensure_client(client_id)
        snapshot = capacity_snapshot(client_id)
        return {
            "client_id": client_id,
            "max_concurrency": snapshot["limit_units"],
            "default_max_concurrency": int(runtime["default_max_concurrency"]),
            "source": snapshot["limit_source"],
            "active_api_calls": snapshot["active_requests"],
            "capacity": snapshot,
            "min_max_concurrency": MIN_MAX_CONCURRENCY,
            "max_max_concurrency": MAX_MAX_CONCURRENCY,
            "mode": "per-extension",
        }

    @app.get("/api/admin/extensions/{client_id}/concurrency")
    async def get_extension_concurrency(client_id: str) -> dict[str, Any]:
        return client_payload(client_id)

    @app.put("/api/admin/extensions/{client_id}/concurrency")
    async def update_extension_concurrency(client_id: str, body: ConcurrencyUpdate) -> dict[str, Any]:
        client_id = ensure_client(client_id)
        previous = runtime["client_limits"].get(client_id)
        runtime["client_limits"][client_id] = _normalize_limit(body.max_concurrency)
        try:
            await persist_and_notify()
        except Exception:
            if previous is None:
                runtime["client_limits"].pop(client_id, None)
            else:
                runtime["client_limits"][client_id] = previous
            raise
        return client_payload(client_id)

    @app.delete("/api/admin/extensions/{client_id}/concurrency")
    async def reset_extension_concurrency(client_id: str) -> dict[str, Any]:
        client_id = ensure_client(client_id)
        previous = runtime["client_limits"].pop(client_id, None)
        try:
            await persist_and_notify()
        except Exception:
            if previous is not None:
                runtime["client_limits"][client_id] = previous
            raise
        return client_payload(client_id)

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
                # v21.1 owns concurrency compatibility only. Runtime identity is
                # owned by runtime_contract and must never be rewritten to 0.21.1.
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities.update({
                            "extension_weighted_concurrency": False,
                            "extension_configurable_concurrency": True,
                            "extension_per_client_concurrency": True,
                            "extension_concurrency_mode": "per-extension",
                            "extension_max_concurrency": int(runtime["default_max_concurrency"]),
                            "extension_default_max_concurrency": int(runtime["default_max_concurrency"]),
                            "extension_capacity_units": int(runtime["default_max_concurrency"]),
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
