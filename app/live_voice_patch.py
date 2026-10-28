from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
import uuid
from contextlib import suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from .api_keys import ApiPrincipal
from .responses_multimodal_input_v183 import attachment_specs

LIVE_PROTOCOL_VERSION = "chat2api-live-v1"
SUPPORTED_MODELS = {"gpt-live", "gpt-live-mini"}
MODEL_ALIASES = {"gpt-live-mini": "gpt-live"}
REALTIME_SESSION_TTL_SECONDS = 60
LIVE_CAPACITY_WEIGHT = 2


def _bearer(headers: Any) -> str:
    authorization = str(headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return str(headers.get("x-api-key") or "").strip()


def _token_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _input_text_from_control(control: Any) -> str:
    if not isinstance(control, dict):
        return ""
    control_type = str(control.get("type") or "")
    if control_type == "input_text":
        return str(control.get("text") or "").strip()
    if control_type != "conversation.item.create":
        return ""
    item = control.get("item")
    if not isinstance(item, dict) or str(item.get("role") or "user") != "user":
        return ""
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            if part.strip():
                parts.append(part.strip())
            continue
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "") not in {"input_text", "text"}:
            continue
        value = str(part.get("text") or part.get("input_text") or "").strip()
        if value:
            parts.append(value)
    return "\n".join(parts).strip()


def _media_ids_from_control(control: dict[str, Any]) -> list[str]:
    """FileStore IDs only; no arbitrary URLs/base64 over a live control frame."""
    kind = str(control.get("type") or "")
    parts = []
    if kind == "input_media":
        parts = control.get("attachments") or []
    elif kind == "conversation.item.create":
        item = control.get("item") if isinstance(control.get("item"), dict) else {}
        parts = item.get("content") or []
    if isinstance(parts, str) and kind == "conversation.item.create":
        return []
    if not isinstance(parts, list):
        raise ValueError("Media content must be an array")
    ids: list[str] = []
    for part in parts:
        if isinstance(part, str) and kind == "conversation.item.create":
            continue
        if not isinstance(part, dict):
            raise ValueError("Media item must be an object")
        ptype = str(part.get("type") or "")
        if ptype in {"input_text", "text"}:
            continue
        if ptype not in {"input_image", "input_file", "image_url", "file"}:
            raise ValueError(f"Unsupported live media part: {ptype}")
        ref = part.get("file_id")
        if ref is None and isinstance(part.get("image_url"), str):
            ref = part["image_url"]
        if not isinstance(ref, str) or not ref.startswith("file_"):
            raise ValueError("Live images/files require an uploaded file_id (no remote URLs)")
        ids.append(ref)
    if len(ids) > 4:
        raise ValueError("At most four media attachments per turn")
    return list(dict.fromkeys(ids))


def install_live_voice_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    broker = app.state.broker
    api_keys = app.state.api_keys
    file_store = app.state.file_store
    telemetry = app.state.telemetry
    realtime_sessions: dict[str, tuple[float, ApiPrincipal]] = {}
    realtime_sessions_lock = asyncio.Lock()

    async def authenticate_managed_token(token: str) -> ApiPrincipal | None:
        supplied = str(token or "").strip()
        if not supplied:
            return None
        principal = await api_keys.authenticate(supplied)
        if not principal:
            return None
        if "audio" not in principal.scopes and "chat" not in principal.scopes:
            return None
        return principal

    async def issue_realtime_session(principal: ApiPrincipal) -> str:
        value = "rt-chat2api-" + secrets.token_urlsafe(32)
        digest = _token_hash(value)
        now = time.monotonic()
        async with realtime_sessions_lock:
            expired = [key for key, (deadline, _) in realtime_sessions.items() if deadline <= now]
            for key in expired:
                realtime_sessions.pop(key, None)
            realtime_sessions[digest] = (now + REALTIME_SESSION_TTL_SECONDS, principal)
        return value

    async def consume_realtime_session(token: str) -> ApiPrincipal | None:
        digest = _token_hash(token)
        now = time.monotonic()
        async with realtime_sessions_lock:
            item = realtime_sessions.pop(digest, None)
            expired = [key for key, (deadline, _) in realtime_sessions.items() if deadline <= now]
            for key in expired:
                realtime_sessions.pop(key, None)
        if not item:
            return None
        deadline, principal = item
        return principal if deadline > now else None

    async def authenticate_websocket(websocket: WebSocket, session_token: str | None) -> ApiPrincipal | None:
        supplied = _bearer(websocket.headers)
        if supplied:
            return await authenticate_managed_token(supplied)
        if session_token:
            return await consume_realtime_session(session_token)
        return None

    def can_accept(client_id: str, weight: int = LIVE_CAPACITY_WEIGHT) -> bool:
        checker = getattr(broker, "can_accept", None)
        if callable(checker):
            try:
                return bool(checker(client_id, weight))
            except Exception:
                return False
        return client_id not in registry.busy_clients

    def resolve_idle_client(requested: str | None, principal: ApiPrincipal) -> str:
        registry.set_routing_key(principal.key_id)
        if requested:
            selected = registry.resolve_client(requested)
            if not can_accept(selected, LIVE_CAPACITY_WEIGHT):
                raise LookupError("Requested Chrome extension has no GPT-Live capacity")
            return selected

        online = registry.online_client_ids()
        if not online:
            raise ConnectionError("No Chrome extension is online. Open Chrome with a paired chat2api extension.")
        idle = [client_id for client_id in online if can_accept(client_id, LIVE_CAPACITY_WEIGHT)]
        if not idle:
            raise LookupError("All online Chrome extensions are at GPT-Live capacity")
        previous = registry.route_for_key(principal.key_id)
        if previous in idle:
            return previous
        selected = secrets.choice(idle)
        # Resolve explicitly so the existing registry persists the API-key sticky route.
        return registry.resolve_client(selected)

    @app.post("/v1/audio/realtime/sessions")
    async def create_live_voice_session(request: Request) -> dict[str, object]:
        principal = await authenticate_managed_token(_bearer(request.headers))
        if principal is None:
            raise HTTPException(status_code=401, detail="Invalid or unauthorized managed API key")
        token = await issue_realtime_session(principal)
        return {
            "object": "realtime.session_token",
            "protocol": LIVE_PROTOCOL_VERSION,
            "expires_in": REALTIME_SESSION_TTL_SECONDS,
            "session_token": token,
            "websocket_path": f"/v1/audio/realtime?session_token={token}",
            "input_audio_format": "pcm16le-16000-mono",
            "output_audio_format": "pcm16le-24000-mono",
            "supports": ["binary_pcm_input", "binary_pcm_output", "input_text", "conversation.item.create", "input_media", "barge_in"],
        }

    @app.websocket("/v1/audio/realtime")
    async def live_voice_socket(
        websocket: WebSocket,
        client_id: str | None = Query(default=None),
        session_token: str | None = Query(default=None),
    ) -> None:
        principal = await authenticate_websocket(websocket, session_token)
        if principal is None:
            await websocket.close(code=4401, reason="Invalid or unauthorized API key/session token")
            return
        await websocket.accept()

        request_id = "live_" + uuid.uuid4().hex
        session_id = "gptlive_" + uuid.uuid4().hex
        state = None
        selected_client: str | None = None
        relay_task: asyncio.Task[None] | None = None
        started = False
        finished = False
        requested_model = "gpt-live"
        effective_model = "gpt-live"
        observed_audio_bytes = 0
        observed_media_count = 0
        input_confirmed = 0
        session_ready = False
        error_message = ""
        graceful_finish = False

        # Session controls and remote audio are sent from separate coroutines.
        # Serialize WebSocket sends so binary PCM frames cannot collide with
        # text acknowledgements during a full-duplex Voice session.
        ws_send_lock = asyncio.Lock()

        async def send_json(payload: dict[str, object]) -> None:
            if finished:
                return
            async with ws_send_lock:
                await websocket.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

        async def relay_extension_events() -> None:
            nonlocal finished, observed_audio_bytes, observed_media_count, input_confirmed, session_ready, error_message
            assert state is not None
            text_by_response: dict[str, str] = {}
            while not finished:
                event = await state.queue.get()
                # The extension emits its existing terminal image.error with
                # kind="voice-live". Previously that event fell through the
                # voice-live branch (empty live_event) and was swallowed. The
                # test then waited 55s and telemetry recorded error=null.
                if event.get("type") == "image.error":
                    error_message = str(event.get("error") or "ChatGPT Voice bridge failed")
                    await send_json({
                        "type": "error", "code": "GPT_LIVE_BROWSER_ERROR",
                        "message": error_message, "retryable": True,
                    })
                    return
                if event.get("kind") != "voice-live":
                    continue
                event_type = str(event.get("live_event") or "")
                if event_type == "session.ready":
                    session_ready = True
                    payload: dict[str, object] = {
                        "type": "session.ready",
                        "session_id": session_id,
                        "protocol": LIVE_PROTOCOL_VERSION,
                        "model": requested_model,
                        "effective_model": effective_model,
                        "input_audio_format": "pcm16le-16000-mono",
                        "output_audio_format": "pcm16le-24000-mono",
                        "supports": ["input_text", "conversation.item.create", "input_media", "image_file_id", "document_file_id"],
                    }
                    if requested_model != effective_model:
                        payload["model_alias_of"] = effective_model
                    await send_json(payload)
                elif event_type == "input.speech_started":
                    await send_json({"type": "input_audio_buffer.speech_started"})
                elif event_type == "input.speech_stopped":
                    await send_json({"type": "input_audio_buffer.speech_stopped"})
                elif event_type == "input.text.sent":
                    input_confirmed += 1
                    await send_json({
                        "type": "input_text.sent",
                        "item_id": str(event.get("item_id") or ""),
                        "text": str(event.get("text") or ""),
                    })
                elif event_type == "input.media.sent":
                    input_confirmed += 1
                    observed_media_count += int(event.get("attachment_count") or 0)
                    await send_json({
                        "type": "input_media.sent",
                        "item_id": str(event.get("item_id") or ""),
                        "attachment_count": int(event.get("attachment_count") or 0),
                    })
                elif event_type == "input.error":
                    await send_json({
                        "type": "error", "code": "VOICE_CALL_INPUT_FAILED",
                        "item_id": str(event.get("item_id") or ""),
                        "message": str(event.get("message") or "Media input was not accepted by the ChatGPT Voice composer"),
                        "retryable": True,
                    })
                elif event_type == "transcript.final":
                    text = str(event.get("text") or "").strip()
                    if text:
                        await send_json({"type": "transcript.final", "text": text})
                elif event_type == "response.created":
                    response_id = str(event.get("response_id") or uuid.uuid4())
                    text_by_response[response_id] = ""
                    await send_json({"type": "response.created", "response_id": response_id})
                elif event_type == "response.text.snapshot":
                    response_id = str(event.get("response_id") or "")
                    text = str(event.get("text") or "")
                    if not response_id or not text:
                        continue
                    previous = text_by_response.get(response_id, "")
                    if text.startswith(previous):
                        delta = text[len(previous):]
                    else:
                        delta = text
                    text_by_response[response_id] = text
                    if delta:
                        await send_json({"type": "response.text.delta", "response_id": response_id, "delta": delta})
                elif event_type == "response.audio.started":
                    response_id = str(event.get("response_id") or "")
                    if response_id:
                        await send_json({"type": "response.audio.started", "response_id": response_id})
                elif event_type == "response.audio.delta":
                    encoded = str(event.get("pcm_base64") or "")
                    if encoded:
                        try:
                            audio = base64.b64decode(encoded, validate=True)
                        except Exception:
                            continue
                        if audio and not finished:
                            async with ws_send_lock:
                                await websocket.send_bytes(audio)
                            observed_audio_bytes += len(audio)
                elif event_type == "response.audio.done":
                    response_id = str(event.get("response_id") or "")
                    if response_id:
                        await send_json({"type": "response.audio.done", "response_id": response_id})
                elif event_type == "response.done":
                    response_id = str(event.get("response_id") or "")
                    if response_id:
                        await send_json({
                            "type": "response.done",
                            "response_id": response_id,
                            "text": text_by_response.pop(response_id, str(event.get("text") or "")),
                        })
                elif event_type == "response.interrupted":
                    response_id = str(event.get("response_id") or "")
                    if response_id:
                        await send_json({
                            "type": "response.interrupted",
                            "response_id": response_id,
                            "reason": str(event.get("reason") or "barge_in"),
                        })
                elif event_type == "session.closed":
                    await send_json({"type": "session.closed", "session_id": session_id})
                    return
                elif event_type == "error":
                    error_message = str(event.get("message") or "GPT-Live bridge error")
                    await send_json({
                        "type": "error",
                        "code": str(event.get("code") or "GPT_LIVE_ERROR"),
                        "message": str(event.get("message") or "GPT-Live bridge error"),
                        "retryable": True,
                    })

        try:
            first = await asyncio.wait_for(websocket.receive(), timeout=15)
            if first.get("type") == "websocket.disconnect":
                return
            raw = first.get("text")
            if not raw:
                await send_json({"type": "error", "code": "SESSION_START_REQUIRED", "message": "First frame must be session.start"})
                return
            try:
                start = json.loads(raw)
            except Exception:
                await send_json({"type": "error", "code": "INVALID_JSON", "message": "session.start must be JSON"})
                return
            if start.get("type") != "session.start":
                await send_json({"type": "error", "code": "SESSION_START_REQUIRED", "message": "First frame must be session.start"})
                return
            requested_model = str(start.get("model") or "gpt-live").strip().lower()
            if requested_model not in SUPPORTED_MODELS:
                await send_json({"type": "error", "code": "UNSUPPORTED_MODEL", "message": f"Unsupported Live model: {requested_model}"})
                return
            effective_model = MODEL_ALIASES.get(requested_model, requested_model)
            provided_request_id = str(start.get("request_id") or "")
            if provided_request_id:
                if not re.fullmatch(r"voicereq_[A-Za-z0-9_-]{8,120}", provided_request_id):
                    await send_json({"type": "error", "code": "INVALID_REQUEST_ID", "message": "Invalid Voice call request ID"})
                    return
                request_id = provided_request_id
            requested_client = str(start.get("client_id") or client_id or "").strip() or None
            selected_client = resolve_idle_client(requested_client, principal)
            state = await broker.create(request_id, selected_client)
            registry.busy_clients.add(selected_client)
            await telemetry.append({
                "request_id": request_id,
                "response_id": session_id,
                "client_id": selected_client,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": effective_model,
                "request_type": "voice_call",
                "stream": True, "prompt_mode": "voice-call",
                "prompt_chars": len(str(start.get("instructions") or "")),
                "attachments_count": 0,
                "status": "running",
                "diagnostics": {"route": "chatgpt-voice-live", "session_id": session_id},
            })
            # Mark dispatch intent BEFORE the async send. A delivery can
            # reach the Worker even when the transport raises before ACK;
            # finally must still send the exact-session stop in that case.
            started = True
            await registry.send(
                selected_client,
                {
                    "type": "voice.live.start",
                    "request_id": request_id,
                    "session_id": session_id,
                    "routing": {"api_key_id": principal.key_id},
                    "options": {
                        "model": effective_model,
                        "requested_model": requested_model,
                        "model_alias_of": effective_model if requested_model != effective_model else None,
                        "instructions": str(start.get("instructions") or "")[:12000],
                        "input_sample_rate": 16000,
                        "output_sample_rate": 24000,
                    },
                },
            )
            relay_task = asyncio.create_task(relay_extension_events())

            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                payload = message.get("bytes")
                if payload is not None:
                    if payload:
                        await registry.send(
                            selected_client,
                            {
                                "type": "voice.live.audio",
                                "request_id": request_id,
                                "session_id": session_id,
                                "pcm_base64": base64.b64encode(payload).decode("ascii"),
                                "sample_rate": 16000,
                            },
                        )
                    continue
                text = message.get("text")
                if not text:
                    continue
                try:
                    control = json.loads(text)
                except Exception:
                    continue
                control_type = str(control.get("type") or "")
                if control_type == "response.cancel":
                    await registry.send(selected_client, {
                        "type": "voice.live.cancel_response",
                        "request_id": request_id,
                        "session_id": session_id,
                    })
                elif control_type in {"input_text", "conversation.item.create", "input_media"}:
                    item = control.get("item") if isinstance(control.get("item"), dict) else {}
                    if control_type == "conversation.item.create" and item.get("role", "user") != "user":
                        await send_json({"type": "error", "code": "INVALID_ITEM_ROLE", "message": "Only user input is accepted"})
                        continue
                    input_text = (
                        str(control.get("text") or "").strip()
                        if control_type == "input_media"
                        else _input_text_from_control(control)
                    )
                    try:
                        file_ids = _media_ids_from_control(control)
                        attachments = attachment_specs(file_store, file_ids, principal)
                        for spec in attachments:
                            mime = str(spec.get("mime_type") or "").lower()
                            allowed = (
                                mime in {"image/png", "image/jpeg", "image/webp", "image/gif",
                                         "application/pdf", "text/plain", "text/csv",
                                         "text/markdown", "application/json",
                                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
                            )
                            if not allowed:
                                raise ValueError("Live image/file MIME is not supported; send PCM as binary microphone frames")
                    except HTTPException as error:
                        await send_json({"type": "error", "code": "MEDIA_ACCESS_DENIED", "message": str(error.detail), "retryable": False})
                        continue
                    except ValueError as error:
                        await send_json({"type": "error", "code": "INVALID_MEDIA_INPUT", "message": str(error), "retryable": False})
                        continue
                    if not input_text and not attachments:
                        await send_json({"type": "error", "code": "EMPTY_INPUT", "message": "Provide text, image or file", "retryable": False})
                        continue
                    if len(input_text) > 12000:
                        await send_json({"type": "error", "code": "INPUT_TEXT_TOO_LONG", "message": "Live text input is limited to 12000 characters", "retryable": False})
                        continue
                    item_id = str(control.get("item_id") or item.get("id") or f"item_{uuid.uuid4().hex}")[:120]
                    await registry.send(selected_client, {
                        "type": "voice.live.multimodal" if attachments else "voice.live.text",
                        "request_id": request_id,
                        "session_id": session_id,
                        "item_id": item_id,
                        "text": input_text,
                        "attachments": attachments,
                    })
                    await send_json({
                        "type": "input_media.queued" if attachments else "input_text.queued",
                        "item_id": item_id, "text": input_text,
                        **({"attachment_count": len(attachments)} if attachments else {}),
                    })
                elif control_type == "session.finish":
                    graceful_finish = True
                    # A client-controlled finish is not evidence that the
                    # browser started Voice. Never record a false completion
                    # when Chrome has not confirmed its live session.
                    if not session_ready:
                        error_message = "GPT-Live session was not ready before session.finish"
                        await send_json({
                            "type": "error", "code": "VOICE_SESSION_NOT_READY",
                            "message": error_message, "retryable": True,
                        })
                    break
                elif control_type == "ping":
                    await send_json({"type": "pong", "timestamp": control.get("timestamp")})
        except (WebSocketDisconnect, asyncio.CancelledError):
            error_message = error_message or "client-disconnected"
        except asyncio.TimeoutError:
            error_message = "SESSION_START_TIMEOUT"
            with suppress(Exception):
                await send_json({"type": "error", "code": "SESSION_START_TIMEOUT", "message": "Timed out waiting for session.start"})
        except (LookupError, KeyError, ConnectionError, RuntimeError) as error:
            error_message = str(error)
            with suppress(Exception):
                await send_json({"type": "error", "code": "GPT_LIVE_UNAVAILABLE", "message": str(error), "retryable": True})
        except Exception as error:
            error_message = str(error)
            with suppress(Exception):
                await send_json({"type": "error", "code": "GPT_LIVE_BRIDGE_ERROR", "message": str(error), "retryable": True})
        finally:
            finished = True
            if started and selected_client:
                with suppress(Exception):
                    await registry.send(selected_client, {
                        "type": "voice.live.stop",
                        "request_id": request_id,
                        "session_id": session_id,
                    })
            if relay_task:
                relay_task.cancel()
                with suppress(asyncio.CancelledError):
                    await relay_task
            if selected_client:
                registry.busy_clients.discard(selected_client)
            if state is not None:
                state.completed_mono = state.completed_mono or time.perf_counter()
                with suppress(Exception):
                    await telemetry.append({
                        "request_id": request_id,
                        "response_id": session_id,
                        "client_id": selected_client,
                        "api_key_id": principal.key_id,
                        "api_key_name": principal.name,
                        "auth_kind": principal.kind,
                        "requested_model": effective_model,
                        "request_type": "voice_call",
                        "stream": True, "prompt_mode": "voice-call",
                        "status": "completed" if session_ready and (graceful_finish or observed_audio_bytes) else
                                  "cancelled" if error_message == "client-disconnected" else "error",
                        "attachments_count": observed_media_count,
                        "audio_bytes": observed_audio_bytes,
                        "diagnostics": {
                            # Preserve request-scoped bridge/ICE diagnostics collected
                            # by broker; this final telemetry record is the sole owner.
                            **state.diagnostics,
                            "route": "chatgpt-voice-live", "session_id": session_id,
                            "session_ready": session_ready, "audio_bytes": observed_audio_bytes,
                            "input_confirmed_turns": input_confirmed,
                            "attachments_confirmed": observed_media_count,
                            "browser_startup_error": error_message if error_message and not session_ready else None,
                        },
                        "timings": state.timings(),
                        "error": error_message or None,
                    })
                await broker.release(request_id)
            with suppress(Exception):
                await websocket.close(code=1000)

    return app
