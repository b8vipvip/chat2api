from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException


PATCH_VERSION = "0.21.13"
ROUTE_IDLE_CLOSE_SECONDS = 10 * 60
MAX_RESERVE_WINDOW_TARGET = 32


def install_v21_13_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "v21_13_reserve_runtime_config_installed", False):
        return app

    registry = app.state.registry
    app.state.v21_13_reserve_runtime_config_installed = True

    @app.get("/api/extensions/runtime-config")
    async def extension_runtime_config(
        x_extension_client_id: str | None = Header(default=None),
        x_extension_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        client_id = str(x_extension_client_id or "").strip()
        token = str(x_extension_token or "").strip()
        if not client_id or not token or not await registry.authenticate(client_id, token):
            raise HTTPException(status_code=401, detail="Invalid extension credentials")

        runtime = getattr(app.state, "concurrency_config", {})
        broker = getattr(app.state, "broker", None)
        limit_for = runtime.get("limit_for") if isinstance(runtime, dict) else None
        if callable(limit_for):
            configured = int(limit_for(client_id))
        else:
            configured = int(
                (runtime.get("max_concurrency") if isinstance(runtime, dict) else 0)
                or getattr(broker, "max_concurrency", 0)
                or 1
            )
        target = max(1, min(MAX_RESERVE_WINDOW_TARGET, configured))
        return {
            "reserve_window_target": target,
            "route_idle_close_seconds": ROUTE_IDLE_CLOSE_SECONDS,
            "max_reserve_window_target": MAX_RESERVE_WINDOW_TARGET,
            "version": PATCH_VERSION,
        }

    return app
