from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .api_keys import ApiPrincipal
from .test_assets_v7 import dictation_sample_mp3
from .token_usage import usage_for

PATCH_VERSION = "0.7.0"


class DictationRequest(BaseModel):
    model: str = "gpt-dictation"
    audio_file_id: str = Field(min_length=1, max_length=120)
    language: str | None = Field(default=None, max_length=40)
    client_id: str | None = None
    timeout: int | None = Field(default=None, ge=15, le=300)


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


def _usage(prompt: str, completion: str) -> tuple[dict[str, int], dict[str, object]]:
    usage = usage_for(prompt, completion)
    return (
        {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
        {"estimated": usage.estimated, "estimator": usage.estimator},
    )


def install_v7_patch(app: FastAPI) -> FastAPI:
    settings = app.state.settings
    registry = app.state.registry
    broker = app.state.broker
    telemetry = app.state.telemetry
    api_keys = app.state.api_keys
    file_store = app.state.file_store
    app.version = PATCH_VERSION

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

    # Keep the older IDs compatible, but make it explicit that only gpt-live is the
    # recommended Voice route. ChatGPT itself chooses the actual Live tier for the account.
    base_model_catalog = registry.model_catalog

    def model_catalog_v7(online_only: bool = True) -> list[dict[str, Any]]:
        rows = list(base_model_catalog(online_only=online_only))
        ids = {str(row.get("id") or ""): row for row in rows}
        if "gpt-live" in ids:
            ids["gpt-live"]["label"] = "ChatGPT Voice / Live route (recommended)"
        if "gpt-live-mini" in ids:
            ids["gpt-live-mini"]["label"] = "Compatibility alias; ChatGPT chooses the actual Live tier"
        if "gpt-dictation" not in ids:
            clients = registry.online_client_ids() if online_only else sorted(registry.clients)
            rows.append(
                {
                    "id": "gpt-dictation",
                    "object": "model",
                    "created": 0,
                    "owned_by": "chat2api",
                    "label": "ChatGPT Dictation browser route",
                    "capabilities": ["audio-transcription", "dictation"],
                    "clients": clients,
                }
            )
        order = {
            "default": 0,
            "chatgpt-web": 1,
            "gpt-image": 2,
            "gpt-live": 3,
            "gpt-dictation": 4,
            "gpt-live-mini": 5,
        }
        return sorted(rows, key=lambda item: (order.get(str(item.get("id") or ""), 10), str(item.get("id") or "")))

    registry.model_catalog = model_catalog_v7

    async def wait_dictation(state, timeout_seconds: int) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                event = await asyncio.wait_for(state.queue.get(), timeout=min(1.0, remaining))
            except asyncio.TimeoutError:
                continue
            event_type = str(event.get("type") or "")
            if event_type == "image.completed" and event.get("kind") == "dictation":
                return event
            if event_type in {"image.error", "image.cancelled"} and event.get("kind") == "dictation":
                raise RuntimeError(str(event.get("error") or event.get("reason") or "Dictation request failed"))
        raise asyncio.TimeoutError

    async def record_dictation(
        *,
        request_id: str,
        response_id: str,
        client_id: str,
        principal: ApiPrincipal,
        state,
        text: str,
        status_value: str,
        error: str | None = None,
    ) -> tuple[dict[str, int], dict[str, object]]:
        usage, token_meta = _usage("[audio dictation]", text)
        await telemetry.append(
            {
                "request_id": request_id,
                "response_id": response_id,
                "client_id": client_id,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": "gpt-dictation",
                "request_type": "dictation",
                "attachments_count": 1,
                "stream": False,
                "prompt_mode": "dictation",
                "prompt_chars": 0,
                "completion_chars": len(text),
                "status": status_value,
                "usage": {**usage, **token_meta},
                "timings": state.timings(),
                "diagnostics": dict(state.diagnostics),
                "error": error,
            }
        )
        return usage, token_meta

    @app.post("/v1/audio/transcriptions")
    async def audio_transcriptions(
        body: DictationRequest,
        x_chat2api_client: str | None = Header(default=None),
        principal: ApiPrincipal = Depends(require_audio_key),
    ) -> dict[str, object]:
        if body.model not in {"gpt-dictation", "chatgpt-dictation"}:
            raise HTTPException(status_code=400, detail="Dictation model must be gpt-dictation")
        item = file_store.get(body.audio_file_id)
        if not item:
            raise HTTPException(status_code=400, detail="Unknown audio_file_id")
        if principal.kind != "master" and item.owner_key_id != principal.key_id:
            raise HTTPException(status_code=403, detail="Audio file belongs to another API key")
        if not str(item.mime_type or "").startswith("audio/"):
            raise HTTPException(status_code=400, detail="audio_file_id must reference an audio/* file")

        client_id = resolve_client(body.client_id or x_chat2api_client)
        request_id = "dictreq_" + uuid.uuid4().hex
        response_id = "dict_" + uuid.uuid4().hex
        timeout_seconds = body.timeout or 90
        try:
            state = await broker.create(request_id, client_id)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        registry.busy_clients.add(client_id)
        try:
            await registry.send(
                client_id,
                {
                    "type": "dictation.request",
                    "request_id": request_id,
                    "audio": {
                        "file_id": item.file_id,
                        "filename": item.filename,
                        "mime_type": item.mime_type,
                        "size": item.size,
                    },
                    "options": {
                        "model": "gpt-dictation",
                        "language": body.language,
                        "timeout_seconds": timeout_seconds,
                    },
                },
            )
            event = await wait_dictation(state, timeout_seconds)
            state.completed_mono = state.completed_mono or time.perf_counter()
            text = str(event.get("text") or "").strip()
            if not text:
                raise RuntimeError("ChatGPT Dictation completed without transcription text")
            usage, token_meta = await record_dictation(
                request_id=request_id,
                response_id=response_id,
                client_id=client_id,
                principal=principal,
                state=state,
                text=text,
                status_value="completed",
            )
            return {
                "id": response_id,
                "object": "audio.transcription",
                "created": int(time.time()),
                "model": "gpt-dictation",
                "text": text,
                "usage": usage,
                "chat2api": {
                    "request_id": request_id,
                    "client_id": client_id,
                    "timings": state.timings(),
                    "diagnostics": state.diagnostics,
                    "token_usage": token_meta,
                },
            }
        except asyncio.TimeoutError as error:
            state.completed_mono = time.perf_counter()
            try:
                await registry.send(client_id, {"type": "dictation.cancel", "request_id": request_id})
            except Exception:
                pass
            await record_dictation(
                request_id=request_id,
                response_id=response_id,
                client_id=client_id,
                principal=principal,
                state=state,
                text="",
                status_value="error",
                error="Timed out waiting for ChatGPT Dictation",
            )
            raise HTTPException(status_code=504, detail="Timed out waiting for ChatGPT Dictation") from error
        except RuntimeError as error:
            state.completed_mono = state.completed_mono or time.perf_counter()
            await record_dictation(
                request_id=request_id,
                response_id=response_id,
                client_id=client_id,
                principal=principal,
                state=state,
                text="",
                status_value="error",
                error=str(error),
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
        finally:
            registry.busy_clients.discard(client_id)
            await broker.release(request_id)

    @app.get("/assets/chat2api-test-dictation.mp3")
    async def dictation_test_asset() -> Response:
        return Response(
            dictation_sample_mp3(),
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "public, max-age=3600",
                "Content-Disposition": 'inline; filename="chat2api-dictation-test.mp3"',
            },
        )

    @app.get("/assets/chat2api-v7.js")
    async def admin_v7_js() -> Response:
        path = Path(__file__).with_name("admin_v7.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    async def response_bytes(response) -> bytes:
        body = getattr(response, "body", None)
        if body is not None:
            return bytes(body)
        chunks: list[bytes] = []
        iterator = getattr(response, "body_iterator", None)
        if iterator is not None:
            async for chunk in iterator:
                chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
        return b"".join(chunks)

    @app.middleware("http")
    async def v7_console_and_version(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path in {"/", "/healthz", "/api/admin/overview"} and "application/json" in response.headers.get("content-type", ""):
            raw = await response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities.update(
                            {
                                "voice_generation": True,
                                "voice_conversation": True,
                                "dictation": True,
                                "audio_transcription": True,
                            }
                        )
            return JSONResponse(payload, status_code=response.status_code)

        if path in {"/admin", "/developers"} and "text/html" in response.headers.get("content-type", ""):
            raw = await response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v7.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            return Response(text, status_code=response.status_code, media_type="text/html", headers={"Cache-Control": "no-store"})
        return response

    return app
