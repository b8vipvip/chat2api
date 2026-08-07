from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .admin import admin_response
from .api_keys import ApiKeyStore, ApiPrincipal
from .broker import RequestBroker, RequestState
from .config import Settings, get_settings
from .desktop import DesktopAgentHub
from .models import (
    ApiKeyCreate,
    ApiKeyUpdate,
    ChatCompletionRequest,
    ClientSummary,
    DesktopAgentRegistration,
    DesktopAgentRegistrationResult,
    ExtensionRegistration,
    ExtensionRegistrationResult,
)
from .prompting import build_prompt
from .registry import ClientRegistry
from .telemetry import TelemetryStore
from .token_usage import usage_for

logger = logging.getLogger("chat2api")
APP_VERSION = "0.4.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    registry = ClientRegistry(config.data_dir)
    broker = RequestBroker()
    desktop_agents = DesktopAgentHub()
    telemetry = TelemetryStore(config.data_dir)
    api_keys = ApiKeyStore(config.data_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await registry.load()
        await telemetry.load()
        await api_keys.load()
        if config.api_key in {"", "change-me"}:
            logger.warning("CHAT2API_API_KEY is using an unsafe default. Change it before remote exposure.")
        if config.pairing_code in {"", "change-me-pairing"}:
            logger.warning("CHAT2API_PAIRING_CODE is using an unsafe default. Change it before remote exposure.")
        yield

    app = FastAPI(
        title="chat2api",
        version=APP_VERSION,
        description="OpenAI-compatible bridge from a remote API to a logged-in ChatGPT browser tab.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.registry = registry
    app.state.broker = broker
    app.state.desktop_agents = desktop_agents
    app.state.telemetry = telemetry
    app.state.api_keys = api_keys
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
            scopes=("admin", "chat", "models", "desktop"),
        )

    async def require_api_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> ApiPrincipal:
        supplied = supplied_token(authorization, x_api_key)
        if not supplied:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
        if config.api_key and secrets.compare_digest(supplied, config.api_key):
            return master_principal()
        principal = await api_keys.authenticate(supplied)
        if not principal:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or disabled API key")
        return principal

    async def require_admin_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> ApiPrincipal:
        supplied = supplied_token(authorization, x_api_key)
        if not supplied:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing administrator API key")
        if config.api_key and secrets.compare_digest(supplied, config.api_key):
            return master_principal()
        managed = await api_keys.authenticate(supplied)
        if managed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Managed API keys cannot access administrator endpoints",
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid administrator API key")

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
            "online_desktop_agents": desktop_agents.online_count(),
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
                "scopes": ["admin", "chat", "models", "desktop"],
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
            "health": {
                "online_extensions": len(registry.online_client_ids()),
                "online_desktop_agents": desktop_agents.online_count(),
            },
            "clients": registry.summaries(),
            "models": registry.model_catalog(online_only=True),
            "api_keys": managed_keys_with_stats(),
            "telemetry": telemetry.summary(),
            "recent_requests": telemetry.recent(20),
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
        return {
            "key": {**item, "kind": "managed", "managed": True},
            "token": token,
            "warning": "This token is shown only once. Store it securely now.",
        }

    @app.patch("/api/admin/keys/{key_id}", dependencies=[Depends(require_admin_key)])
    async def update_api_key(key_id: str, body: ApiKeyUpdate) -> dict[str, object]:
        if key_id == "master":
            raise HTTPException(status_code=400, detail="The master key is managed through CHAT2API_API_KEY in .env")
        try:
            item = await api_keys.update(key_id, name=body.name, enabled=body.enabled)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"key": {**item, "kind": "managed", "managed": True}}

    @app.delete("/api/admin/keys/{key_id}", dependencies=[Depends(require_admin_key)])
    async def revoke_api_key(key_id: str) -> dict[str, object]:
        if key_id == "master":
            raise HTTPException(status_code=400, detail="The master key cannot be revoked from the web console")
        try:
            item = await api_keys.revoke(key_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"key": {**item, "kind": "managed", "managed": True}}

    @app.get("/api/admin/requests", dependencies=[Depends(require_admin_key)])
    async def admin_requests(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        status_filter: str | None = Query(default=None, alias="status"),
        model: str | None = Query(default=None),
        key_id: str | None = Query(default=None),
        q: str | None = Query(default=None),
    ) -> dict[str, object]:
        result = telemetry.query(
            limit=limit,
            offset=offset,
            status=status_filter,
            model=model,
            key_id=key_id,
            q=q,
        )
        return {**result, "summary": telemetry.summary()}

    @app.get("/api/admin/requests/{request_id}", dependencies=[Depends(require_admin_key)])
    async def admin_request_detail(request_id: str) -> dict[str, object]:
        row = telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Request record not found")
        return row

    @app.post("/api/extensions/register", response_model=ExtensionRegistrationResult)
    async def register_extension(
        body: ExtensionRegistration,
        x_pairing_code: str | None = Header(default=None),
    ) -> ExtensionRegistrationResult:
        if not x_pairing_code or not secrets.compare_digest(x_pairing_code, config.pairing_code):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid pairing code")
        client_id, token = await registry.register(body.name, body.browser_name, body.version, body.metadata)
        return ExtensionRegistrationResult(client_id=client_id, token=token)

    @app.get("/api/clients", response_model=list[ClientSummary], dependencies=[Depends(require_admin_key)])
    async def clients() -> list[dict[str, object]]:
        return registry.summaries()

    @app.post(
        "/api/desktop/register",
        response_model=DesktopAgentRegistrationResult,
        dependencies=[Depends(require_admin_key)],
    )
    async def register_desktop_agent(body: DesktopAgentRegistration) -> DesktopAgentRegistrationResult:
        agent = await desktop_agents.register(body.name, body.platform, body.version, body.metadata)
        return DesktopAgentRegistrationResult(agent_id=agent.agent_id)

    @app.get("/api/desktop/bootstrap", dependencies=[Depends(require_admin_key)])
    async def desktop_bootstrap(request: Request) -> dict[str, object]:
        return {
            "server_url": config.resolved_public_url(str(request.base_url)),
            "pairing_code": config.pairing_code,
            "extension_name": "chat2api Desktop Chrome",
            "local_bridge_port": 8791,
            "auto_bind": True,
            "expires_in_seconds": 120,
        }

    @app.get("/api/desktop/commands/{agent_id}", dependencies=[Depends(require_admin_key)])
    async def desktop_commands(agent_id: str, timeout: int = Query(default=25, ge=1, le=30)) -> dict[str, object]:
        try:
            return await desktop_agents.wait(agent_id, timeout)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/models")
    async def models(_principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        return {"object": "list", "data": registry.model_catalog(online_only=True)}

    def resolve_client_now(requested: str | None) -> str:
        try:
            return registry.resolve_client(requested)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ConnectionError as error:
            raise error

    async def resolve_client_with_wakeup(requested: str | None) -> str:
        try:
            return resolve_client_now(requested)
        except ConnectionError as initial_error:
            awakened = await desktop_agents.wake("api_request", requested)
            if not awakened:
                raise HTTPException(status_code=503, detail=str(initial_error)) from initial_error
            deadline = asyncio.get_running_loop().time() + config.desktop_wake_timeout_seconds
            last_error: Exception = initial_error
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.5)
                try:
                    return resolve_client_now(requested)
                except ConnectionError as error:
                    last_error = error
                except HTTPException:
                    raise
            raise HTTPException(
                status_code=503,
                detail=f"Desktop client was notified, but the Chrome extension did not become ready: {last_error}",
            ) from last_error

    def completion_id() -> str:
        return "chatcmpl-" + uuid.uuid4().hex

    def standard_usage(prompt: str, completion: str) -> tuple[dict[str, int], dict[str, object]]:
        usage = usage_for(prompt, completion)
        return (
            {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            },
            {"estimated": usage.estimated, "estimator": usage.estimator},
        )

    def chat2api_meta(
        state: RequestState,
        token_meta: dict[str, object],
        principal: ApiPrincipal,
    ) -> dict[str, object]:
        return {
            "client_id": state.client_id,
            "request_id": state.request_id,
            "api_key": {
                "key_id": principal.key_id,
                "name": principal.name,
                "kind": principal.kind,
            },
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
    ) -> tuple[dict[str, int], dict[str, object]]:
        usage, token_meta = standard_usage(prompt, final_text)
        await telemetry.append(
            {
                "request_id": state.request_id,
                "response_id": response_id,
                "client_id": state.client_id,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
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

    async def record_early_error(
        *,
        request_id: str,
        response_id: str,
        requested_model: str,
        prompt: str,
        principal: ApiPrincipal,
        stream: bool,
        prompt_mode: str,
        started_mono: float,
        error: str,
    ) -> None:
        usage, token_meta = standard_usage(prompt, "")
        await telemetry.append(
            {
                "request_id": request_id,
                "response_id": response_id,
                "client_id": None,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
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
        response_id: str,
        model: str,
        delta: dict[str, object],
        finish_reason: str | None = None,
        usage: dict[str, int] | None = None,
        meta: dict[str, object] | None = None,
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
        request: Request,
        state: RequestState,
        response_id: str,
        model: str,
        timeout_seconds: int,
        prompt: str,
        principal: ApiPrincipal,
        prompt_mode: str,
    ) -> AsyncIterator[str]:
        yield "data: " + json.dumps(
            chunk_payload(response_id, model, {"role": "assistant"}), ensure_ascii=False
        ) + "\n\n"
        sent_text = ""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        recorded = False
        try:
            while True:
                if await request.is_disconnected():
                    await registry.send(state.client_id, {"type": "chat.cancel", "request_id": state.request_id})
                    state.completed_mono = state.completed_mono or time.perf_counter()
                    await record_request(
                        state,
                        model,
                        prompt,
                        "error",
                        principal,
                        stream=True,
                        prompt_mode=prompt_mode,
                        response_id=response_id,
                        final_text=sent_text,
                        error="API client disconnected",
                    )
                    recorded = True
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await registry.send(state.client_id, {"type": "chat.cancel", "request_id": state.request_id})
                    state.completed_mono = state.completed_mono or time.perf_counter()
                    await record_request(
                        state,
                        model,
                        prompt,
                        "error",
                        principal,
                        stream=True,
                        prompt_mode=prompt_mode,
                        response_id=response_id,
                        final_text=sent_text,
                        error="Timed out waiting for ChatGPT",
                    )
                    recorded = True
                    yield "data: " + json.dumps(
                        {"error": {"message": "Timed out waiting for ChatGPT", "type": "timeout_error"}},
                        ensure_ascii=False,
                    ) + "\n\n"
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
                        yield "data: " + json.dumps(
                            chunk_payload(response_id, model, {"content": delta}), ensure_ascii=False
                        ) + "\n\n"
                elif event_type == "chat.snapshot":
                    snapshot = str(event.get("text") or "")
                    if snapshot.startswith(sent_text):
                        delta = snapshot[len(sent_text) :]
                        if delta:
                            sent_text = snapshot
                            yield "data: " + json.dumps(
                                chunk_payload(response_id, model, {"content": delta}), ensure_ascii=False
                            ) + "\n\n"
                elif event_type == "chat.completed":
                    final_text = str(event.get("text") or state.text)
                    if final_text.startswith(sent_text):
                        delta = final_text[len(sent_text) :]
                        if delta:
                            sent_text = final_text
                            yield "data: " + json.dumps(
                                chunk_payload(response_id, model, {"content": delta}), ensure_ascii=False
                            ) + "\n\n"
                    usage, token_meta = await record_request(
                        state,
                        model,
                        prompt,
                        "completed",
                        principal,
                        stream=True,
                        prompt_mode=prompt_mode,
                        response_id=response_id,
                        final_text=final_text,
                    )
                    recorded = True
                    yield "data: " + json.dumps(
                        chunk_payload(
                            response_id,
                            model,
                            {},
                            "stop",
                            usage,
                            chat2api_meta(state, token_meta, principal),
                        ),
                        ensure_ascii=False,
                    ) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
                elif event_type in {"chat.error", "chat.cancelled"}:
                    message = str(event.get("error") or event.get("reason") or "Browser request failed")
                    await record_request(
                        state,
                        model,
                        prompt,
                        "error",
                        principal,
                        stream=True,
                        prompt_mode=prompt_mode,
                        response_id=response_id,
                        final_text=sent_text,
                        error=message,
                    )
                    recorded = True
                    yield "data: " + json.dumps(
                        {
                            "error": {"message": message, "type": "browser_error"},
                            "chat2api": {
                                "request_id": state.request_id,
                                "api_key": principal.as_dict(),
                                "diagnostics": state.diagnostics,
                                "timings": state.timings(),
                            },
                        },
                        ensure_ascii=False,
                    ) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
        finally:
            if not recorded and state.completed_mono:
                await record_request(
                    state,
                    model,
                    prompt,
                    "error",
                    principal,
                    stream=True,
                    prompt_mode=prompt_mode,
                    response_id=response_id,
                    final_text=sent_text,
                    error="Streaming request ended before completion",
                )
            registry.busy_clients.discard(state.client_id)
            await broker.release(state.request_id)

    @app.post("/v1/chat/completions")
    async def chat_completions(
        body: ChatCompletionRequest,
        request: Request,
        x_chat2api_client: str | None = Header(default=None),
        principal: ApiPrincipal = Depends(require_api_key),
    ):
        prompt = build_prompt(body.messages, body.prompt_mode)
        request_id = "req_" + uuid.uuid4().hex
        response_id = completion_id()
        request_started = time.perf_counter()
        requested_client = body.client_id or x_chat2api_client

        try:
            client_id = await resolve_client_with_wakeup(requested_client)
        except HTTPException as error:
            await record_early_error(
                request_id=request_id,
                response_id=response_id,
                requested_model=body.model,
                prompt=prompt,
                principal=principal,
                stream=body.stream,
                prompt_mode=body.prompt_mode,
                started_mono=request_started,
                error=str(error.detail),
            )
            raise

        if not registry.supports_model(client_id, body.model):
            message = f"Model {body.model!r} is not reported as available on client {client_id}"
            await record_early_error(
                request_id=request_id,
                response_id=response_id,
                requested_model=body.model,
                prompt=prompt,
                principal=principal,
                stream=body.stream,
                prompt_mode=body.prompt_mode,
                started_mono=request_started,
                error=message,
            )
            raise HTTPException(status_code=400, detail=message)

        try:
            state = await broker.create(request_id, client_id)
        except RuntimeError as error:
            await record_early_error(
                request_id=request_id,
                response_id=response_id,
                requested_model=body.model,
                prompt=prompt,
                principal=principal,
                stream=body.stream,
                prompt_mode=body.prompt_mode,
                started_mono=request_started,
                error=str(error),
            )
            raise HTTPException(status_code=409, detail=str(error)) from error

        registry.busy_clients.add(client_id)
        timeout_seconds = body.timeout or config.request_timeout_seconds
        try:
            await registry.send(
                client_id,
                {
                    "type": "chat.request",
                    "request_id": request_id,
                    "prompt": prompt,
                    "options": {
                        "auto_switch_text": True,
                        "timeout_seconds": timeout_seconds,
                        "model": body.model,
                    },
                },
            )
        except Exception as error:
            state.completed_mono = time.perf_counter()
            await record_request(
                state,
                body.model,
                prompt,
                "error",
                principal,
                stream=body.stream,
                prompt_mode=body.prompt_mode,
                response_id=response_id,
                error=str(error),
            )
            registry.busy_clients.discard(client_id)
            await broker.release(request_id)
            raise HTTPException(status_code=503, detail=str(error)) from error

        if body.stream:
            return StreamingResponse(
                stream_request(
                    request,
                    state,
                    response_id,
                    body.model,
                    timeout_seconds,
                    prompt,
                    principal,
                    body.prompt_mode,
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        try:
            text = await asyncio.wait_for(state.final_future, timeout=timeout_seconds)
            usage, token_meta = await record_request(
                state,
                body.model,
                prompt,
                "completed",
                principal,
                stream=False,
                prompt_mode=body.prompt_mode,
                response_id=response_id,
                final_text=text,
            )
            meta = chat2api_meta(state, token_meta, principal)
        except asyncio.TimeoutError as error:
            await registry.send(client_id, {"type": "chat.cancel", "request_id": request_id})
            state.completed_mono = time.perf_counter()
            await record_request(
                state,
                body.model,
                prompt,
                "error",
                principal,
                stream=False,
                prompt_mode=body.prompt_mode,
                response_id=response_id,
                final_text=state.text,
                error="Timed out waiting for ChatGPT",
            )
            raise HTTPException(status_code=504, detail="Timed out waiting for ChatGPT") from error
        except RuntimeError as error:
            await record_request(
                state,
                body.model,
                prompt,
                "error",
                principal,
                stream=False,
                prompt_mode=body.prompt_mode,
                response_id=response_id,
                final_text=state.text,
                error=str(error),
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
        finally:
            registry.busy_clients.discard(client_id)
            await broker.release(request_id)

        return JSONResponse(
            {
                "id": response_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
                "chat2api": meta,
            }
        )

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
                await registry.touch(
                    client_id,
                    message.get("metadata") if isinstance(message.get("metadata"), dict) else None,
                )
                if message_type in {"heartbeat", "extension.hello", "extension.status"}:
                    if message_type == "heartbeat":
                        await websocket.send_json({"type": "heartbeat.ack", "ts": time.time()})
                    continue
                request_id = str(message.get("request_id") or "")
                if request_id and message_type in {
                    "chat.diagnostics",
                    "chat.started",
                    "chat.delta",
                    "chat.snapshot",
                    "chat.completed",
                    "chat.error",
                    "chat.cancelled",
                }:
                    await broker.publish(request_id, message)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Extension WebSocket failed: %s", client_id)
        finally:
            active_request = broker.client_requests.get(client_id)
            if active_request:
                await broker.publish(
                    active_request,
                    {
                        "type": "chat.error",
                        "request_id": active_request,
                        "error": "Chrome extension disconnected",
                    },
                )
            await registry.detach(client_id, websocket)

    return app


app = create_app()
