from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response


ATTACHMENT_DOWNLOAD_REVISION = 83
_EXTENSION_FILE_PATH = "/api/extensions/files/{file_id}"
_ASCII_SUFFIX = re.compile(r"\.[A-Za-z0-9][A-Za-z0-9._-]{0,15}$")


def attachment_download_headers(filename: str) -> dict[str, str]:
    """Build HTTP/1.x-safe headers while preserving the UTF-8 filename losslessly."""
    original = str(filename or "attachment").replace("\r", "").replace("\n", "").strip() or "attachment"
    match = _ASCII_SUFFIX.search(original)
    suffix = match.group(0) if match else ""
    fallback = f"attachment{suffix}"
    encoded = quote(original, safe="._~-")
    return {
        "Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}",
        # Starlette encodes header values as latin-1. Percent-UTF8 keeps this
        # transport header ASCII-safe while retaining the original filename.
        "X-Chat2API-Filename": encoded,
        "X-Chat2API-Filename-Encoding": "percent-utf8",
        "X-Chat2API-Attachment-Revision": str(ATTACHMENT_DOWNLOAD_REVISION),
        "Cache-Control": "no-store",
    }


def install_attachment_download_v82_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "attachment_download_v82_installed", False):
        return app

    registry = app.state.registry
    file_store = app.state.file_store
    telemetry = app.state.telemetry
    broker = app.state.broker

    # app.main owns the historical route. Replace it rather than adding a
    # duplicate route because Starlette resolves the first matching route.
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == _EXTENSION_FILE_PATH
            and "GET" in (getattr(route, "methods", set()) or set())
        )
    ]

    @app.get(_EXTENSION_FILE_PATH, include_in_schema=False)
    async def extension_file_v82(
        file_id: str,
        request_id: str = Query(..., min_length=6, max_length=120),
        client_id: str = Query(...),
        token: str = Query(...),
    ) -> Response:
        if not await registry.authenticate(client_id, token):
            raise HTTPException(status_code=401, detail="Invalid extension token")
        # A paired Worker is not a global file reader. A download is valid only
        # while the exact request is running on this Worker, the file was
        # explicitly attached to that request, and the request API key owns it.
        row = telemetry.get(request_id)
        item = file_store.get(file_id)
        active = getattr(broker, "requests", {}).get(request_id)
        allowed_ids = row.get("input_file_ids") if isinstance(row, dict) else None
        dispatch_socket = getattr(active, "dispatch_socket", None)
        sockets = getattr(registry, "sockets", None)
        current_socket = sockets.get(client_id) if isinstance(sockets, dict) else None
        if (
            not isinstance(row, dict)
            or active is None
            or dispatch_socket is None
            or current_socket is not dispatch_socket
            or str(getattr(active, "client_id", "") or "") != client_id
            or getattr(active, "terminal_event_type", None) is not None
            or getattr(active, "completed_mono", None) is not None
            or str(row.get("status") or "") != "running"
            or str(row.get("client_id") or "") != client_id
            or not item
            or (
                str(row.get("api_key_id") or "") != "master"
                and str(row.get("api_key_id") or "") != str(item.owner_key_id or "")
            )
            or not isinstance(allowed_ids, list)
            or file_id not in {str(value) for value in allowed_ids}
        ):
            raise HTTPException(status_code=404, detail="Unknown attachment")
        try:
            item, payload = file_store.read(file_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=410, detail=str(error)) from error
        return Response(
            payload,
            media_type=item.mime_type,
            headers=attachment_download_headers(item.filename),
        )

    app.state.attachment_download_v82_installed = True
    return app
