from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_ID = "playground-chat-v1"
ASSET_PATH = "/assets/chat2api-playground-chat.js"


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


def install_playground_chat_patch(app: FastAPI) -> FastAPI:
    """Add a manual, multi-turn chat window to the administrator Playground.

    The browser intentionally sends manual chat messages directly through the
    production /v1/chat/completions boundary with the selected business API key.
    This keeps the Playground representative of real callers and ensures the
    automatic randomized-test prompt layer cannot rewrite administrator-authored
    chat messages.
    """
    if getattr(app.state, "playground_chat_patch_installed", False):
        return app
    if not getattr(app.state, "playground_lifecycle_patch_installed", False):
        raise RuntimeError("playground lifecycle must be installed before playground chat")

    app.state.playground_chat_patch_installed = True

    @app.get(ASSET_PATH, include_in_schema=False)
    async def playground_chat_js() -> Response:
        path = Path(__file__).with_name("admin_playground_chat.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

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
