from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute

logger = logging.getLogger("chat2api.admin_requests")
PATCH_ID = "request-device-identity-v47-request-stability-v92-sibling-v142"
ASSET_PATH = "/assets/chat2api-request-device-identity-v47.js"


def _canonical_label(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("配对码", "设备码").replace("扩展", "Worker")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _install_request_route_stability(app: FastAPI, telemetry: Any) -> None:
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if route.path == "/api/admin/requests":
            if getattr(route.dependant.call, "__chat2api_request_stability_v92__", False):
                continue
            async def stable_admin_requests(**kwargs: Any) -> dict[str, Any]:
                result = await _maybe_await(telemetry.query(limit=int(kwargs.get("limit", 50)), offset=int(kwargs.get("offset", 0)), status=kwargs.get("status_filter"), model=kwargs.get("model"), key_id=kwargs.get("key_id"), q=kwargs.get("q")))
                if not isinstance(result, dict):
                    raise HTTPException(status_code=500, detail="Request history query returned an invalid result")
                summary = await _maybe_await(telemetry.summary())
                return {**result, "summary": summary if isinstance(summary, dict) else {}}
            stable_admin_requests.__chat2api_request_stability_v92__ = True
            route.dependant.call = stable_admin_requests
            route.endpoint = stable_admin_requests
        elif route.path == "/api/admin/requests/{request_id}":
            if getattr(route.dependant.call, "__chat2api_request_stability_v92__", False):
                continue
            async def stable_admin_request_detail(**kwargs: Any) -> dict[str, Any]:
                row = await _maybe_await(telemetry.get(str(kwargs.get("request_id") or "")))
                if not row:
                    raise HTTPException(status_code=404, detail="Request record not found")
                if not isinstance(row, dict):
                    raise HTTPException(status_code=500, detail="Request history detail returned an invalid result")
                return row
            stable_admin_request_detail.__chat2api_request_stability_v92__ = True
            route.dependant.call = stable_admin_request_detail
            route.endpoint = stable_admin_request_detail


def install_request_device_identity_patch(app: FastAPI) -> FastAPI:
    telemetry = app.state.telemetry
    registry = app.state.registry
    pairings = getattr(app.state, "pairings", None)
    if pairings is None or getattr(telemetry, "_chat2api_request_device_identity_v47", False):
        _install_request_route_stability(app, telemetry)
        return app

    base_recent, base_query, base_get = telemetry.recent, telemetry.query, telemetry.get
    base_pairing_create = getattr(pairings, "create", None)
    if callable(base_pairing_create):
        async def create_device_code(name: str = "Worker"):
            return await base_pairing_create(_canonical_label(name) or "Worker")
        pairings.create = create_device_code

    def pairing_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            rows.extend(pairings.list_public())
        except Exception:
            pass
        if rows:
            return rows
        path = Path(getattr(pairings, "path", ""))
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw = payload.get("pairing_codes") if isinstance(payload, dict) else None
        return [dict(item) for item in raw or [] if isinstance(item, dict)]

    def device_maps() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        by_client: dict[str, dict[str, Any]] = {}
        by_id: dict[str, dict[str, Any]] = {}
        by_device: dict[str, dict[str, Any]] = {}
        for item in pairing_rows():
            pairing_id = str(item.get("pairing_id") or "").strip()
            client_id = str(item.get("bound_client_id") or "").strip()
            device_id = str(item.get("bound_device_id") or "").strip()
            if pairing_id:
                by_id[pairing_id] = item
            if client_id:
                by_client[client_id] = item
            if device_id:
                by_device[device_id] = item
        return by_client, by_id, by_device

    def decorate(row: dict[str, Any] | None, maps=None) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return row
        result = dict(row)
        client_id = str(result.get("client_id") or result.get("worker_client_id") or "").strip()
        if not client_id:
            result.setdefault("device_name", None); result.setdefault("device_code_id", None); result.setdefault("worker_client_id", None)
            return result
        by_client, by_id, by_device = maps or device_maps()
        pairing = by_client.get(client_id)
        client = registry.clients.get(client_id)
        pairing_id = str(getattr(client, "pairing_id", "") or "").strip() if client else ""
        device_id = str(getattr(client, "device_id", "") or "").strip() if client else ""
        if pairing is None and pairing_id:
            pairing = by_id.get(pairing_id)
        # Multi-Worker devices bind the pairing code to one primary Worker. Sibling
        # Workers share the same physical device_id and must inherit that identity.
        if pairing is None and device_id:
            pairing = by_device.get(device_id)
        if pairing is not None:
            pairing_id = str(pairing.get("pairing_id") or pairing_id).strip()
        result["worker_client_id"] = client_id
        result["device_code_id"] = pairing_id or None
        result["device_name"] = _canonical_label((pairing or {}).get("name")) or None
        return result

    def decorate_query_result(raw: Any) -> dict[str, Any]:
        result = dict(raw); maps = device_maps()
        result["data"] = [decorate(row, maps) or {} for row in result.get("data") or []]
        return result

    def recent_with_device(limit: int = 100) -> list[dict[str, Any]]:
        maps = device_maps(); return [decorate(row, maps) or {} for row in base_recent(limit)]

    def query_with_device(*args: Any, **kwargs: Any) -> Any:
        raw = base_query(*args, **kwargs)
        if inspect.isawaitable(raw):
            async def resolve_query(): return decorate_query_result(await raw)
            return resolve_query()
        return decorate_query_result(raw)

    def get_with_device(request_id: str) -> Any:
        raw = base_get(request_id)
        if inspect.isawaitable(raw):
            async def resolve_get(): return decorate(await raw, device_maps())
            return resolve_get()
        return decorate(raw, device_maps())

    telemetry.recent, telemetry.query, telemetry.get = recent_with_device, query_with_device, get_with_device
    telemetry._chat2api_request_device_identity_v47 = True
    _install_request_route_stability(app, telemetry)

    @app.get(ASSET_PATH, include_in_schema=False)
    async def request_device_identity_asset() -> Response:
        source = Path(__file__).with_name("admin_request_device_identity_v47.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def inject_request_device_identity(request: Request, call_next: Callable):
        response = await call_next(request)
        if request.url.path not in {"/admin", "/developers"} or "text/html" not in response.headers.get("content-type", ""):
            return response
        body = getattr(response, "body", None)
        if body is None:
            chunks = []
            iterator = getattr(response, "body_iterator", None)
            if iterator is not None:
                async for chunk in iterator:
                    chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
            body = b"".join(chunks)
        text = bytes(body).decode("utf-8", errors="replace")
        marker = f'<script src="{ASSET_PATH}"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
