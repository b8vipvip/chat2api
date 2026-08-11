from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.10.0"


def install_v10_patch(app: FastAPI) -> FastAPI:
    app.version = PATCH_VERSION

    @app.get("/assets/chat2api-v10.js")
    async def admin_v10_js() -> Response:
        path = Path(__file__).with_name("admin_v10.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

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
    async def v10_console_and_version(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if (
            path in {"/", "/healthz", "/api/admin/overview"}
            or (path.startswith("/api/admin/requests/") and path.endswith("/log"))
        ) and "application/json" in content_type:
            raw = await response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                if path in {"/", "/healthz", "/api/admin/overview"}:
                    payload["version"] = PATCH_VERSION
                if path.startswith("/api/admin/requests/") and path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["extension_runtime_log"] = True
                        capabilities["browser_local_time_display"] = True
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v10.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
