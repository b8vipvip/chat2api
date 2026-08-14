from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.20.2"


async def _response_bytes(response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def install_v20_2_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    app.version = PATCH_VERSION

    base_model_catalog = registry.model_catalog
    if not getattr(registry, "_chat2api_v20_2_live_catalog", False):
        def model_catalog_v20_2(online_only: bool = True) -> list[dict[str, Any]]:
            rows = [dict(row) for row in base_model_catalog(online_only=online_only)]
            for row in rows:
                model_id = str(row.get("id") or "")
                if model_id == "gpt-live":
                    row["label"] = "GPT Live · 实时语音主模型"
                    row["realtime"] = {
                        "supported": True,
                        "protocol": "chat2api-live-v1",
                        "endpoint": "/v1/audio/realtime",
                        "input_audio_format": "pcm16le-16000-mono",
                        "output_audio_format": "pcm16le-24000-mono",
                    }
                elif model_id == "gpt-live-mini":
                    row["label"] = "GPT Live Mini · GPT Live 兼容别名"
                    row["alias_of"] = "gpt-live"
                    row["realtime"] = {
                        "supported": True,
                        "protocol": "chat2api-live-v1",
                        "endpoint": "/v1/audio/realtime",
                        "effective_model": "gpt-live",
                        "same_browser_voice_route": True,
                        "performance_difference_guaranteed": False,
                    }
            return rows

        registry.model_catalog = model_catalog_v20_2
        registry._chat2api_v20_2_live_catalog = True

    @app.get("/assets/chat2api-v20-2.js")
    async def admin_v20_2_js() -> Response:
        path = Path(__file__).with_name("admin_v20_2.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def v20_2_realtime_voice_metadata(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type and (
            path in {"/", "/healthz", "/api/admin/overview"}
            or path.startswith("/api/admin/")
        ):
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if "server_version" in payload or path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["realtime_voice_websocket"] = True
                        capabilities["realtime_voice_managed_api_key"] = True
                        capabilities["realtime_voice_browser_session_token"] = True
                        capabilities["gpt_live_mini_alias_of_gpt_live"] = True
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v20-2.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
