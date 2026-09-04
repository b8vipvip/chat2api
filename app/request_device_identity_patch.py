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
PATCH_ID = "request-device-identity-v47-request-stability-v93"
ASSET_PATH = "/assets/chat2api-request-device-identity-v47.js"
REQUEST_HISTORY_ASSET = "/assets/chat2api-request-history-v93.js"


def _canonical_label(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("配对码", "设备码").replace("扩展", "Worker")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _install_request_route_stability(app: FastAPI, telemetry: Any) -> None:
    """Install the sole server-side owner for request-history GET routes.

    Historical decorators changed TelemetryStore.query/get between sync and async
    implementations while the legacy endpoint unpacked query() synchronously. The
    route owner keeps FastAPI's original dependency graph and normalizes either
    telemetry contract before returning a mapping.
    """

    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue

        if route.path == "/api/admin/requests":
            if getattr(route.dependant.call, "__chat2api_request_stability_v93__", False):
                continue

            async def stable_admin_requests(**kwargs: Any) -> dict[str, Any]:
                limit = int(kwargs.get("limit", 50))
                offset = int(kwargs.get("offset", 0))
                status_filter = kwargs.get("status_filter")
                model = kwargs.get("model")
                key_id = kwargs.get("key_id")
                q = kwargs.get("q")
                result = await _maybe_await(
                    telemetry.query(
                        limit=limit,
                        offset=offset,
                        status=status_filter,
                        model=model,
                        key_id=key_id,
                        q=q,
                    )
                )
                if not isinstance(result, dict):
                    logger.error("Request history query returned non-mapping type=%s", type(result).__name__)
                    raise HTTPException(status_code=500, detail="Request history query returned an invalid result")
                summary = await _maybe_await(telemetry.summary())
                if not isinstance(summary, dict):
                    logger.warning("Request history summary returned non-mapping type=%s", type(summary).__name__)
                    summary = {}
                return {**result, "summary": summary}

            stable_admin_requests.__chat2api_request_stability_v93__ = True
            route.dependant.call = stable_admin_requests
            route.endpoint = stable_admin_requests

        elif route.path == "/api/admin/requests/{request_id}":
            if getattr(route.dependant.call, "__chat2api_request_stability_v93__", False):
                continue

            async def stable_admin_request_detail(**kwargs: Any) -> dict[str, Any]:
                request_id = str(kwargs.get("request_id") or "")
                row = await _maybe_await(telemetry.get(request_id))
                if not row:
                    raise HTTPException(status_code=404, detail="Request record not found")
                if not isinstance(row, dict):
                    logger.error("Request history detail returned non-mapping type=%s", type(row).__name__)
                    raise HTTPException(status_code=500, detail="Request history detail returned an invalid result")
                return row

            stable_admin_request_detail.__chat2api_request_stability_v93__ = True
            route.dependant.call = stable_admin_request_detail
            route.endpoint = stable_admin_request_detail


def install_request_device_identity_patch(app: FastAPI) -> FastAPI:
    """Decorate request telemetry and install the canonical request-history owner."""

    telemetry = app.state.telemetry
    registry = app.state.registry
    pairings = getattr(app.state, "pairings", None)
    if pairings is None or getattr(telemetry, "_chat2api_request_device_identity_v47", False):
        _install_request_route_stability(app, telemetry)
        return app

    base_recent = telemetry.recent
    base_query = telemetry.query
    base_get = telemetry.get
    base_pairing_create = getattr(pairings, "create", None)

    if callable(base_pairing_create):
        async def create_device_code(name: str = "Worker"):
            canonical = _canonical_label(name) or "Worker"
            return await base_pairing_create(canonical)

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

    def device_maps() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        by_client: dict[str, dict[str, Any]] = {}
        by_id: dict[str, dict[str, Any]] = {}
        for item in pairing_rows():
            pairing_id = str(item.get("pairing_id") or "")
            client_id = str(item.get("bound_client_id") or "")
            if pairing_id:
                by_id[pairing_id] = item
            if client_id:
                by_client[client_id] = item
        return by_client, by_id

    def decorate(
        row: dict[str, Any] | None,
        maps: tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return row
        result = dict(row)
        client_id = str(result.get("client_id") or "")
        if not client_id:
            result.setdefault("device_name", None)
            result.setdefault("device_code_id", None)
            result.setdefault("worker_client_id", None)
            return result

        by_client, by_id = maps or device_maps()
        pairing = by_client.get(client_id)
        client = registry.clients.get(client_id)
        pairing_id = str(getattr(client, "pairing_id", "") or "") if client else ""
        if pairing is None and pairing_id:
            pairing = by_id.get(pairing_id)
        if pairing is not None:
            pairing_id = str(pairing.get("pairing_id") or pairing_id)

        result["worker_client_id"] = client_id
        result["device_code_id"] = pairing_id or None
        result["device_name"] = _canonical_label((pairing or {}).get("name")) or None
        return result

    def decorate_query_result(raw: Any) -> dict[str, Any]:
        result = dict(raw)
        maps = device_maps()
        result["data"] = [decorate(row, maps) or {} for row in result.get("data") or []]
        return result

    def recent_with_device(limit: int = 100) -> list[dict[str, Any]]:
        maps = device_maps()
        return [decorate(row, maps) or {} for row in base_recent(limit)]

    def query_with_device(*args: Any, **kwargs: Any) -> Any:
        raw = base_query(*args, **kwargs)
        if inspect.isawaitable(raw):
            async def resolve_query() -> dict[str, Any]:
                return decorate_query_result(await raw)

            return resolve_query()
        return decorate_query_result(raw)

    def get_with_device(request_id: str) -> Any:
        raw = base_get(request_id)
        if inspect.isawaitable(raw):
            async def resolve_get() -> dict[str, Any] | None:
                return decorate(await raw, device_maps())

            return resolve_get()
        return decorate(raw, device_maps())

    telemetry.recent = recent_with_device
    telemetry.query = query_with_device
    telemetry.get = get_with_device
    telemetry._chat2api_request_device_identity_v47 = True

    _install_request_route_stability(app, telemetry)

    @app.get(ASSET_PATH, include_in_schema=False)
    async def request_device_identity_asset() -> Response:
        source = Path(__file__).with_name("admin_request_device_identity_v47.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get(REQUEST_HISTORY_ASSET, include_in_schema=False)
    async def request_history_asset() -> Response:
        source = Path(__file__).with_name("admin_request_history_v93.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def inject_request_device_identity(request: Request, call_next: Callable):
        response = await call_next(request)
        if request.url.path not in {"/admin", "/developers"} or "text/html" not in response.headers.get("content-type", ""):
            return response
        body = getattr(response, "body", None)
        if body is None:
            chunks: list[bytes] = []
            iterator = getattr(response, "body_iterator", None)
            if iterator is not None:
                async for chunk in iterator:
                    chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
            body = b"".join(chunks)
        text = bytes(body).decode("utf-8", errors="replace")
        identity_marker = f'<script src="{ASSET_PATH}"></script>'
        history_marker = f'<script src="{REQUEST_HISTORY_ASSET}"></script>'
        if identity_marker not in text:
            text = text.replace("</body>", identity_marker + "</body>")
        if history_marker not in text:
            text = text.replace("</body>", history_marker + "</body>")
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
