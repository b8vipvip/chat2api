from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_VERSION = "0.21.11"
ADMIN_COLUMN_MENU_VIEWPORT_ASSET = "/assets/chat2api-v21-11-column-menu.js"


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


def install_v21_11_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "v21_11_column_menu_viewport_installed", False):
        return app

    app.state.v21_11_column_menu_viewport_installed = True

    @app.get(ADMIN_COLUMN_MENU_VIEWPORT_ASSET, include_in_schema=False)
    async def admin_v21_11_js() -> Response:
        path = Path(__file__).with_name("admin_v21_11.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def v21_11_column_menu_viewport(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
            return response

        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        marker = f'<script src="{ADMIN_COLUMN_MENU_VIEWPORT_ASSET}"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
