from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .admin import admin_response
from .api_keys import ApiKeyStore, ApiPrincipal
from .broker import RequestBroker, RequestState
from .config import Settings, get_settings
from .file_store import FileStore
from .models import (
    ApiKeyCreate,
    ApiKeyUpdate,
    ChatCompletionRequest,
    ClientSummary,
    ExtensionRegistration,
    ExtensionRegistrationResult,
    FileUploadRequest,
    ImageGenerationRequest,
    TestRunCreate,
)
from .prompting import build_prompt
from .registry import ClientRegistry
from .telemetry import TelemetryStore
from .test_runs import TestRunStore
from .token_usage import usage_for

logger = logging.getLogger("chat2api")
APP_VERSION = "0.5.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    registry = ClientRegistry(config.data_dir)
    broker = RequestBroker()
    telemetry = TelemetryStore(config.data_dir)
    api_keys = ApiKeyStore(config.data_dir, config.api_key)
    file_store = FileStore(config.data_dir)
    test_runs = TestRunStore(config.data_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await registry.load()
        await telemetry.load()
        await api_keys.load()
        await file_store.load()
        await test_runs.load()
        recovered_runs = await test_runs.recover_interrupted()
        if recovered_runs:
            logger.warning("Recovered %s interrupted playground test run(s) as stalled", len(recovered_runs))
        if config.api_key in {"", "change-me"}:
            logger.warning("CHAT2API_API_KEY is using an unsafe default. Change it before remote exposure.")
        if config.pairing_code in {"", "change-me-pairing"}:
            logger.warning("CHAT2API_PAIRING_CODE is using an unsafe default. Change it before remote exposure.")
        yield

    app = FastAPI(
        title="chat2api",
        version=APP_VERSION,
        description="OpenAI-compatible browser bridge for text, files, vision and ChatGPT Images.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.registry = registry
    app.state.broker = broker
    app.state.telemetry = telemetry
    app.state.api_keys = api_keys
    app.state.file_store = file_store
    app.state.test_runs = test_runs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.origins,
        allow_credentials=config.origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def supplied_token(authorization: str | None, x_api_key: str | None) -> str:
        supplied = (x_api_key or "").strip()
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        return supplied

    def master_principal() -> ApiPrincipal:
        return ApiPrincipal(
            key_id="master",
            name="CHAT2API_API_KEY",
            kind="master",
            scopes=("admin", "chat", "models", "files", "images"),
        )

    async def require_api_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> ApiPrincipal:
        supplied = supplied_token(authorization, x_api_key)
        if not supplied:
            raise HTTPException(status_code=401, detail="Missing API key")
        if config.api_key and secrets.compare_digest(supplied, config.api_key):
            return master_principal()
        principal = await api_keys.authenticate(supplied)
        if not principal:
            raise HTTPException(status_code=401, detail="Invalid or disabled API key")
        return principal

    async def require_admin_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> ApiPrincipal:
        supplied = supplied_token(authorization, x_api_key)
        if not supplied:
            raise HTTPException(status_code=401, detail="Missing administrator API key")
        if config.api_key and secrets.compare_digest(supplied, config.api_key):
            return master_principal()
        if await api_keys.authenticate(supplied):
            raise HTTPException(status_code=403, detail="Managed API keys cannot access administrator endpoints")
        raise HTTPException(status_code=401, detail="Invalid administrator API key")

    def require_scope(principal: ApiPrincipal, scope: str) -> None:
        if principal.kind != "master" and scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"API key does not have {scope!r} permission")

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "name": "chat2api",
            "status": "ok",
            "docs": "/docs",
            "developers": "/developers",
            "admin": "/admin",
            "version": APP_VERSION,
        }

    @app.get("/healthz")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "online_extensions": len(registry.online_client_ids()),
            "version": APP_VERSION,
        }

    @app.get("/admin")
    async def admin():
        return admin_response("overview")

    @app.get("/developers")
    async def developers():
        return admin_response("docs")

    def managed_keys_with_stats() -> list[dict[str, Any]]:
        stats = telemetry.key_stats()
        rows: list[dict[str, Any]] = [
            {
                "key_id": "master",
                "name": "CHAT2API_API_KEY",
                "kind": "master",
                "managed": False,
                "prefix": "server .env",
                "enabled": True,
                "configured_enabled": True,
                "expired": False,
                "revoked_at": None,
                "created_at": None,
                "expires_at": None,
                "last_used_at": None,
                "secret_recoverable": False,
                "scopes": ["admin", "chat", "models", "files", "images"],
                **stats.get("master", {}),
            }
        ]
        for item in api_keys.list_public():
            rows.append({**item, "kind": "managed", "managed": True, **stats.get(item["key_id"], {})})
        return rows

    @app.get("/api/admin/overview", dependencies=[Depends(require_admin_key)])
    async def admin_overview() -> dict[str, object]:
        return {
            "version": APP_VERSION,
            "health": {"online_extensions": len(registry.online_client_ids())},
            "clients": registry.summaries(),
            "models": registry.model_catalog(online_only=True),
            "api_keys": managed_keys_with_stats(),
            "telemetry": telemetry.summary(),
            "recent_requests": telemetry.recent(20),
            "recent_tests": test_runs.recent(10),
            "capabilities": {
                "text": True,
                "vision": True,
                "file_understanding": True,
                "image_generation": True,
                "voice_generation": False,
                "voice_conversation": False,
                "desktop_agent": False,
            },
        }

    @app.get("/api/admin/keys", dependencies=[Depends(require_admin_key)])
    async def admin_keys() -> dict[str, object]:
        return {"data": managed_keys_with_stats()}

    @app.post("/api/admin/keys", dependencies=[Depends(require_admin_key)])
    async def create_api_key(body: ApiKeyCreate) -> dict[str, object]:
        expires_at = None
        if body.expires_in_days:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)).isoformat()
        item, token = await api_keys.create(body.name, expires_at)
        return {"key": {**item, "kind": "managed", "managed": True}, "token": token}

    @app.get("/api/admin/keys/{key_id}/secret", dependencies=[Depends(require_admin_key)])
    async def reveal_api_key(key_id: str) -> dict[str, str]:
        if key_id == "master":
            raise HTTPException(status_code=400, detail="The master key is only available in the server .env")
        try:
            return {"key_id": key_id, "token": api_keys.reveal(key_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.patch("/api/admin/keys/{key_id}", dependencies=[Depends(require_admin_key)])
    async def update_api_key(key_id: str, body: ApiKeyUpdate) -> dict[str, object]:
        if key_id == "master":
            raise HTTPException(status_code=400, detail="The master key is managed through CHAT2API_API_KEY in .env")
        try:
            item = await api_keys.update(key_id, name=body.name, enabled=body.enabled)
            return {"key": {**item, "kind": "managed", "managed": True}}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/api/admin/keys/{key_id}", dependencies=[Depends(require_admin_key)])
    async def revoke_api_key(key_id: str) -> dict[str, object]:
        if key_id == "master":
            raise HTTPException(status_code=400, detail="The master key cannot be revoked from the web console")
        try:
            item = await api_keys.revoke(key_id)
            return {"key": {**item, "kind": "managed", "managed": True}}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/admin/requests", dependencies=[Depends(require_admin_key)])
    async def admin_requests(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        status_filter: str | None = Query(default=None, alias="status"),
        model: str | None = Query(default=None),
        key_id: str | None = Query(default=None),
        q: str | None = Query(default=None),
    ) -> dict[str, object]:
        result = telemetry.query(limit=limit, offset=offset, status=status_filter, model=model, key_id=key_id, q=q)
        return {**result, "summary": telemetry.summary()}

    @app.get("/api/admin/requests/{request_id}", dependencies=[Depends(require_admin_key)])
    async def admin_request_detail(request_id: str) -> dict[str, object]:
        row = telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Request record not found")
        return row

    @app.get("/api/admin/tests", dependencies=[Depends(require_admin_key)])
    async def admin_tests(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
        return {"data": test_runs.recent(limit)}

    @app.get("/api/admin/tests/{run_id}", dependencies=[Depends(require_admin_key)])
    async def admin_test_detail(run_id: str) -> dict[str, object]:
        row = test_runs.get(run_id)
        if not row:
            raise HTTPException(status_code=404, detail="Test report not found")
        return row

    @app.post("/api/admin/tests", dependencies=[Depends(require_admin_key)])
    async def save_test_report(body: TestRunCreate) -> dict[str, object]:
        row = await test_runs.upsert(body.model_dump())
        return {"report": row}

    @app.post("/api/extensions/register", response_model=ExtensionRegistrationResult)
    async def register_extension(body: ExtensionRegistration, x_pairing_code: str | None = Header(default=None)) -> ExtensionRegistrationResult:
        if not x_pairing_code or not secrets.compare_digest(x_pairing_code, config.pairing_code):
            raise HTTPException(status_code=401, detail="Invalid pairing code")
        client_id, token = await registry.register(body.name, body.browser_name, body.version, body.metadata)
        return ExtensionRegistrationResult(client_id=client_id, token=token)

    @app.get("/api/clients", response_model=list[ClientSummary], dependencies=[Depends(require_admin_key)])
    async def clients() -> list[dict[str, object]]:
        return registry.summaries()

    @app.post("/v1/files")
    async def upload_file(body: FileUploadRequest, principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        require_scope(principal, "files")
        try:
            item = await file_store.create(
                filename=body.filename,
                data_base64=body.data_base64,
                mime_type=body.mime_type,
                owner_key_id=principal.key_id,
                purpose=body.purpose,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return item.public()

    @app.get("/v1/files/{file_id}")
    async def file_metadata(file_id: str, principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        require_scope(principal, "files")
        item = file_store.get(file_id)
        if not item:
            raise HTTPException(status_code=404, detail="Unknown file_id")
        if principal.kind != "master" and item.owner_key_id != principal.key_id:
            raise HTTPException(status_code=403, detail="File belongs to another API key")
        return item.public()

    @app.delete("/v1/files/{file_id}")
    async def delete_file(file_id: str, principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, bool]:
        require_scope(principal, "files")
        try:
            await file_store.delete(file_id, None if principal.kind == "master" else principal.key_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        return {"deleted": True}

    @app.get("/api/extensions/files/{file_id}")
    async def extension_file(file_id: str, client_id: str = Query(...), token: str = Query(...)) -> Response:
        if not await registry.authenticate(client_id, token):
            raise HTTPException(status_code=401, detail="Invalid extension token")
        try:
            item, payload = file_store.read(file_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=410, detail=str(error)) from error
        return Response(
            payload,
            media_type=item.mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{item.filename.replace(chr(34), "")}"',
                "X-Chat2API-Filename": item.filename,
                "Cache-Control": "no-store",
            },
        )

    @app.get("/v1/models")
    async def models(principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        require_scope(principal, "models")
        return {"object": "list", "data": registry.model_catalog(online_only=True)}

    def resolve_client_now(requested: str | None) -> str:
        try:
            return registry.resolve_client(requested)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ConnectionError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def completion_id() -> str:
        return "chatcmpl-" + uuid.uuid4().hex

    def accepted_request_id(supplied: str | None, prefix: str) -> str:
        value = str(supplied or "").strip()
        if not value:
            return prefix + uuid.uuid4().hex
        if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]{8,120}", value):
            raise HTTPException(status_code=400, detail=f"Invalid {prefix} request ID")
        if value in broker.requests or telemetry.get(value):
            raise HTTPException(status_code=409, detail=f"Duplicate request_id: {value}")
        return value

    def standard_usage(prompt: str, completion: str) -> tuple[dict[str, int], dict[str, object]]:
        usage = usage_for(prompt, completion)
        return (
            {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens},
            {"estimated": usage.estimated, "estimator": usage.estimator},
        )

    def attachment_specs(file_ids: list[str], principal: ApiPrincipal) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for file_id in file_ids:
            item = file_store.get(file_id)
            if not item:
                raise HTTPException(status_code=400, detail=f"Unknown attachment file_id: {file_id}")
            if principal.kind != "master" and item.owner_key_id != principal.key_id:
                raise HTTPException(status_code=403, detail=f"Attachment {file_id} belongs to another API key")
            result.append({"file_id": item.file_id, "filename": item.filename, "mime_type": item.mime_type, "size": item.size})
        return result

    async def inline_image_files(body: ChatCompletionRequest, principal: ApiPrincipal) -> list[str]:
        result: list[str] = []
        sequence = 0
        for message in body.messages:
            if not isinstance(message.content, list):
                continue
            for part in message.content:
                if not isinstance(part, dict) or part.get("type") not in {"image_url", "input_image"}:
                    continue
                value = part.get("image_url")
                if isinstance(value, dict):
                    value = value.get("url")
                if not isinstance(value, str) or not value:
                    continue
                if value.startswith("file_"):
                    result.append(value)
                    continue
                if not value.startswith("data:"):
                    raise HTTPException(
                        status_code=400,
                        detail="Remote image URLs are not fetched for security. Use a base64 data URL or upload via POST /v1/files.",
                    )
                sequence += 1
                try:
                    item = await file_store.create(
                        filename=f"inline-image-{sequence}.png",
                        data_base64=value,
                        mime_type=None,
                        owner_key_id=principal.key_id,
                        purpose="vision",
                    )
                except ValueError as error:
                    raise HTTPException(status_code=400, detail=str(error)) from error
                result.append(item.file_id)
        return result

    def chat2api_meta(state: RequestState, token_meta: dict[str, object], principal: ApiPrincipal) -> dict[str, object]:
        return {
            "client_id": state.client_id,
            "request_id": state.request_id,
            "api_key": {"key_id": principal.key_id, "name": principal.name, "kind": principal.kind},
            "diagnostics": dict(state.diagnostics),
            "timings": state.timings(),
            "token_usage": token_meta,
        }

    async def record_request(
        state: RequestState,
        requested_model: str,
        prompt: str,
        status_value: str,
        principal: ApiPrincipal,
        *,
        stream: bool,
        prompt_mode: str,
        response_id: str,
        final_text: str = "",
        error: str | None = None,
        request_type: str = "text",
        attachments_count: int = 0,
    ) -> tuple[dict[str, int], dict[str, object]]:
        usage, token_meta = standard_usage(prompt, final_text)
        await telemetry.upsert(
            {
                "request_id": state.request_id,
                "response_id": response_id,
                "client_id": state.client_id,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
                "request_type": request_type,
                "attachments_count": attachments_count,
                "stream": stream,
                "prompt_mode": prompt_mode,
                "prompt_chars": len(prompt),
                "completion_chars": len(final_text),
                "status": status_value,
                "usage": {**usage, **token_meta},
                "timings": state.timings(),
                "diagnostics": dict(state.diagnostics),
                "error": error,
            }
        )
        return usage, token_meta

    async def record_request_running(
        state: RequestState,
        requested_model: str,
        prompt: str,
        principal: ApiPrincipal,
        *,
        stream: bool,
        prompt_mode: str,
        response_id: str,
        request_type: str,
        attachments_count: int,
    ) -> None:
        usage, token_meta = standard_usage(prompt, "")
        await telemetry.upsert(
            {
                "request_id": state.request_id,
                "response_id": response_id,
                "client_id": state.client_id,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
                "request_type": request_type,
                "attachments_count": attachments_count,
                "stream": stream,
                "prompt_mode": prompt_mode,
                "prompt_chars": len(prompt),
                "completion_chars": 0,
                "status": "running",
                "usage": {**usage, **token_meta},
                "timings": state.timings(),
                "diagnostics": dict(state.diagnostics),
                "error": None,
            }
        )

    def failure_status(state: RequestState, event_type: str = "", message: str = "") -> str:
        diagnostics = state.diagnostics or {}
        if event_type == "chat.cancelled" or diagnostics.get("playground_cancelled") is True:
            return "cancelled"
        if any(
            diagnostics.get(name) is True
            for name in (
                "extension_dispatch_watchdog_fired",
                "extension_submit_watchdog_fired",
                "post_submit_generation_watchdog_fired",
                "generation_activity_watchdog_fired",
                "absolute_request_lease_watchdog_fired",
            )
        ):
            return "stalled"
        if "cancel" in str(message or "").lower():
            return "cancelled"
        return "error"

    async def record_early_error(
        *, request_id: str, response_id: str, requested_model: str, prompt: str,
        principal: ApiPrincipal, stream: bool, prompt_mode: str, started_mono: float,
        error: str, request_type: str = "text", attachments_count: int = 0,
    ) -> None:
        usage, token_meta = standard_usage(prompt, "")
        await telemetry.upsert(
            {
                "request_id": request_id,
                "response_id": response_id,
                "client_id": None,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
                "request_type": request_type,
                "attachments_count": attachments_count,
                "stream": stream,
                "prompt_mode": prompt_mode,
                "prompt_chars": len(prompt),
                "completion_chars": 0,
                "status": "error",
                "usage": {**usage, **token_meta},
                "timings": {"total_ms": round((time.perf_counter() - started_mono) * 1000, 1)},
                "diagnostics": {},
                "error": error,
            }
        )

    def chunk_payload(
        response_id: str, model: str, delta: dict[str, object], finish_reason: str | None = None,
        usage: dict[str, int] | None = None, meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if usage is not None:
            payload["usage"] = usage
        if meta is not None:
            payload["chat2api"] = meta
        return payload

    async def stream_request(
        request: Request, state: RequestState, response_id: str, model: str, timeout_seconds: int,
        prompt: str, principal: ApiPrincipal, prompt_mode: str, attachments_count: int,
    ) -> AsyncIterator[str]:
        yield "data: " + json.dumps(chunk_payload(response_id, model, {"role": "assistant"}), ensure_ascii=False) + "\n\n"
        sent_text = ""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        recorded = False
        request_type = "multimodal" if attachments_count else "text"
        try:
            while True:
                if await request.is_disconnected():
                    await registry.send(state.client_id, {"type": "chat.cancel", "request_id": state.request_id})
                    state.completed_mono = state.completed_mono or time.perf_counter()
                    await record_request(state, model, prompt, "error", principal, stream=True, prompt_mode=prompt_mode,
                                         response_id=response_id, final_text=sent_text, error="API client disconnected",
                                         request_type=request_type, attachments_count=attachments_count)
                    recorded = True
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await registry.send(state.client_id, {"type": "chat.cancel", "request_id": state.request_id})
                    state.completed_mono = state.completed_mono or time.perf_counter()
                    await record_request(state, model, prompt, "error", principal, stream=True, prompt_mode=prompt_mode,
                                         response_id=response_id, final_text=sent_text, error="Timed out waiting for ChatGPT",
                                         request_type=request_type, attachments_count=attachments_count)
                    recorded = True
                    yield "data: " + json.dumps({"error": {"message": "Timed out waiting for ChatGPT", "type": "timeout_error"}}, ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
                try:
                    event = await asyncio.wait_for(state.queue.get(), timeout=min(1.0, remaining))
                except asyncio.TimeoutError:
                    continue
                event_type = event.get("type")
                if event_type in {"chat.diagnostics", "chat.started"}:
                    continue
                if event_type == "chat.delta":
                    delta = str(event.get("delta") or "")
                    if delta:
                        sent_text += delta
                        yield "data: " + json.dumps(chunk_payload(response_id, model, {"content": delta}), ensure_ascii=False) + "\n\n"
                elif event_type == "chat.snapshot":
                    snapshot = str(event.get("text") or "")
                    if snapshot.startswith(sent_text):
                        delta = snapshot[len(sent_text):]
                        if delta:
                            sent_text = snapshot
                            yield "data: " + json.dumps(chunk_payload(response_id, model, {"content": delta}), ensure_ascii=False) + "\n\n"
                elif event_type == "chat.completed":
                    final_text = str(event.get("text") or state.text)
                    if final_text.startswith(sent_text):
                        delta = final_text[len(sent_text):]
                        if delta:
                            sent_text = final_text
                            yield "data: " + json.dumps(chunk_payload(response_id, model, {"content": delta}), ensure_ascii=False) + "\n\n"
                    usage, token_meta = await record_request(
                        state, model, prompt, "completed", principal, stream=True, prompt_mode=prompt_mode,
                        response_id=response_id, final_text=final_text, request_type=request_type,
                        attachments_count=attachments_count,
                    )
                    recorded = True
                    yield "data: " + json.dumps(
                        chunk_payload(response_id, model, {}, "stop", usage, chat2api_meta(state, token_meta, principal)),
                        ensure_ascii=False,
                    ) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
                elif event_type in {"chat.error", "chat.cancelled"}:
                    message = str(event.get("error") or event.get("reason") or "Browser request failed")
                    await record_request(state, model, prompt, failure_status(state, str(event_type), message), principal, stream=True, prompt_mode=prompt_mode,
                                         response_id=response_id, final_text=sent_text, error=message,
                                         request_type=request_type, attachments_count=attachments_count)
                    recorded = True
                    yield "data: " + json.dumps(
                        {"error": {"message": message, "type": "browser_error"},
                         "chat2api": {"request_id": state.request_id, "api_key": principal.as_dict(),
                                      "diagnostics": state.diagnostics, "timings": state.timings()}},
                        ensure_ascii=False,
                    ) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
        finally:
            if not recorded and state.completed_mono:
                message = "Streaming request ended before completion"
                await record_request(state, model, prompt, failure_status(state, message=message), principal, stream=True, prompt_mode=prompt_mode,
                                     response_id=response_id, final_text=sent_text,
                                     error=message, request_type=request_type,
                                     attachments_count=attachments_count)
            registry.busy_clients.discard(state.client_id)
            await broker.release(state.request_id)

    @app.post("/v1/chat/completions")
    async def chat_completions(
        body: ChatCompletionRequest,
        request: Request,
        x_chat2api_client: str | None = Header(default=None),
        x_chat2api_request_id: str | None = Header(default=None),
        principal: ApiPrincipal = Depends(require_api_key),
    ):
        require_scope(principal, "chat")
        prompt = build_prompt(body.messages, body.prompt_mode)
        inline_ids = await inline_image_files(body, principal)
        file_ids = list(dict.fromkeys(body.all_file_ids() + inline_ids))
        attachments = attachment_specs(file_ids, principal)
        request_id = accepted_request_id(x_chat2api_request_id, "req_")
        response_id = completion_id()
        request_started = time.perf_counter()
        requested_client = body.client_id or x_chat2api_client
        request_type = "multimodal" if attachments else "text"

        try:
            client_id = resolve_client_now(requested_client)
        except HTTPException as error:
            await record_early_error(request_id=request_id, response_id=response_id, requested_model=body.model,
                                     prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                                     started_mono=request_started, error=str(error.detail), request_type=request_type,
                                     attachments_count=len(attachments))
            raise

        try:
            state = await broker.create(request_id, client_id)
        except RuntimeError as error:
            await record_early_error(request_id=request_id, response_id=response_id, requested_model=body.model,
                                     prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                                     started_mono=request_started, error=str(error), request_type=request_type,
                                     attachments_count=len(attachments))
            raise HTTPException(status_code=409, detail=str(error)) from error

        registry.busy_clients.add(client_id)
        timeout_seconds = body.timeout or config.request_timeout_seconds
        try:
            await record_request_running(
                state,
                body.model,
                prompt,
                principal,
                stream=body.stream,
                prompt_mode=body.prompt_mode,
                response_id=response_id,
                request_type=request_type,
                attachments_count=len(attachments),
            )
            await registry.send(
                client_id,
                {
                    "type": "chat.request",
                    "request_id": request_id,
                    "prompt": prompt,
                    "attachments": attachments,
                    "options": {"auto_switch_text": True, "timeout_seconds": timeout_seconds, "model": body.model},
                },
            )
        except Exception as error:
            state.completed_mono = time.perf_counter()
            await record_request(state, body.model, prompt, "error", principal, stream=body.stream,
                                 prompt_mode=body.prompt_mode, response_id=response_id, error=str(error),
                                 request_type=request_type, attachments_count=len(attachments))
            registry.busy_clients.discard(client_id)
            await broker.release(request_id)
            raise HTTPException(status_code=503, detail=str(error)) from error

        if body.stream:
            return StreamingResponse(
                stream_request(request, state, response_id, body.model, timeout_seconds, prompt, principal,
                               body.prompt_mode, len(attachments)),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        try:
            text = await asyncio.wait_for(state.final_future, timeout=timeout_seconds)
            usage, token_meta = await record_request(
                state, body.model, prompt, "completed", principal, stream=False, prompt_mode=body.prompt_mode,
                response_id=response_id, final_text=text, request_type=request_type,
                attachments_count=len(attachments),
            )
            meta = chat2api_meta(state, token_meta, principal)
        except asyncio.TimeoutError as error:
            await registry.send(client_id, {"type": "chat.cancel", "request_id": request_id})
            state.completed_mono = time.perf_counter()
            await record_request(state, body.model, prompt, "error", principal, stream=False,
                                 prompt_mode=body.prompt_mode, response_id=response_id, final_text=state.text,
                                 error="Timed out waiting for ChatGPT", request_type=request_type,
                                 attachments_count=len(attachments))
            raise HTTPException(status_code=504, detail="Timed out waiting for ChatGPT") from error
        except RuntimeError as error:
            message = str(error)
            await record_request(state, body.model, prompt, failure_status(state, message=message), principal, stream=False,
                                 prompt_mode=body.prompt_mode, response_id=response_id, final_text=state.text,
                                 error=message, request_type=request_type, attachments_count=len(attachments))
            raise HTTPException(status_code=502, detail=str(error)) from error
        finally:
            registry.busy_clients.discard(client_id)
            await broker.release(request_id)

        return JSONResponse(
            {"id": response_id, "object": "chat.completion", "created": int(time.time()), "model": body.model,
             "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
             "usage": usage, "chat2api": meta}
        )

    @app.post("/v1/images/generations")
    async def image_generations(
        body: ImageGenerationRequest,
        x_chat2api_client: str | None = Header(default=None),
        x_chat2api_request_id: str | None = Header(default=None),
        principal: ApiPrincipal = Depends(require_api_key),
    ) -> dict[str, object]:
        require_scope(principal, "images")
        attachments = attachment_specs([item.file_id for item in body.attachments], principal)
        request_id = accepted_request_id(x_chat2api_request_id, "imgreq_")
        response_id = "img_" + uuid.uuid4().hex
        started = time.perf_counter()
        client_id = resolve_client_now(body.client_id or x_chat2api_client)
        try:
            state = await broker.create(request_id, client_id)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        registry.busy_clients.add(client_id)
        timeout_seconds = body.timeout or max(config.request_timeout_seconds, 300)
        try:
            await telemetry.upsert(
                {
                    "request_id": request_id, "response_id": response_id, "client_id": client_id,
                    "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                    "requested_model": "gpt-image", "request_type": "image_generation",
                    "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                    "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "running",
                    "usage": {}, "timings": state.timings(), "diagnostics": dict(state.diagnostics), "error": None,
                }
            )
            await registry.send(
                client_id,
                {
                    "type": "image.request",
                    "request_id": request_id,
                    "prompt": body.prompt,
                    "attachments": attachments,
                    "options": {"model": "gpt-image", "size": body.size, "timeout_seconds": timeout_seconds},
                },
            )
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            completed: dict[str, Any] | None = None
            while asyncio.get_running_loop().time() < deadline:
                remaining = deadline - asyncio.get_running_loop().time()
                try:
                    event = await asyncio.wait_for(state.queue.get(), timeout=min(1.0, remaining))
                except asyncio.TimeoutError:
                    continue
                kind = event.get("type")
                if kind == "image.completed":
                    completed = event
                    break
                if kind in {"image.error", "image.cancelled"}:
                    raise RuntimeError(str(event.get("error") or event.get("reason") or "Image generation failed"))
            if not completed:
                raise asyncio.TimeoutError
            images = completed.get("images") if isinstance(completed.get("images"), list) else []
            if not images:
                raise RuntimeError("ChatGPT Images finished without a captured image")
            output: list[dict[str, object]] = []
            for image in images[:1]:
                if body.response_format == "b64_json":
                    if image.get("b64_json"):
                        output.append({"b64_json": image["b64_json"]})
                    elif image.get("url"):
                        output.append({"url": image["url"]})
                else:
                    if image.get("url"):
                        output.append({"url": image["url"]})
                    elif image.get("b64_json"):
                        mime = image.get("mime_type") or "image/png"
                        output.append({"url": f"data:{mime};base64,{image['b64_json']}"})
            state.completed_mono = state.completed_mono or time.perf_counter()
            usage, token_meta = standard_usage(body.prompt, "")
            await telemetry.upsert(
                {
                    "request_id": request_id, "response_id": response_id, "client_id": client_id,
                    "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                    "requested_model": "gpt-image", "request_type": "image_generation",
                    "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                    "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "completed",
                    "usage": {**usage, **token_meta}, "timings": state.timings(),
                    "diagnostics": dict(state.diagnostics), "error": None,
                }
            )
            return {
                "created": int(time.time()),
                "model": "gpt-image",
                "data": output,
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
            await telemetry.upsert(
                {"request_id": request_id, "response_id": response_id, "client_id": client_id,
                 "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                 "requested_model": "gpt-image", "request_type": "image_generation",
                 "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                 "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "error",
                 "usage": {}, "timings": state.timings(), "diagnostics": state.diagnostics,
                 "error": "Timed out waiting for ChatGPT Images"}
            )
            raise HTTPException(status_code=504, detail="Timed out waiting for ChatGPT Images") from error
        except RuntimeError as error:
            state.completed_mono = state.completed_mono or time.perf_counter()
            await telemetry.upsert(
                {"request_id": request_id, "response_id": response_id, "client_id": client_id,
                 "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                 "requested_model": "gpt-image", "request_type": "image_generation",
                 "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                 "prompt_chars": len(body.prompt), "completion_chars": 0,
                 "status": failure_status(state, message=str(error)),
                 "usage": {}, "timings": state.timings(), "diagnostics": state.diagnostics, "error": str(error)}
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
        finally:
            registry.busy_clients.discard(client_id)
            await broker.release(request_id)

    @app.websocket("/ws/extensions/{client_id}")
    async def extension_socket(websocket: WebSocket, client_id: str, token: str = Query(default="")) -> None:
        if not await registry.authenticate(client_id, token):
            await websocket.close(code=4401, reason="Invalid extension token")
            return
        await websocket.accept()
        await registry.attach(client_id, websocket)
        try:
            await websocket.send_json({"type": "server.hello", "client_id": client_id, "version": APP_VERSION})
            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                await registry.touch(client_id, message.get("metadata") if isinstance(message.get("metadata"), dict) else None)
                if message_type in {"heartbeat", "extension.hello", "extension.status"}:
                    if message_type == "heartbeat":
                        await websocket.send_json({"type": "heartbeat.ack", "ts": time.time()})
                    continue
                request_id = str(message.get("request_id") or "")
                if request_id and message_type in {
                    "chat.diagnostics", "chat.started", "chat.delta", "chat.snapshot", "chat.completed",
                    "chat.error", "chat.cancelled", "image.diagnostics", "image.started", "image.progress",
                    "image.completed", "image.error", "image.cancelled",
                }:
                    await broker.publish(request_id, message)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Extension WebSocket failed: %s", client_id)
        finally:
            active_request = broker.client_requests.get(client_id)
            if active_request:
                await broker.publish(active_request, {"type": "chat.error", "request_id": active_request,
                                                      "error": "Chrome extension disconnected"})
            await registry.detach(client_id, websocket)

    return app


app = create_app()
