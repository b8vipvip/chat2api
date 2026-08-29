from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .response_semantic_guard_patch import install_response_semantic_guard_patch
from .timezone_utils import beijing_now_iso


PATCH_ID = "playground-chat-v2"
ASSET_PATH = "/assets/chat2api-playground-chat.js"


class PlaygroundChatRecord(BaseModel):
    request_id: str = Field(min_length=8, max_length=160)
    model: str = Field(min_length=1, max_length=120)
    api_key_id: str | None = Field(default=None, max_length=160)
    api_key_name: str | None = Field(default=None, max_length=200)
    status: str = Field(default="passed", max_length=32)
    duration_ms: float | None = Field(default=None, ge=0)
    first_token_ms: float | None = Field(default=None, ge=0)
    prompt: str = Field(default="", max_length=20000)
    response_chars: int = Field(default=0, ge=0)
    attachments_count: int = Field(default=0, ge=0, le=4)
    error: str | None = Field(default=None, max_length=2000)


async def _response_bytes(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def _record_status(value: str) -> str:
    status = str(value or "").lower()
    if status in {"passed", "failed", "stalled", "warning", "cancelled"}:
        return status
    return "failed"


def install_playground_chat_patch(app: FastAPI) -> FastAPI:
    """Add a persistent multi-turn chat surface to the administrator Playground.

    Manual messages still exercise the production /v1/chat/completions boundary
    verbatim with a selected business API key. Each attempted turn is additionally
    persisted into the same TestRunStore used by the automatic Playground so the
    operator can correlate chat UI behavior with request diagnostics.
    """
    if getattr(app.state, "playground_chat_patch_installed", False):
        return app
    if not getattr(app.state, "playground_lifecycle_patch_installed", False):
        raise RuntimeError("playground lifecycle must be installed before playground chat")

    app.state.playground_chat_patch_installed = True
    install_response_semantic_guard_patch(app)

    @app.get(ASSET_PATH, include_in_schema=False)
    async def playground_chat_js() -> Response:
        path = Path(__file__).with_name("admin_playground_chat.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/admin/playground/chat-records")
    async def save_playground_chat_record(body: PlaygroundChatRecord) -> dict[str, Any]:
        status = _record_status(body.status)
        now = beijing_now_iso()
        run_id = "chatrun_" + uuid.uuid4().hex
        prompt_preview = " ".join(body.prompt.split())[:240]
        summary = (
            f"聊天完成 · {body.response_chars} chars"
            if status == "passed"
            else f"聊天{status} · {str(body.error or 'request failed')[:300]}"
        )
        row = await app.state.test_runs.upsert(
            {
                "run_id": run_id,
                "request_id": body.request_id,
                "request_ids": [body.request_id],
                "test_type": "chat",
                "model": body.model,
                "api_key_id": body.api_key_id,
                "api_key_name": body.api_key_name or body.api_key_id or "手动粘贴",
                "status": status,
                "started_at": now,
                "finished_at": now,
                "duration_ms": body.duration_ms,
                "error": body.error,
                "summary": summary,
                "results": [
                    {
                        "kind": "chat",
                        "label": "聊天对话",
                        "status": status,
                        "message": summary,
                        "error": body.error,
                        "request_id": body.request_id,
                        "total_ms": body.duration_ms,
                        "first_token_ms": body.first_token_ms,
                        "response_chars": body.response_chars,
                        "attachments_count": body.attachments_count,
                    }
                ],
                "quality": {
                    "source": "manual-playground-chat",
                    "playground_chat": PATCH_ID,
                    "prompt_preview": prompt_preview,
                    "prompt_chars": len(body.prompt),
                    "response_chars": body.response_chars,
                    "attachments_count": body.attachments_count,
                },
            }
        )
        return {"run": row}

    @app.middleware("http")
    async def playground_chat_console(request: Request, call_next):
        response = await call_next(request)
        if request.url.path not in {"/admin", "/developers"}:
            return response
        if "text/html" not in response.headers.get("content-type", ""):
            return response

        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        marker = f'<script src="{ASSET_PATH}"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
