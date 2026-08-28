from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_ID = "request-device-identity-v47"
ASSET_PATH = "/assets/chat2api-request-device-identity-v47.js"


def _canonical_label(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("配对码", "设备码").replace("扩展", "Worker")


def install_request_device_identity_patch(app: FastAPI) -> FastAPI:
    """Add a stable device-code name to request-history rows.

    Request telemetry already records the browser client_id.  This patch resolves
    that ID through the one-device code store so the admin console can show the
    human name (for example ``ubuntu03`` or ``free``) instead of forcing the
    operator to cross-reference an ``ext_*`` identifier manually.

    Legacy storage/API field names remain readable for installed clients.  New
    presentation aliases are additive and do not invalidate old Worker tokens.
    """

    telemetry = app.state.telemetry
    registry = app.state.registry
    pairings = getattr(app.state, "pairings", None)
    if pairings is None or getattr(telemetry, "_chat2api_request_device_identity_v47", False):
        return app

    base_recent = telemetry.recent
    base_query = telemetry.query
    base_get = telemetry.get

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

    def decorate(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return row
        result = dict(row)
        client_id = str(result.get("client_id") or "")
        if not client_id:
            result.setdefault("device_name", None)
            result.setdefault("device_code_id", None)
            result.setdefault("worker_client_id", None)
            return result

        by_client, by_id = device_maps()
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

    def recent_with_device(limit: int = 100) -> list[dict[str, Any]]:
        return [decorate(row) or {} for row in base_recent(limit)]

    def query_with_device(*args, **kwargs) -> dict[str, Any]:
        result = dict(base_query(*args, **kwargs))
        result["data"] = [decorate(row) or {} for row in result.get("data") or []]
        return result

    def get_with_device(request_id: str) -> dict[str, Any] | None:
        return decorate(base_get(request_id))

    telemetry.recent = recent_with_device
    telemetry.query = query_with_device
    telemetry.get = get_with_device
    telemetry._chat2api_request_device_identity_v47 = True

    @app.get(ASSET_PATH, include_in_schema=False)
    async def request_device_identity_asset() -> Response:
        source = Path(__file__).with_name("admin_request_device_identity_v47.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def inject_request_device_identity(request: Request, call_next: Callable):
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
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
        marker = f'<script src="{ASSET_PATH}"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
