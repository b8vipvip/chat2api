"""Serve the C-track admin console feature asset through the existing admin UI owner."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response


ASSET_NAME = "chat2api-control-plane-admin-v1.js"
ASSET_PATH = Path(__file__).with_name("admin_control_plane_admin_v1.js")


def install_admin_control_plane_ui_v1(app: FastAPI) -> FastAPI:
    if getattr(app.state, "admin_control_plane_ui_v1_installed", False):
        return app
    if not ASSET_PATH.is_file():
        raise RuntimeError("C-track admin UI asset is missing")

    @app.get("/assets/" + ASSET_NAME, include_in_schema=False)
    async def admin_control_plane_asset() -> Response:
        try:
            payload = ASSET_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("C-track admin UI asset is unavailable") from exc
        return Response(
            payload,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    app.state.admin_control_plane_ui_v1_installed = True
    return app
