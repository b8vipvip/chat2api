from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_VERSION = "0.22.38"
ASSET = "/assets/chat2api-linux-worker-proxy-health-v55.js"


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


def install_linux_worker_proxy_health_patch(app: FastAPI) -> FastAPI:
    """Install the final Linux Worker proxy-health presentation owner.

    Historical stable-table code intentionally blocks the legacy Chinese-progress
    overlay because both used to repaint the same cells. Proxy health is now a
    separate v55 layer with its own marker and mutation reconciliation, so the
    table can keep owning layout while v55 exclusively owns the proxy-status cell.
    """

    if getattr(app.state, "linux_worker_proxy_health_patch_installed", False):
        return app
    app.state.linux_worker_proxy_health_patch_installed = True

    @app.get(ASSET, include_in_schema=False)
    async def linux_worker_proxy_health_asset() -> Response:
        path = Path(__file__).with_name("admin_linux_worker_proxy_health_v55.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def linux_worker_proxy_health_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
            return response

        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        marker = f'<script src="{ASSET}"></script>'
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
