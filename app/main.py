from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .broker import RequestBroker, RequestState
from .config import Settings, get_settings
from .desktop import DesktopAgentHub
from .models import (
    ChatCompletionRequest,
    ClientSummary,
    DesktopAgentRegistration,
    DesktopAgentRegistrationResult,
    ExtensionRegistration,
    ExtensionRegistrationResult,
)
from .prompting import build_prompt
from .registry import ClientRegistry

logger = logging.getLogger("chat2api")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    registry = ClientRegistry(config.data_dir)
    broker = RequestBroker()
    desktop_agents = DesktopAgentHub()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await registry.load()
        if config.api_key in {"", "change-me"}:
            logger.warning("CHAT2API_API_KEY is using an unsafe default. Change it before remote exposure.")
        if config.pairing_code in {"", "change-me-pairing"}:
            logger.warning("CHAT2API_PAIRING_CODE is using an unsafe default. Change it before remote exposure.")
        yield

    app = FastAPI(
        title="chat2api",
        version="0.2.0",
        description="OpenAI-compatible bridge from a remote API to a logged-in ChatGPT browser tab.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.registry = registry
    app.state.broker = broker
    app.state.desktop_agents = desktop_agents
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.origins,
        allow_credentials=config.origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def require_api_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> None:
        supplied = x_api_key or ""
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not supplied or not secrets.compare_digest(supplied, config.api_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": "chat2api", "status": "ok", "docs": "/docs", "version": "0.2.0"}

    @app.get("/healthz")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "online_extensions": len(registry.online_client_ids()),
            "online_desktop_agents": desktop_agents.online_count(),
        }

    @app.post("/api/extensions/register", response_model=ExtensionRegistrationResult)
    async def register_extension(
        body: ExtensionRegistration,
        x_pairing_code: str | None = Header(default=None),
    ) -> ExtensionRegistrationResult:
        if not x_pairing_code or not secrets.compare_digest(x_pairing_code, config.pairing_code):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid pairing code")
        client_id, token = await registry.register(body.name, body.browser_name, body.version, body.metadata)
        return ExtensionRegistrationResult(client_id=client_id, token=token)

    @app.get("/api/clients", response_model=list[ClientSummary], dependencies=[Depends(require_api_key)])
    async def clients() -> list[dict[str, object]]:
        return registry.summaries()

    @app.post(
        "/api/desktop/register",
        response_model=DesktopAgentRegistrationResult,
        dependencies=[Depends(require_api_key)],
    )
    async def register_desktop_agent(body: DesktopAgentRegistration) -> DesktopAgentRegistrationResult:
        agent = await desktop_agents.register(body.name, body.platform, body.version, body.metadata)
        return DesktopAgentRegistrationResult(agent_id=agent.agent_id)

    @app.get("/api/desktop/bootstrap", dependencies=[Depends(require_api_key)])
    async def desktop_bootstrap(request: Request) -> dict[str, object]:
        return {
            "server_url": config.resolved_public_url(str(request.base_url)),
            "pairing_code": config.pairing_code,
            "extension_name": "chat2api Desktop Chrome",
            "local_bridge_port": 8791,
            "auto_bind": True,
            "expires_in_seconds": 120,
        }

    @app.get("/api/desktop/commands/{agent_id}", dependencies=[Depends(require_api_key)])
    async def desktop_commands(agent_id: str, timeout: int = Query(default=25, ge=1, le=30)) -> dict[str, object]:
        try:
            return await desktop_agents.wait(agent_id, timeout)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/models", dependencies=[Depends(require_api_key)])
    async def models() -> dict[str, object]:
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
                    continue
                except HTTPException:
                    raise
            raise HTTPException(
                status_code=503,
                detail=f"Desktop client was notified, but the Chrome extension did not become ready: {last_error}",
            ) from last_error

    def completion_id() -> str:
        return "chatcmpl-" + uuid.uuid4().hex

    def chunk_payload(
        response_id: str,
        model: str,
        delta: dict[str, object],
        finish_reason: str | None = None,
    ) -> dict[str, object]:
        return {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    async def stream_request(
        request: Request,
        state: RequestState,
        response_id: str,
        model: str,
        timeout_seconds: int,
    ) -> AsyncIterator[str]:
        yield "data: " + json.dumps(chunk_payload(response_id, model, {"role": "assistant"}), ensure_ascii=False) + "\n\n"
        sent_text = ""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        try:
            while True:
                if await request.is_disconnected():
                    await registry.send(state.client_id, {"type": "chat.cancel", "request_id": state.request_id})
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await registry.send(state.client_id, {"type": "chat.cancel", "request_id": state.request_id})
                    error = {"error": {"message": "Timed out waiting for ChatGPT", "type": "timeout_error"}}
                    yield "data: " + json.dumps(error, ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
                try:
                    event = await asyncio.wait_for(state.queue.get(), timeout=min(1.0, remaining))
                except asyncio.TimeoutError:
                    continue
                event_type = event.get("type")
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
                    yield "data: " + json.dumps(chunk_payload(response_id, model, {}, "stop"), ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
                elif event_type in {"chat.error", "chat.cancelled"}:
                    message = str(event.get("error") or event.get("reason") or "Browser request failed")
                    yield "data: " + json.dumps({"error": {"message": message, "type": "browser_error"}}, ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
        finally:
            registry.busy_clients.discard(state.client_id)
            await broker.release(state.request_id)

    @app.post("/v1/chat/completions", dependencies=[Depends(require_api_key)])
    async def chat_completions(
        body: ChatCompletionRequest,
        request: Request,
        x_chat2api_client: str | None = Header(default=None),
    ):
        requested_client = body.client_id or x_chat2api_client
        client_id = await resolve_client_with_wakeup(requested_client)
        if not registry.supports_model(client_id, body.model):
            raise HTTPException(
                status_code=400,
                detail=f"Model {body.model!r} is not reported as available on client {client_id}",
            )

        request_id = "req_" + uuid.uuid4().hex
        try:
            state = await broker.create(request_id, client_id)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        registry.busy_clients.add(client_id)
        prompt = build_prompt(body.messages, body.prompt_mode)
        timeout_seconds = body.timeout or config.request_timeout_seconds
        response_id = completion_id()
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
            registry.busy_clients.discard(client_id)
            await broker.release(request_id)
            raise HTTPException(status_code=503, detail=str(error)) from error

        if body.stream:
            return StreamingResponse(
                stream_request(request, state, response_id, body.model, timeout_seconds),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        try:
            text = await asyncio.wait_for(state.final_future, timeout=timeout_seconds)
        except asyncio.TimeoutError as error:
            await registry.send(client_id, {"type": "chat.cancel", "request_id": request_id})
            raise HTTPException(status_code=504, detail="Timed out waiting for ChatGPT") from error
        except RuntimeError as error:
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
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "chat2api": {"client_id": client_id, "request_id": request_id},
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
            await websocket.send_json({"type": "server.hello", "client_id": client_id, "version": "0.2.0"})
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
                    {"type": "chat.error", "request_id": active_request, "error": "Chrome extension disconnected"},
                )
            await registry.detach(client_id, websocket)

    return app


app = create_app()
