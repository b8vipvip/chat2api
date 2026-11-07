"""Browser-independent dispatch for the *manual* administrator chat Playground.

The Playground previously held a browser HTTP request open for the entire
multi-minute ChatGPT generation. On some real routes that browser-facing hop
closes after ~3s even though the Worker later completes. A short authenticated
202 starts the SAME /v1/chat/completions endpoint inside the application,
then the browser observes the authoritative request_id outcome. This is a
presentation transport only, NOT a new Worker/terminal owner and NOT a
replacement for the separate real SSE transport test.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, SecretStr

log = logging.getLogger("chat2api.playground.detached")
_REQUEST_ID = re.compile(r"req_[a-f0-9]{32}\Z", re.I)
_ALLOWED_PAYLOAD = {
    "model", "messages", "prompt_mode", "attachments", "stream",
    "reasoning_effort",
}


class DetachedChatRequest(BaseModel):
    request_id: str = Field(min_length=36, max_length=36)
    api_key: SecretStr = Field(min_length=1, max_length=512)
    payload: dict[str, Any]


def _validated_payload(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) - _ALLOWED_PAYLOAD or value.get("stream") is not False:
        raise HTTPException(status_code=422, detail="Only non-stream manual chat is accepted")
    if not isinstance(value.get("model"), str) or not 1 <= len(value["model"]) <= 120:
        raise HTTPException(status_code=422, detail="Invalid chat model")
    messages = value.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise HTTPException(status_code=422, detail="Invalid chat messages")
    for item in messages:
        if not isinstance(item, dict) or set(item) - {"role", "content"}:
            raise HTTPException(status_code=422, detail="Invalid chat message shape")
        if item.get("role") not in {"system", "developer", "user", "assistant"}:
            raise HTTPException(status_code=422, detail="Invalid role")
    if messages[-1].get("role") != "user" or not isinstance(messages[-1].get("content"), str) or len(messages[-1]["content"]) > 20000:
        raise HTTPException(status_code=422, detail="Invalid final message")
    attachments = value.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 4:
        raise HTTPException(status_code=422, detail="Invalid attachments")
    if value.get("prompt_mode", "full") not in {"full", "incremental"}:
        raise HTTPException(status_code=422, detail="Invalid prompt mode")
    if len(str(value).encode("utf-8")) > 150000:
        raise HTTPException(status_code=413, detail="Chat request too large")
    return value


def install_playground_detached_chat(app: FastAPI, chat_run_id) -> None:
    if getattr(app.state, "playground_detached_chat_v292_installed", False):
        return
    app.state.playground_detached_chat_v292_installed = True
    # Never save the API credential or prompt in these job records.
    # Task closures retain the secret only while the request is running.
    jobs: dict[str, dict[str, Any]] = {}
    app.state.playground_detached_chat_jobs = jobs

    async def require_admin(authorization: str | None = Header(default=None)) -> None:
        supplied = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
        master = str(app.state.settings.api_key or "")
        if not master or not supplied or not secrets.compare_digest(supplied, master):
            raise HTTPException(status_code=403, detail="Administrator key required")

    async def run_internal(request_id: str, token: str, payload: dict[str, Any], record: dict) -> None:
        try:
            # In-process ASGI avoids the browser/reverse-proxy long-lived hop.
            # The same production /v1 route owns scheduling, Broker, Worker and
            # its one terminal; no new agent request path or fallback submission.
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://playground-internal",
                timeout=None,
            ) as client:
                response = await asyncio.wait_for(client.post(
                    "/v1/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": "Bearer " + token,
                        "X-Chat2API-Request-ID": request_id,
                        "X-Chat2API-Playground-Dispatch": "server-detached-v292",
                    },
                ), timeout=960)
            record["state"] = "done" if response.status_code == 200 else "failed"
            record["http_status"] = response.status_code
        except asyncio.CancelledError:
            record["state"] = "cancelled"
            raise
        except Exception:
            # No prompts, tokens, headers or page content in logs/records.
            record["state"] = "transport-error"
            log.warning("Detached Playground internal request failed id=%s", request_id)
        finally:
            record["finished_at"] = time.monotonic()

    @app.post(
        "/api/admin/playground/chat-dispatch",
        dependencies=[Depends(require_admin)],
        status_code=202,
    )
    async def start_detached_chat(body: DetachedChatRequest) -> dict[str, Any]:
        request_id = body.request_id
        if not _REQUEST_ID.fullmatch(request_id):
            raise HTTPException(status_code=422, detail="Invalid request ID")
        payload = _validated_payload(body.payload)
        # Run records must exist before dispatch to maintain the one-to-one
        # Test History and Request History contract.
        if not app.state.test_runs.get(chat_run_id(request_id)):
            raise HTTPException(status_code=409, detail="Playground chat run not registered")
        now = time.monotonic()
        for key, item in list(jobs.items()):
            if item.get("finished_at") and now - item["finished_at"] > 3600:
                jobs.pop(key, None)
        if request_id in jobs:
            return {"request_id": request_id, "state": jobs[request_id]["state"],
                    "reused": True, "transport": "server-detached-v292"}
        if sum(item["state"] == "running" for item in jobs.values()) >= 8:
            raise HTTPException(status_code=429, detail="Too many active Playground chats")
        record: dict[str, Any] = {"state": "running", "finished_at": None, "http_status": None}
        jobs[request_id] = record
        record["task"] = asyncio.create_task(
            run_internal(request_id, body.api_key.get_secret_value(), payload, record),
            name="playground-" + request_id[:20],
        )
        return {"request_id": request_id, "state": "running", "reused": False,
                "transport": "server-detached-v292"}

    @app.get(
        "/api/admin/playground/chat-dispatch/{request_id}",
        dependencies=[Depends(require_admin)],
    )
    async def detached_chat_status(request_id: str) -> dict[str, Any]:
        if not _REQUEST_ID.fullmatch(request_id):
            raise HTTPException(status_code=422, detail="Invalid request ID")
        item = jobs.get(request_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Unknown detached request")
        return {"request_id": request_id, "state": item["state"],
                "http_status": item["http_status"],
                "transport": "server-detached-v292"}
