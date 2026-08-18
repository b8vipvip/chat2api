from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_VERSION = "0.21.6"
ADMIN_HEALTH_ASSET = "/assets/chat2api-v21-6.js"


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


def install_v21_6_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "v21_6_health_center_installed", False):
        return app

    app.version = PATCH_VERSION
    app.state.v21_6_health_center_installed = True

    @app.get(ADMIN_HEALTH_ASSET, include_in_schema=False)
    async def admin_v21_6_js() -> Response:
        path = Path(__file__).with_name("admin_v21_6.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def v21_6_extension_health_console(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")
        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = f'<script src="{ADMIN_HEALTH_ASSET}"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)
        return response

    return app
