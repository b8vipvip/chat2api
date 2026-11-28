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
from .native_content_v1 import native_delivery_fingerprint, validate_native_snapshot
from .native_snapshot_finality_v282 import native_interaction_digest
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
    from .dot_v1 import install_dot_v1
    install_dot_v1(app)

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
        expected = request.headers.get("X-Chat2API-User-ID")
        if expected is not None and expected != str(user["user_id"]):
            raise HTTPException(status_code=409, detail="Web Chat account changed; reload the page",
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
        from .web_chat_identity_v1 import web_chat_model_decision
        if "models" not in principal.scopes:
            raise HTTPException(status_code=403, detail="Web Chat shared API Key lacks models permission",
                                headers=_PRIVATE_HEADERS)
        registry = getattr(request.app.state, "registry", None)
        if registry is None:
            raise HTTPException(status_code=503, detail="model catalog unavailable",
                                headers=_PRIVATE_HEADERS)
        # In production the live Worker library is the authority for concrete
        # picker choices. Historical sync tasks may contain stale/offline
        # models or omit a newly discovered picker after a failed sync.
        management_snapshot = getattr(request.app.state, "worker_text_model_management_v146_snapshot", None)
        management_state = management_snapshot() if callable(management_snapshot) else {}
        live_rows = management_state.get("data") if isinstance(management_state, dict) else None
        disabled_models = set(management_state.get("disabled") or []) if isinstance(management_state, dict) else set()
        excluded = {"gpt-image", "gpt-live", "gpt-live-mini", "chatgpt-web", "auto"}
        catalog = {
            str(row.get("id") or ""): dict(row)
            for row in registry.model_catalog(online_only=True)
            if isinstance(row, dict)
        }
        models: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add_model(model_id: str, row: dict[str, Any]) -> None:
            if not model_id or model_id in excluded or model_id in disabled_models or model_id in seen:
                return
            if web_chat_model_decision(request.app, principal, model_id).get("allowed") is not True:
                return
            seen.add(model_id)
            models.append({**row, "id": model_id})

        def add_default(mapping: str) -> None:
            if mapping in disabled_models:
                return
            add_model("default", {
                "label": mapping or "默认模型",
                "mapped_model_id": mapping or None,
                "capabilities": ["text"],
                "selectable": True,
                "source": "worker-available-default",
                "validated": False,
            })

        if isinstance(live_rows, list):
            default_mapping = str(management_state.get("legacy_minimum_model") or "").strip()
            if any(isinstance(row, dict) and row.get("id") == "default"
                   and row.get("enabled") is not False and row.get("routable_by")
                   for row in live_rows):
                add_default(default_mapping)
            for model_id, row in catalog.items():
                if model_id == "default" or not row.get("clients"):
                    continue
                if row.get("selectable") is False and row.get("validated") is not True:
                    # The mapped Free model is a compatibility route, not a
                    # concrete picker selection. It is represented by default.
                    continue
                if "text" not in (row.get("capabilities") or []):
                    continue
                add_model(model_id, row)
        else:
            # Older/focused composition roots without a live management
            # projection retain their historical catalog contract.
            model_task_snapshot = getattr(request.app.state, "worker_model_tasks_v147_snapshot", None)
            if callable(model_task_snapshot):
                try:
                    available_payload = await model_task_snapshot()
                except Exception as error:
                    raise HTTPException(status_code=503, detail="available model catalogue unavailable",
                                        headers=_PRIVATE_HEADERS) from error
                mapping = str(available_payload.get("available_models_default_mapping") or "").strip()
                for available in available_payload.get("available_models") or []:
                    if not isinstance(available, dict):
                        continue
                    model_id = str(available.get("id") or "").strip()
                    if model_id == "default":
                        add_default(mapping)
                    else:
                        row = catalog.get(model_id, {
                            "object": "model", "created": 0,
                            "owned_by": "chat2api", "capabilities": ["text"],
                        })
                        add_model(model_id, {**row, "label": model_id, "selectable": True})
            else:
                minimum = getattr(registry, "legacy_minimum_model", None)
                mapping = str(minimum() or "").strip() if callable(minimum) else ""
                for model_id, row in catalog.items():
                    if model_id == "default":
                        add_default(mapping)
                    else:
                        add_model(model_id, row)
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

    pending_native_interactions: dict[str, dict[str, Any]] = {}
    native_interaction_locks: dict[str, asyncio.Lock] = {}
    native_canvas_refresh_at: dict[str, float] = {}

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
                "recapture_status": message.get("recapture_status"),
                "snapshot": message.get("snapshot"),
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
            if (
                any(type(item) not in (int, float) or not math.isfinite(item)
                    for item in (minimum, maximum, step))
                or minimum >= maximum or step <= 0
            ):
                raise HTTPException(status_code=422, detail="native range metadata is invalid", headers=_PRIVATE_HEADERS)
            units = (value - minimum) / step
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
        if tag == "button" and kind == "tab" and action == "set_selected" and value is True:
            group = control.get("group")
            if isinstance(group, str) and re.fullmatch(r"tg_[1-9][0-9]{0,3}", group):
                return
            raise HTTPException(status_code=422, detail="native tab group is invalid", headers=_PRIVATE_HEADERS)
        if tag == "button" and kind == "toggle" and action == "set_pressed" and type(value) is bool:
            return
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
            if isinstance(group, str) and group:
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
            else:
                control["checked"] = True
        elif node.get("tag") == "input" and control.get("kind") == "range":
            control["value"] = value
        elif node.get("tag") == "select":
            control["selected"] = value
            for option in node.get("children") or []:
                option_control = option.get("control") if isinstance(option, dict) else None
                if isinstance(option_control, dict) and option_control.get("kind") == "option":
                    option_control["selected"] = option_control.get("value") == value
        elif node.get("tag") == "button" and control.get("kind") == "tab":
            group = control.get("group")
            stack = [snapshot.get("root")]
            while stack:
                current = stack.pop()
                if not isinstance(current, dict):
                    continue
                other = current.get("control")
                if (current.get("tag") == "button" and isinstance(other, dict)
                        and other.get("kind") == "tab" and other.get("group") == group):
                    other["selected"] = current is node
                children = current.get("children")
                if isinstance(children, list):
                    stack.extend(children)
        elif node.get("tag") == "button" and control.get("kind") == "toggle":
            control["pressed"] = value

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
        canvas_refresh = interaction_id == "native_refresh" and action == "refresh_canvas" and value is None
        if (
            type(revision) is not int or not 1 <= revision < 1000000
            or not isinstance(interaction_id, str)
            or (not canvas_refresh and not re.fullmatch(r"nc_[1-9][0-9]{0,6}_[1-9][0-9]{0,3}", interaction_id))
            or not isinstance(action, str) or (not canvas_refresh and action not in {"set_open", "set_checked", "set_value", "set_selected", "set_pressed"})
        ):
            raise HTTPException(status_code=422, detail="invalid interaction envelope", headers=_PRIVATE_HEADERS)

        if request_id not in native_interaction_locks and len(native_interaction_locks) >= 128:
            for stale_request_id, stale_lock in list(native_interaction_locks.items()):
                if not stale_lock.locked():
                    native_interaction_locks.pop(stale_request_id, None)
                    if len(native_interaction_locks) < 128:
                        break
        if request_id not in native_interaction_locks and len(native_interaction_locks) >= 128:
            raise HTTPException(status_code=429, detail="native interaction lock capacity reached",
                                headers=_PRIVATE_HEADERS)
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
            def canvas_count(root):
                stack, count = [root], 0
                while stack:
                    node = stack.pop()
                    if not isinstance(node,dict):
                        continue
                    count += int(node.get("tag") == "canvas_raster")
                    stack.extend(node.get("children") or [])
                return count
            target = find_native_interaction(snapshot.get("root"), interaction_id)
            if canvas_refresh:
                if not canvas_count(snapshot.get("root")):
                    raise HTTPException(status_code=404, detail="native canvas not found", headers=_PRIVATE_HEADERS)
                now = asyncio.get_running_loop().time()
                if now - native_canvas_refresh_at.get(request_id, -1000) < 15:
                    raise HTTPException(status_code=429, detail="canvas refresh rate limit", headers=_PRIVATE_HEADERS)
                native_canvas_refresh_at[request_id] = now
                if len(native_canvas_refresh_at)>128:
                    native_canvas_refresh_at.pop(next(iter(native_canvas_refresh_at)))
            else:
                if target is None:
                    raise HTTPException(status_code=404, detail="native control not found", headers=_PRIVATE_HEADERS)
                validate_native_interaction(target, action, value)
            store = getattr(request.app.state, "native_content", None)
            stored_before = store.get(request_id, owner) if store is not None else None
            stored_target = (
                find_native_interaction(stored_before.get("root"), interaction_id)
                if isinstance(stored_before, dict) else None
            )
            if (
                not isinstance(stored_before, dict)
                or stored_before.get("revision") != revision
                or (not canvas_refresh and (
                    not isinstance(stored_target, dict)
                    or stored_target.get("tag") != target.get("tag")
                    or stored_target.get("control") != target.get("control")
                ))
            ):
                raise HTTPException(status_code=409, detail="native snapshot changed; refresh and retry",
                                    headers=_PRIVATE_HEADERS)

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
                action_message = {
                    "type": "chat.native.interaction",
                    "request_id": request_id,
                    "action_id": action_id,
                    "revision": revision,
                    "interaction_id": interaction_id,
                    "action": action,
                    "value": value,
                }
                try:
                    await request.app.state.registry.send(client_id, action_message)
                except (RuntimeError, KeyError) as error:
                    raise HTTPException(status_code=503, detail="original Worker is unavailable", headers=_PRIVATE_HEADERS) from error
                try:
                    acknowledgement = await asyncio.wait_for(future, timeout=3.0)
                except asyncio.TimeoutError:
                    # The Worker may have applied the action while its first
                    # ACK was lost. Re-send the same server-generated action
                    # ID; the content script deduplicates it and returns the
                    # original result without applying it twice.
                    retry_future = asyncio.get_running_loop().create_future()
                    pending_native_interactions[action_id]["future"] = retry_future
                    try:
                        await request.app.state.registry.send(client_id, action_message)
                    except (RuntimeError, KeyError) as error:
                        raise HTTPException(status_code=503, detail="original Worker route was lost during retry",
                                            headers=_PRIVATE_HEADERS) from error
                    try:
                        acknowledgement = await asyncio.wait_for(retry_future, timeout=2.0)
                    except asyncio.TimeoutError as error:
                        raise HTTPException(status_code=504, detail="Worker native control acknowledgement timed out",
                                            headers=_PRIVATE_HEADERS) from error
            finally:
                pending_native_interactions.pop(action_id, None)

            if not acknowledgement.get("ok"):
                raise HTTPException(status_code=409, detail=acknowledgement.get("error") or "Worker rejected native control action",
                                    headers=_PRIVATE_HEADERS)

            stored = store.get(request_id, owner) if store is not None else None
            if not isinstance(stored, dict) or stored.get("revision") != revision:
                raise HTTPException(status_code=409, detail="native snapshot changed during interaction; reload the conversation",
                                    headers=_PRIVATE_HEADERS)
            recaptured = acknowledgement.get("recapture_status") == "verified"
            if canvas_refresh and acknowledgement.get("recapture_status") == "unchanged":
                return JSONResponse({"ok":True,"revision":revision,"persisted":True,"unchanged":True},
                                    headers=_PRIVATE_HEADERS)
            if canvas_refresh and not recaptured:
                raise HTTPException(status_code=409, detail="fresh canvas pixels unavailable", headers=_PRIVATE_HEADERS)
            if recaptured:
                try:
                    raw = acknowledgement.get("snapshot")
                    if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > 768 * 1024:
                        raise ValueError("interaction snapshot is too large")
                    updated = validate_native_snapshot(raw)
                    if updated["revision"] != revision + 1:
                        raise ValueError("interaction snapshot revision mismatch")
                    if canvas_refresh and canvas_count(updated["root"]) != canvas_count(stored["root"]):
                        raise ValueError("canvas refresh lost a verified raster")
                    # Image indices are request archive bindings. A dynamic
                    # recapture cannot invent or reorder those bindings.
                    for root in (stored["root"], updated["root"]):
                        stack = [root]
                        while stack:
                            node = stack.pop()
                            if node.get("tag") == "img":
                                raise ValueError("interaction raster recapture unsupported")
                            stack.extend(node.get("children") or [])
                except (ValueError, TypeError, KeyError):
                    raise HTTPException(status_code=409, detail="Worker action applied but recaptured DOM could not be validated",
                                        headers=_PRIVATE_HEADERS) from None
            else:
                updated = copy.deepcopy(stored)
                apply_native_interaction(updated, interaction_id, action, value)
                updated["revision"] = revision + 1
            if not store.publish(request_id, owner, updated):
                raise HTTPException(status_code=409, detail="native interaction state could not be committed",
                                    headers=_PRIVATE_HEADERS)
            persisted = bool(store.persist(request_id, owner))
            if persisted:
                try:
                    # Retain the original terminal evidence rather than rewriting
                    # Worker completion diagnostics with an interactive revision.
                    previous = row.get("native_interaction_proof")
                    base = revision
                    if (isinstance(previous, dict)
                            and previous.get("revision") == revision
                            and previous.get("sha256") == native_interaction_digest(stored)
                            and type(previous.get("base_revision")) is int
                            and 1 <= previous["base_revision"] < revision):
                        base = previous["base_revision"]
                    await request.app.state.telemetry.update(request_id, {
                        "native_interaction_proof": {
                            "base_revision": base, "revision": revision + 1,
                            "sha256": native_interaction_digest(updated),
                            "recaptured": recaptured,
                        }
                    })
                except (KeyError, ValueError, OSError):
                    persisted = False
            return JSONResponse({
                "ok": True,
                "revision": revision + 1,
                "persisted": persisted,
                **({"recaptured": recaptured} if acknowledgement.get("recapture_status") else {}),
                **({"warning": "interaction applied but durable snapshot persistence is pending"} if not persisted else
                   {"warning": "interaction applied; full DOM recapture unavailable"}
                   if acknowledgement.get("recapture_status") == "unavailable" else {}),
            }, headers={**_PRIVATE_HEADERS, "X-Content-Type-Options": "nosniff"})

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
        if path.startswith(("/api/user/chat/dot", "/chat/dot", "/api/user/chat/completions", "/api/user/chat/conversations", "/api/user/chat/requests/", "/api/user/chat/files/", "/api/user/chat/generated-files/", "/api/user/chat/library", "/api/user/files/")):
            response.headers["Cache-Control"] = "private, no-store"
            vary_values = [value.strip() for value in response.headers.get("Vary", "").split(",") if value.strip()]
            if not any(value.lower() == "cookie" for value in vary_values):
                vary_values.append("Cookie")
            response.headers["Vary"] = ", ".join(vary_values)
        return response
    app.state.web_chat_features_v1_installed = True
    app.state.web_chat_integration_version = 1
    return app
