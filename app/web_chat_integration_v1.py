"""Production installer for the safe Web Chat renderer and recovery/file APIs."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .native_content_v1 import native_delivery_fingerprint
from .user_commerce import USER_SESSION_COOKIE


def _asset_handler(asset_path: Path, media_type: str):
    async def serve_asset() -> Response:
        return Response(
            asset_path.read_bytes(),
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
            },
        )
    return serve_asset


_REQUEST_ID_RE = re.compile(r"^req_[A-Za-z0-9_-]{8,120}$")
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie"}
_NATIVE_TERMINAL = {"completed", "error", "cancelled", "stalled"}


def install_web_chat_features_v1(app: Any) -> Any:
    if getattr(app.state, "web_chat_features_v1_installed", False):
        return app

    required = ("user_accounts", "file_store", "telemetry", "broker", "registry")
    missing = [name for name in required if getattr(app.state, name, None) is None]
    if missing:
        raise RuntimeError("Web Chat integration requires app state: " + ", ".join(missing))

    from .web_chat_recovery_api import install_web_chat_recovery_api
    from .web_chat_file_download_api import install_web_chat_file_download_api
    from .web_chat_conversations_v1 import install_web_chat_conversations_v1
    install_web_chat_recovery_api(app)
    install_web_chat_file_download_api(app)
    install_web_chat_conversations_v1(app)

    if not callable(getattr(app.state, "native_response_snapshot", None)):
        raise RuntimeError("Web Chat native delivery requires the production native snapshot authority")

    def current_user(request: Request):
        accounts = getattr(request.app.state, "user_accounts", None)
        if accounts is None:
            raise HTTPException(status_code=503, detail="user account service unavailable",
                                headers=_PRIVATE_HEADERS)
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            raise HTTPException(status_code=401, detail="user login required",
                                headers=_PRIVATE_HEADERS)
        return accounts, user

    def owned_request(request_id: str, request: Request):
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise HTTPException(status_code=404, detail="request not found",
                                headers=_PRIVATE_HEADERS)
        accounts, user = current_user(request)
        telemetry = getattr(request.app.state, "telemetry", None)
        if telemetry is None:
            raise HTTPException(status_code=503, detail="request telemetry unavailable",
                                headers=_PRIVATE_HEADERS)
        row = telemetry.get(request_id)
        key_id = str((row or {}).get("api_key_id") or "")
        if not row or not key_id or not accounts.owns_key(str(user["user_id"]), key_id):
            raise HTTPException(status_code=404, detail="request not found",
                                headers=_PRIVATE_HEADERS)
        return accounts, user, key_id

    async def user_native_snapshot(request_id: str, request: Request, *,
                                   watch_pending_archive: bool = False):
        _, _, owner = owned_request(request_id, request)
        loader = request.app.state.native_response_snapshot
        return await loader(
            request_id, owner,
            watch_pending_archive=watch_pending_archive,
            preserve_native_tree=True,
        )

    @app.get("/api/user/chat/requests/{request_id}/native-content")
    async def user_chat_native_content(request_id: str, request: Request) -> Response:
        snapshot = await user_native_snapshot(request_id, request)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="native content unavailable",
                                headers=_PRIVATE_HEADERS)
        snapshot.pop("_pending_archive_pollable", None)
        return JSONResponse(snapshot, headers={
            **_PRIVATE_HEADERS, "X-Content-Type-Options": "nosniff",
        })

    @app.get("/api/user/chat/requests/{request_id}/native-content/events")
    async def user_chat_native_content_events(request_id: str, request: Request) -> StreamingResponse:
        accounts, user, owner = owned_request(request_id, request)
        telemetry = request.app.state.telemetry
        loader = request.app.state.native_response_snapshot

        async def changes():
            last = None
            for tick in range(3600):
                try:
                    if tick and tick % 40 == 0:
                        refreshed = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
                        if not refreshed or str(refreshed.get("user_id") or "") != str(user["user_id"]):
                            return
                        if not accounts.owns_key(str(user["user_id"]), owner):
                            return
                    current = await loader(
                        request_id, owner, watch_pending_archive=True,
                        preserve_native_tree=True,
                    )
                    pending_archive_pollable = bool(
                        current and current.pop("_pending_archive_pollable", False))
                    signature = (
                        native_delivery_fingerprint(current),
                        current.get("finality") if current else None,
                    )
                    if current and signature[0] is not None and signature != last:
                        last = signature
                        yield "event: native.snapshot\n" + "data: " + json.dumps(
                            current, ensure_ascii=False, separators=(",", ":")
                        ) + "\n\n"
                    row = telemetry.get(request_id) or {}
                    status = str(row.get("status") or "").lower()
                    if status in _NATIVE_TERMINAL:
                        if not (status == "completed" and pending_archive_pollable and tick < 720):
                            yield "event: native.end\ndata: {}\n\n"
                            return
                except (ValueError, OSError):
                    return
                if tick % 40 == 0:
                    yield ": native keepalive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(changes(), media_type="text/event-stream", headers={
            **_PRIVATE_HEADERS,
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        })

    assets = {
        "/assets/chat2api-chat-renderer-v2.js": ("chat_renderer_v2.js", "application/javascript"),
        "/assets/chat2api-chat-native-renderer-v1.js": ("chat_native_renderer_v1.js", "application/javascript"),
        "/assets/chat2api-chat-stream-recovery-v1.js": ("chat_stream_recovery_v1.js", "application/javascript"),
    }
    for route_path, (filename, media_type) in assets.items():
        asset_path = Path(__file__).with_name(filename)
        if not asset_path.is_file():
            raise RuntimeError("Required Web Chat asset is missing: " + filename)
        app.add_api_route(
            route_path, _asset_handler(asset_path, media_type),
            methods=["GET"], include_in_schema=False,
        )

    @app.middleware("http")
    async def web_chat_private_cache_boundary(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith(("/api/user/chat/completions", "/api/user/chat/conversations", "/api/user/chat/requests/", "/api/user/chat/files/", "/api/user/files/")):
            response.headers["Cache-Control"] = "private, no-store"
            vary_values = [value.strip() for value in response.headers.get("Vary", "").split(",") if value.strip()]
            if not any(value.lower() == "cookie" for value in vary_values):
                vary_values.append("Cookie")
            response.headers["Vary"] = ", ".join(vary_values)
        return response
    app.state.web_chat_features_v1_installed = True
    app.state.web_chat_integration_version = 1
    return app
