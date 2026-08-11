from __future__ import annotations

import asyncio
import base64
import json
import secrets
import uuid
from contextlib import suppress
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from .api_keys import ApiPrincipal

LIVE_PROTOCOL_VERSION = "chat2api-live-v1"
SUPPORTED_MODELS = {"gpt-live", "gpt-live-mini"}


def _master_principal() -> ApiPrincipal:
    return ApiPrincipal(
        key_id="master",
        name="CHAT2API_API_KEY",
        kind="master",
        scopes=("admin", "chat", "models", "files", "images", "audio"),
    )


def _bearer(headers: Any) -> str:
    authorization = str(headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return str(headers.get("x-api-key") or "").strip()


def install_live_voice_patch(app: FastAPI) -> FastAPI:
    settings = app.state.settings
    registry = app.state.registry
    broker = app.state.broker
    api_keys = app.state.api_keys

    async def authenticate(websocket: WebSocket) -> ApiPrincipal | None:
        supplied = _bearer(websocket.headers)
        if not supplied:
            return None
        if settings.api_key and secrets.compare_digest(supplied, settings.api_key):
            return _master_principal()
        principal = await api_keys.authenticate(supplied)
        if not principal:
            return None
        if "audio" not in principal.scopes and "chat" not in principal.scopes:
            return None
        return principal

    def resolve_client(requested: str | None) -> str:
        return registry.resolve_client(requested)

    @app.websocket("/v1/audio/realtime")
    async def live_voice_socket(
        websocket: WebSocket,
        client_id: str | None = Query(default=None),
    ) -> None:
        principal = await authenticate(websocket)
        if principal is None:
            await websocket.close(code=4401, reason="Invalid or unauthorized API key")
            return
        await websocket.accept()

        request_id = "live_" + uuid.uuid4().hex
        session_id = "gptlive_" + uuid.uuid4().hex
        state = None
        selected_client: str | None = None
        relay_task: asyncio.Task[None] | None = None
        started = False
        finished = False

        async def send_json(payload: dict[str, object]) -> None:
            if finished:
                return
            await websocket.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

        async def relay_extension_events() -> None:
            nonlocal finished
            assert state is not None
            text_by_response: dict[str, str] = {}
            while not finished:
                event = await state.queue.get()
                if event.get("kind") != "voice-live":
                    if event.get("type") == "image.error":
                        await send_json({
                            "type": "error",
                            "code": "GPT_LIVE_BROWSER_ERROR",
                            "message": str(event.get("error") or "ChatGPT Voice bridge failed"),
                            "retryable": True,
                        })
                    continue
                event_type = str(event.get("live_event") or "")
                if event_type == "session.ready":
                    await send_json({
                        "type": "session.ready",
                        "session_id": session_id,
                        "protocol": LIVE_PROTOCOL_VERSION,
                        "model": str(event.get("model") or "gpt-live"),
                    })
                elif event_type == "input.speech_started":
                    await send_json({"type": "input_audio_buffer.speech_started"})
                elif event_type == "input.speech_stopped":
                    await send_json({"type": "input_audio_buffer.speech_stopped"})
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
                            await websocket.send_bytes(audio)
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
            model = str(start.get("model") or "gpt-live").strip().lower()
            if model not in SUPPORTED_MODELS:
                await send_json({"type": "error", "code": "UNSUPPORTED_MODEL", "message": f"Unsupported Live model: {model}"})
                return
            requested_client = str(start.get("client_id") or client_id or "").strip() or None
            selected_client = resolve_client(requested_client)
            state = await broker.create(request_id, selected_client)
            registry.busy_clients.add(selected_client)
            await registry.send(
                selected_client,
                {
                    "type": "voice.live.start",
                    "request_id": request_id,
                    "session_id": session_id,
                    "options": {
                        "model": model,
                        "instructions": str(start.get("instructions") or "")[:12000],
                        "input_sample_rate": 16000,
                        "output_sample_rate": 24000,
                    },
                },
            )
            started = True
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
                control_type = control.get("type")
                if control_type == "response.cancel":
                    await registry.send(selected_client, {
                        "type": "voice.live.cancel_response",
                        "request_id": request_id,
                        "session_id": session_id,
                    })
                elif control_type == "session.finish":
                    break
                elif control_type == "ping":
                    await send_json({"type": "pong", "timestamp": control.get("timestamp")})
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except asyncio.TimeoutError:
            with suppress(Exception):
                await send_json({"type": "error", "code": "SESSION_START_TIMEOUT", "message": "Timed out waiting for session.start"})
        except (LookupError, KeyError, ConnectionError, RuntimeError) as error:
            with suppress(Exception):
                await send_json({"type": "error", "code": "GPT_LIVE_UNAVAILABLE", "message": str(error), "retryable": True})
        except Exception as error:
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
                await broker.release(request_id)
            with suppress(Exception):
                await websocket.close(code=1000)

    return app
