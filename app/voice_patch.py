from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .api_keys import ApiPrincipal
from .token_usage import usage_for

PATCH_VERSION = "0.6.0"


class SpeechRequest(BaseModel):
    model: str = "gpt-live"
    input: str = Field(min_length=1, max_length=12000)
    voice: str | None = Field(default=None, max_length=80)
    response_format: Literal["b64_json"] = "b64_json"
    client_id: str | None = None
    timeout: int | None = Field(default=None, ge=30, le=600)


class VoiceConversationRequest(BaseModel):
    model: str = "gpt-live"
    audio_file_id: str
    instruction: str | None = Field(default=None, max_length=4000)
    response_format: Literal["b64_json"] = "b64_json"
    client_id: str | None = None
    timeout: int | None = Field(default=None, ge=30, le=600)


def _supplied_token(authorization: str | None, x_api_key: str | None) -> str:
    supplied = (x_api_key or "").strip()
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return supplied


def _master_principal() -> ApiPrincipal:
    return ApiPrincipal(
        key_id="master",
        name="CHAT2API_API_KEY",
        kind="master",
        scopes=("admin", "chat", "models", "files", "images", "audio"),
    )


def _audio_usage(prompt: str, transcript: str) -> tuple[dict[str, int], dict[str, object]]:
    usage = usage_for(prompt, transcript)
    return (
        {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens},
        {"estimated": usage.estimated, "estimator": usage.estimator},
    )


def install_voice_patch(app: FastAPI) -> FastAPI:
    settings = app.state.settings
    registry = app.state.registry
    broker = app.state.broker
    telemetry = app.state.telemetry
    api_keys = app.state.api_keys
    file_store = app.state.file_store

    async def require_audio_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> ApiPrincipal:
        supplied = _supplied_token(authorization, x_api_key)
        if not supplied:
            raise HTTPException(status_code=401, detail="Missing API key")
        if settings.api_key and secrets.compare_digest(supplied, settings.api_key):
            return _master_principal()
        principal = await api_keys.authenticate(supplied)
        if not principal:
            raise HTTPException(status_code=401, detail="Invalid or disabled API key")
        if "audio" not in principal.scopes and "chat" not in principal.scopes:
            raise HTTPException(status_code=403, detail="API key does not have audio permission")
        return principal

    def resolve_client(requested: str | None) -> str:
        try:
            return registry.resolve_client(requested)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ConnectionError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    async def wait_voice(state, timeout_seconds: int) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                event = await asyncio.wait_for(state.queue.get(), timeout=min(1.0, remaining))
            except asyncio.TimeoutError:
                continue
            event_type = str(event.get("type") or "")
            # Voice uses the already-supported image.* broker event namespace in v0.6.
            if event_type == "image.completed" and event.get("kind") == "voice":
                return event
            if event_type in {"image.error", "image.cancelled"} and event.get("kind") == "voice":
                raise RuntimeError(str(event.get("error") or event.get("reason") or "Voice request failed"))
        raise asyncio.TimeoutError

    async def record_voice(
        *, request_id: str, response_id: str, client_id: str, principal: ApiPrincipal,
        model: str, request_type: str, prompt: str, transcript: str, state,
        status_value: str, error: str | None = None, audio_bytes: int = 0,
    ) -> tuple[dict[str, int], dict[str, object]]:
        usage, token_meta = _audio_usage(prompt, transcript)
        diagnostics = dict(state.diagnostics)
        diagnostics["audio_bytes"] = int(audio_bytes)
        await telemetry.append(
            {
                "request_id": request_id,
                "response_id": response_id,
                "client_id": client_id,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": model,
                "request_type": request_type,
                "attachments_count": 1 if request_type == "voice_conversation" else 0,
                "stream": False,
                "prompt_mode": "voice",
                "prompt_chars": len(prompt),
                "completion_chars": len(transcript),
                "status": status_value,
                "usage": {**usage, **token_meta},
                "timings": state.timings(),
                "diagnostics": diagnostics,
                "error": error,
            }
        )
        return usage, token_meta

    async def execute_voice(
        *, principal: ApiPrincipal, model: str, prompt: str, client_id: str | None,
        timeout_seconds: int, audio_spec: dict[str, object] | None, request_type: str,
    ) -> dict[str, object]:
        request_id = "voicereq_" + uuid.uuid4().hex
        response_id = "voice_" + uuid.uuid4().hex
        selected_client = resolve_client(client_id)
        try:
            state = await broker.create(request_id, selected_client)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        registry.busy_clients.add(selected_client)
        try:
            await registry.send(
                selected_client,
                {
                    "type": "voice.request",
                    "request_id": request_id,
                    "prompt": prompt,
                    "audio": audio_spec,
                    "options": {
                        "model": model,
                        "timeout_seconds": timeout_seconds,
                        "mode": "conversation" if audio_spec else "speech",
                    },
                },
            )
            event = await wait_voice(state, timeout_seconds)
            state.completed_mono = state.completed_mono or time.perf_counter()
            voice = event.get("voice") if isinstance(event.get("voice"), dict) else {}
            transcript = str(voice.get("transcript") or "")
            audio_b64 = str(voice.get("b64_json") or "")
            mime_type = str(voice.get("mime_type") or "audio/webm;codecs=opus")
            if not audio_b64:
                raise RuntimeError("GPT-Live completed without captured audio")
            usage, token_meta = await record_voice(
                request_id=request_id,
                response_id=response_id,
                client_id=selected_client,
                principal=principal,
                model=model,
                request_type=request_type,
                prompt=prompt,
                transcript=transcript,
                state=state,
                status_value="completed",
                audio_bytes=int(voice.get("size") or 0),
            )
            return {
                "id": response_id,
                "object": "audio.speech" if request_type == "voice_generation" else "audio.conversation",
                "created": int(time.time()),
                "model": model,
                "audio": {"b64_json": audio_b64, "mime_type": mime_type},
                "transcript": transcript,
                "usage": usage,
                "chat2api": {
                    "request_id": request_id,
                    "client_id": selected_client,
                    "timings": state.timings(),
                    "diagnostics": state.diagnostics,
                    "token_usage": token_meta,
                },
            }
        except asyncio.TimeoutError as error:
            state.completed_mono = time.perf_counter()
            await record_voice(
                request_id=request_id, response_id=response_id, client_id=selected_client,
                principal=principal, model=model, request_type=request_type, prompt=prompt,
                transcript="", state=state, status_value="error", error="Timed out waiting for GPT-Live",
            )
            raise HTTPException(status_code=504, detail="Timed out waiting for GPT-Live") from error
        except RuntimeError as error:
            state.completed_mono = state.completed_mono or time.perf_counter()
            await record_voice(
                request_id=request_id, response_id=response_id, client_id=selected_client,
                principal=principal, model=model, request_type=request_type, prompt=prompt,
                transcript="", state=state, status_value="error", error=str(error),
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
        finally:
            registry.busy_clients.discard(selected_client)
            await broker.release(request_id)

    @app.post("/v1/audio/speech")
    async def audio_speech(
        body: SpeechRequest,
        x_chat2api_client: str | None = Header(default=None),
        principal: ApiPrincipal = Depends(require_audio_key),
    ) -> dict[str, object]:
        return await execute_voice(
            principal=principal,
            model=body.model,
            prompt=body.input,
            client_id=body.client_id or x_chat2api_client,
            timeout_seconds=body.timeout or max(settings.request_timeout_seconds, 180),
            audio_spec=None,
            request_type="voice_generation",
        )

    @app.post("/v1/audio/conversations")
    async def audio_conversation(
        body: VoiceConversationRequest,
        x_chat2api_client: str | None = Header(default=None),
        principal: ApiPrincipal = Depends(require_audio_key),
    ) -> dict[str, object]:
        item = file_store.get(body.audio_file_id)
        if not item:
            raise HTTPException(status_code=400, detail="Unknown audio_file_id")
        if principal.kind != "master" and item.owner_key_id != principal.key_id:
            raise HTTPException(status_code=403, detail="Audio file belongs to another API key")
        if not str(item.mime_type or "").startswith("audio/"):
            raise HTTPException(status_code=400, detail="audio_file_id must reference an audio/* file")
        return await execute_voice(
            principal=principal,
            model=body.model,
            prompt=body.instruction or "",
            client_id=body.client_id or x_chat2api_client,
            timeout_seconds=body.timeout or max(settings.request_timeout_seconds, 180),
            audio_spec={"file_id": item.file_id, "filename": item.filename, "mime_type": item.mime_type, "size": item.size},
            request_type="voice_conversation",
        )

    @app.get("/assets/chat2api-v6.js")
    async def admin_v6_js() -> Response:
        path = Path(__file__).with_name("admin_v6.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def inject_v6_console(request: Request, call_next):
        response = await call_next(request)
        if request.url.path not in {"/admin", "/developers"}:
            return response
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        text = body.decode("utf-8", errors="replace")
        marker = '<script src="/assets/chat2api-v6.js"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
