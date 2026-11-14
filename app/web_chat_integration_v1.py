"""Production installer for the safe Web Chat renderer and recovery/file APIs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Response


def _asset_handler(asset_path: Path, media_type: str):
    async def serve_asset() -> Response:
        return Response(
            asset_path.read_bytes(),
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
            },
        )
    return serve_asset


def install_web_chat_features_v1(app: Any) -> Any:
    if getattr(app.state, "web_chat_features_v1_installed", False):
        return app

    required = ("user_accounts", "file_store", "telemetry", "broker", "registry")
    missing = [name for name in required if getattr(app.state, name, None) is None]
    if missing:
        raise RuntimeError("Web Chat integration requires app state: " + ", ".join(missing))

    from .web_chat_recovery_api import install_web_chat_recovery_api
    from .web_chat_file_download_api import install_web_chat_file_download_api
    install_web_chat_recovery_api(app)
    install_web_chat_file_download_api(app)

    assets = {
        "/assets/chat2api-chat-renderer-v2.js": ("chat_renderer_v2.js", "application/javascript"),
        "/assets/chat2api-chat-stream-recovery-v1.js": ("chat_stream_recovery_v1.js", "application/javascript"),
    }
    for route_path, (filename, media_type) in assets.items():
        asset_path = Path(__file__).with_name(filename)
        if not asset_path.is_file():
            raise RuntimeError("Required Web Chat asset is missing: " + filename)
        app.add_api_route(
            route_path, _asset_handler(asset_path, media_type),
            methods=["GET"], include_in_schema=False,
        )

    @app.middleware("http")
    async def web_chat_private_cache_boundary(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith(("/api/user/chat/requests/", "/api/user/chat/files/", "/api/user/files/")):
            response.headers["Cache-Control"] = "private, no-store"
            vary_values = [value.strip() for value in response.headers.get("Vary", "").split(",") if value.strip()]
            if not any(value.lower() == "cookie" for value in vary_values):
                vary_values.append("Cookie")
            response.headers["Vary"] = ", ".join(vary_values)
        return response
    app.state.web_chat_features_v1_installed = True
    app.state.web_chat_integration_version = 1
    return app
