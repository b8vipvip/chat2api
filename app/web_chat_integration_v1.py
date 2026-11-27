"""Production installer for the safe Web Chat renderer and recovery/file APIs."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import uuid
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
            management_snapshot = getattr(request.app.state, "worker_text_model_management_v146_snapshot", None)
            management_state = management_snapshot() if callable(management_snapshot) else {}
            disabled_models = {
                str(model_id) for model_id in (management_state.get("disabled") or [])
            } if isinstance(management_state, dict) else set()
            catalog = {
                str(row.get("id") or ""): dict(row)
                for row in registry.model_catalog(online_only=True)
                if isinstance(row, dict)
            }
            for available in available_rows:
                if not isinstance(available, dict):
                    continue
                model_id = str(available.get("id") or "").strip()
                if (
                    not model_id
                    or model_id in {"gpt-image", "gpt-live", "gpt-live-mini", "chatgpt-web"}
                    or model_id in disabled_models
                ):
                    continue
                if model_id == "default":
                    if default_mapping and default_mapping in disabled_models:
                        continue
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
    pending_native_interactions: dict[str, dict[str, Any]] = {}
    native_interaction_locks: dict[str, asyncio.Lock] = {}

    async def receive_native_interaction_ack(client_id: str, websocket: Any, message: dict[str, Any]) -> None:
        action_id = str(message.get("action_id") or "")
        pending = pending_native_interactions.get(action_id)
        if (not pending or pending.get("client_id") != client_id
                or pending.get("websocket") is not websocket):
            return
        if (
            pending.get("request_id") != str(message.get("request_id") or "")
            or pending.get("revision") != message.get("revision")
            or pending.get("interaction_id") != str(message.get("interaction_id") or "")
        ):
            return
        future = pending.get("future")
        if isinstance(future, asyncio.Future) and not future.done():
            future.set_result({
                "ok": message.get("ok") is True,
                "revision": message.get("revision"),
                "error": str(message.get("error") or "")[:120],
            })

    app.state.native_interaction_ack_receiver = receive_native_interaction_ack

    def find_native_interaction(root: object, interaction_id: str) -> dict[str, Any] | None:
        stack = [root]
        visited = 0
        while stack and visited < 30000:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            visited += 1
            if node.get("interaction_id") == interaction_id:
                return node
            children = node.get("children")
            if isinstance(children, list):
                stack.extend(reversed(children))
        return None

    def validate_native_interaction(node: dict[str, Any], action: str, value: object) -> None:
        tag = node.get("tag")
        control = node.get("control") if isinstance(node.get("control"), dict) else {}
        if control.get("disabled") is True:
            raise HTTPException(status_code=409, detail="native control is disabled", headers=_PRIVATE_HEADERS)
        kind = control.get("kind")
        if tag == "details" and kind == "disclosure" and action == "set_open" and type(value) is bool:
            return
        if tag == "input" and kind == "checkbox" and action == "set_checked" and type(value) is bool:
            return
        if tag == "input" and kind == "radio" and action == "set_checked" and value is True:
            return
        if tag == "input" and kind == "range" and action == "set_value":
            if type(value) not in (int, float) or not math.isfinite(value):
                raise HTTPException(status_code=422, detail="invalid range value", headers=_PRIVATE_HEADERS)
            minimum, maximum, step = (control.get(key) for key in ("min", "max", "step"))
            units = (value - minimum) / step if step else math.inf
            if (value < minimum or value > maximum
                    or not math.isfinite(units) or abs(units - round(units)) > 1e-6):
                raise HTTPException(status_code=422, detail="range value outside allowed steps", headers=_PRIVATE_HEADERS)
            return
        if tag == "select" and kind == "select" and action == "set_value" and isinstance(value, str):
            options = node.get("children") if isinstance(node.get("children"), list) else []
            if any(
                isinstance(option, dict)
                and isinstance(option.get("control"), dict)
                and option["control"].get("kind") == "option"
                and option["control"].get("value") == value
                and option["control"].get("disabled") is not True
                for option in options
            ):
                return
            raise HTTPException(status_code=422, detail="select option is not allowed", headers=_PRIVATE_HEADERS)
        raise HTTPException(status_code=422, detail="native control action is not allowed", headers=_PRIVATE_HEADERS)

    def apply_native_interaction(snapshot: dict[str, Any], interaction_id: str, action: str, value: object) -> None:
        node = find_native_interaction(snapshot.get("root"), interaction_id)
        if node is None:
            raise ValueError("native interaction target disappeared")
        control = node.get("control") if isinstance(node.get("control"), dict) else {}
        if node.get("tag") == "details":
            control["open"] = value
        elif node.get("tag") == "input" and control.get("kind") == "checkbox":
            control["checked"] = value
        elif node.get("tag") == "input" and control.get("kind") == "radio":
            group = control.get("group")
            stack = [snapshot.get("root")]
            while stack:
                current = stack.pop()
                if not isinstance(current, dict):
                    continue
                other = current.get("control")
                if (current.get("tag") == "input" and isinstance(other, dict)
                        and other.get("kind") == "radio" and other.get("group") == group):
                    other["checked"] = current is node
                children = current.get("children")
                if isinstance(children, list):
                    stack.extend(children)
        elif node.get("tag") == "input" and control.get("kind") == "range":
            control["value"] = value
        elif node.get("tag") == "select":
            control["selected"] = value
            for option in node.get("children") or []:
                option_control = option.get("control") if isinstance(option, dict) else None
                if isinstance(option_control, dict) and option_control.get("kind") == "option":
                    option_control["selected"] = option_control.get("value") == value

    @app.post("/api/user/chat/requests/{request_id}/native-content/interactions")
    async def user_chat_native_content_interaction(request_id: str, request: Request) -> Response:
        if not same_origin_admin_request(request):
            raise HTTPException(status_code=403, detail="Same-origin request required", headers=_PRIVATE_HEADERS)
        accounts, user, owner = owned_request(request_id, request)
        try:
            body = await request.json()
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="invalid interaction body", headers=_PRIVATE_HEADERS)
        if not isinstance(body, dict) or set(body) != {"revision", "interaction_id", "action", "value"}:
            raise HTTPException(status_code=422, detail="invalid interaction schema", headers=_PRIVATE_HEADERS)
        revision = body.get("revision")
        interaction_id = body.get("interaction_id")
        action = body.get("action")
        value = body.get("value")
        if (
            type(revision) is not int or not 1 <= revision < 1000000
            or not isinstance(interaction_id, str)
            or not re.fullmatch(r"nc_[1-9][0-9]{0,6}_[1-9][0-9]{0,3}", interaction_id)
            or not isinstance(action, str) or action not in {"set_open", "set_checked", "set_value"}
        ):
            raise HTTPException(status_code=422, detail="invalid interaction envelope", headers=_PRIVATE_HEADERS)

        lock = native_interaction_locks.setdefault(request_id, asyncio.Lock())
        async with lock:
            row = request.app.state.telemetry.get(request_id) or {}
            if str(row.get("status") or "").lower() != "completed":
                raise HTTPException(status_code=409, detail="native controls are available after completion", headers=_PRIVATE_HEADERS)
            snapshot = await user_native_snapshot(request_id, request)
            if not snapshot or snapshot.get("finality") != "verified-final":
                raise HTTPException(status_code=409, detail="verified native snapshot is not ready", headers=_PRIVATE_HEADERS)
            if snapshot.get("revision") != revision:
                raise HTTPException(status_code=409, detail="native snapshot revision changed; refresh and retry", headers=_PRIVATE_HEADERS)
            target = find_native_interaction(snapshot.get("root"), interaction_id)
            if target is None:
                raise HTTPException(status_code=404, detail="native control not found", headers=_PRIVATE_HEADERS)
            validate_native_interaction(target, action, value)

            client_id = str(row.get("client_id") or "")
            if not client_id:
                raise HTTPException(status_code=503, detail="original Worker route unavailable", headers=_PRIVATE_HEADERS)
            if len(pending_native_interactions) >= 128:
                raise HTTPException(status_code=429, detail="too many pending native interactions", headers=_PRIVATE_HEADERS)
            action_id = uuid.uuid4().hex
            future = asyncio.get_running_loop().create_future()
            pending_native_interactions[action_id] = {
                "client_id": client_id,
                "websocket": request.app.state.registry.sockets.get(client_id),
                "request_id": request_id,
                "revision": revision,
                "interaction_id": interaction_id,
                "future": future,
            }
            try:
                try:
                    await request.app.state.registry.send(client_id, {
                        "type": "chat.native.interaction",
                        "request_id": request_id,
                        "action_id": action_id,
                        "revision": revision,
                        "interaction_id": interaction_id,
                        "action": action,
                        "value": value,
                    })
                except (RuntimeError, KeyError) as error:
                    raise HTTPException(status_code=503, detail="original Worker is unavailable", headers=_PRIVATE_HEADERS) from error
                try:
                    acknowledgement = await asyncio.wait_for(future, timeout=5.0)
                except asyncio.TimeoutError as error:
                    raise HTTPException(status_code=504, detail="Worker native control acknowledgement timed out", headers=_PRIVATE_HEADERS) from error
            finally:
                pending_native_interactions.pop(action_id, None)

            if not acknowledgement.get("ok"):
                raise HTTPException(status_code=409, detail=acknowledgement.get("error") or "Worker rejected native control action",
                                    headers=_PRIVATE_HEADERS)

            store = getattr(request.app.state, "native_content", None)
            stored = store.get(request_id, owner) if store is not None else None
            if not isinstance(stored, dict) or stored.get("revision") != revision:
                raise HTTPException(status_code=409, detail="native snapshot changed during interaction; reload the conversation",
                                    headers=_PRIVATE_HEADERS)
            updated = copy.deepcopy(stored)
            apply_native_interaction(updated, interaction_id, action, value)
            updated["revision"] = revision + 1
            if not store.publish(request_id, owner, updated):
                raise HTTPException(status_code=409, detail="native interaction state could not be committed",
                                    headers=_PRIVATE_HEADERS)
            persisted = bool(store.persist(request_id, owner))
            return JSONResponse({
                "ok": True,
                "revision": revision + 1,
                "persisted": persisted,
                **({"warning": "interaction applied but durable snapshot persistence is pending"} if not persisted else {}),
            }, headers={**_PRIVATE_HEADERS, "X-Content-Type-Options": "nosniff"})

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
