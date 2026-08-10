from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.9.0"


def _remove_dictation_routes(app: FastAPI) -> None:
    kept = []
    for route in app.router.routes:
        path = str(getattr(route, "path", "") or "")
        methods = set(getattr(route, "methods", set()) or set())
        if path == "/v1/audio/transcriptions" and "POST" in methods:
            continue
        kept.append(route)
    app.router.routes[:] = kept
    app.openapi_schema = None


def install_v9_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    app.version = PATCH_VERSION
    _remove_dictation_routes(app)

    base_model_catalog = registry.model_catalog

    def model_catalog_v9(online_only: bool = True) -> list[dict[str, Any]]:
        rows = list(base_model_catalog(online_only=online_only))
        cleaned: list[dict[str, Any]] = []
        for row in rows:
            model_id = str(row.get("id") or "").lower()
            if model_id in {"gpt-dictation", "chatgpt-dictation"}:
                continue
            value = dict(row)
            capabilities = [
                item for item in list(value.get("capabilities") or [])
                if str(item).lower() not in {"dictation", "dictation-auto-send", "audio-transcription", "gpt-dictation"}
            ]
            if capabilities:
                value["capabilities"] = capabilities
            cleaned.append(value)
        return cleaned

    registry.model_catalog = model_catalog_v9

    @app.get("/assets/chat2api-v9.js")
    async def admin_v9_js() -> Response:
        path = Path(__file__).with_name("admin_v9.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

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
    async def v9_console_and_version(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path

        if path in {"/", "/healthz", "/api/admin/overview"} and "application/json" in response.headers.get("content-type", ""):
            raw = await response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        for name in ("dictation", "audio_transcription", "gpt_dictation"):
                            capabilities.pop(name, None)
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in response.headers.get("content-type", ""):
            raw = await response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v9.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)
        return response

    return app
