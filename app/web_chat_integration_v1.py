"""Production installer for the safe Web Chat renderer and recovery/file APIs."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from .admin_auth import same_origin_admin_request
from .models import FileUploadRequest
from .native_content_v1 import native_delivery_fingerprint
from .user_commerce import USER_SESSION_COOKIE


def _asset_handler(
    asset_path: Path, media_type: str, cache_control: str = "no-store",
):
    async def serve_asset() -> Response:
        return Response(
            asset_path.read_bytes(),
            media_type=media_type,
            headers={
                "Cache-Control": cache_control,
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
            },
        )
    return serve_asset


_KATEX_FONT_RE = re.compile(r"^KaTeX_[A-Za-z0-9_-]+\.(?:woff2|woff|ttf)$")


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
    from .web_chat_client_diagnostics_v1 import install_web_chat_client_diagnostics_v1
    install_web_chat_recovery_api(app)
    install_web_chat_file_download_api(app)
    install_web_chat_conversations_v1(app)
    install_web_chat_client_diagnostics_v1(app)

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

    async def resolve_chat_identity(request: Request, *, require_same_origin: bool = False):
        if require_same_origin and not same_origin_admin_request(request):
            raise HTTPException(status_code=403, detail="Same-origin request required",
                                headers=_PRIVATE_HEADERS)
        from .web_chat_identity_v1 import resolve_web_chat_principal
        requested_key_id = request.headers.get("x-chat2api-user-key-id")
        return await resolve_web_chat_principal(request, requested_key_id)

    @app.get("/api/user/chat/session")
    async def user_chat_session(request: Request) -> Response:
        user, principal = await resolve_chat_identity(request)
        return JSONResponse({
            "key_id": principal.key_id,
            "scopes": list(principal.scopes),
        }, headers=_PRIVATE_HEADERS)

    @app.get("/api/user/chat/models")
    async def user_chat_models(request: Request) -> Response:
        _, principal = await resolve_chat_identity(request)
        if "models" not in principal.scopes:
            raise HTTPException(status_code=403, detail="Web Chat shared API Key lacks models permission",
                                headers=_PRIVATE_HEADERS)
        registry = getattr(request.app.state, "registry", None)
        if registry is None:
            raise HTTPException(status_code=503, detail="model catalog unavailable",
                                headers=_PRIVATE_HEADERS)
        # The administrator's "可用模型" projection is the single source
        # for Web Chat choices. A Free page-default remains the routing alias
        # "default", while its visible label is the configured concrete model ID.
        # This preserves the dispatch semantics without displaying a misleading
        # generic alias or exposing image/voice-only routes as text models.
        model_task_snapshot = getattr(request.app.state, "worker_model_tasks_v147_snapshot", None)
        models: list[dict[str, Any]] = []
        if callable(model_task_snapshot):
            try:
                available_payload = await model_task_snapshot()
            except Exception as error:
                raise HTTPException(status_code=503, detail="available model catalogue unavailable",
                                    headers=_PRIVATE_HEADERS) from error
            available_rows = available_payload.get("available_models") or []
            default_mapping = str(available_payload.get("available_models_default_mapping") or "").strip()
            catalog = {
                str(row.get("id") or ""): dict(row)
                for row in registry.model_catalog(online_only=True)
                if isinstance(row, dict)
            }
            for available in available_rows:
                if not isinstance(available, dict):
                    continue
                model_id = str(available.get("id") or "").strip()
                if not model_id or model_id in {"gpt-image", "gpt-live", "gpt-live-mini", "chatgpt-web"}:
                    continue
                if model_id == "default":
                    models.append({
                        "id": "default",
                        "label": default_mapping or "默认模型",
                        "mapped_model_id": default_mapping or None,
                        "capabilities": ["text", "vision", "file-understanding"],
                        "selectable": True,
                        "source": "worker-available-default",
                    })
                    continue
                row = catalog.get(model_id, {
                    "id": model_id, "object": "model", "created": 0,
                    "owned_by": "chat2api", "capabilities": ["text"],
                })
                row["id"] = model_id
                row["label"] = model_id
                row["selectable"] = True
                models.append(row)
        else:
            # Compatibility for focused tests and older composition roots that
            # have not installed the v147 available-model projection.
            minimum = getattr(registry, "legacy_minimum_model", None)
            default_mapping = str(minimum() or "").strip() if callable(minimum) else ""
            for source in registry.model_catalog(online_only=True):
                if not isinstance(source, dict):
                    continue
                model_id = str(source.get("id") or "")
                if model_id in {"chatgpt-web", "gpt-image", "gpt-live", "gpt-live-mini"}:
                    continue
                row = dict(source)
                if model_id == "default":
                    row["label"] = default_mapping or "默认模型"
                    row["mapped_model_id"] = default_mapping or None
                models.append(row)
        return JSONResponse({
            "object": "list",
            "data": models,
        }, headers=_PRIVATE_HEADERS)

    @app.post("/api/user/chat/files")
    async def user_chat_upload_file(request: Request) -> Response:
        _, principal = await resolve_chat_identity(request, require_same_origin=True)
        if "files" not in principal.scopes:
            raise HTTPException(status_code=403, detail="Web Chat shared API Key lacks files permission",
                                headers=_PRIVATE_HEADERS)
        try:
            body = FileUploadRequest.model_validate(await request.json())
        except (ValueError, TypeError, ValidationError) as error:
            raise HTTPException(status_code=422, detail="Invalid Web Chat file upload request",
                                headers=_PRIVATE_HEADERS) from error
        if body.purpose not in {"assistants", "vision"}:
            raise HTTPException(status_code=400, detail="Unsupported Web Chat file purpose",
                                headers=_PRIVATE_HEADERS)
        try:
            item = await request.app.state.file_store.create(
                filename=body.filename,
                data_base64=body.data_base64,
                mime_type=body.mime_type,
                owner_key_id=principal.key_id,
                purpose=body.purpose,
                source="web_chat",
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error),
                                headers=_PRIVATE_HEADERS) from error
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail="Web Chat file storage is unavailable",
                                headers=_PRIVATE_HEADERS) from error
        return JSONResponse({
            "id": item.file_id,
            "object": "file",
            "bytes": int(item.size),
            "filename": item.filename,
            "purpose": item.purpose,
            "status": "processed",
            "mime_type": item.mime_type,
        }, headers={
            **_PRIVATE_HEADERS,
            "X-Content-Type-Options": "nosniff",
        })

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
        "/assets/chat2api-chat-voice-client-v1.js": ("chat_voice_client_v1.js", "application/javascript"),
        "/assets/katex/0.19.1/katex.min.js": (
            "vendor/katex/katex.min.js", "application/javascript",
            "public, max-age=31536000, immutable",
        ),
        "/assets/katex/0.19.1/katex.min.css": (
            "vendor/katex/katex.min.css", "text/css",
            "public, max-age=31536000, immutable",
        ),
    }
    for route_path, spec in assets.items():
        filename, media_type = spec[:2]
        cache_control = spec[2] if len(spec) > 2 else "no-store"
        asset_path = Path(__file__).parent / filename
        if not asset_path.is_file():
            if route_path.startswith("/assets/katex/"):
                # Source checkouts may omit generated vendor files; rendering
                # fails closed to the text fallback. Production Docker images
                # always package the pinned local distribution.
                continue
            raise RuntimeError("Required Web Chat asset is missing: " + filename)
        app.add_api_route(
            route_path, _asset_handler(asset_path, media_type, cache_control),
            methods=["GET"], include_in_schema=False,
        )

    @app.get("/assets/katex/0.19.1/fonts/{font_name}", include_in_schema=False)
    async def serve_katex_font(font_name: str) -> Response:
        if not _KATEX_FONT_RE.fullmatch(font_name):
            raise HTTPException(status_code=404, detail="font not found")
        asset_path = Path(__file__).parent / "vendor" / "katex" / "fonts" / font_name
        if not asset_path.is_file():
            raise HTTPException(status_code=404, detail="font not found")
        suffix = asset_path.suffix.lower()
        media_type = {
            ".woff2": "font/woff2",
            ".woff": "font/woff",
            ".ttf": "font/ttf",
        }[suffix]
        return Response(
            asset_path.read_bytes(),
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
            },
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
