from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException


PATCH_VERSION = "0.21.13"
# v0.8.30 removes the speculative browser reserve pool. Keep this historical
# endpoint for bridge compatibility, but publish the current single-authority
# policy instead of advertising a non-existent warm-window target.
ROUTE_IDLE_CLOSE_SECONDS = 5 * 60
MAX_RESERVE_WINDOW_TARGET = 0


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
            configured = max(1, int(limit_for(client_id)))
        else:
            configured = max(
                1,
                int(
                    (runtime.get("max_concurrency") if isinstance(runtime, dict) else 0)
                    or getattr(broker, "max_concurrency", 0)
                    or 1
                ),
            )
        return {
            "reserve_window_target": 0,
            "route_idle_close_seconds": ROUTE_IDLE_CLOSE_SECONDS,
            "max_reserve_window_target": MAX_RESERVE_WINDOW_TARGET,
            "worker_concurrency": configured,
            "speculative_worker_windows": False,
            "window_decision_authority": "conversation-routing-v30",
            "server_scheduler_authority": "server-single-authority-scheduler-v58",
            "version": PATCH_VERSION,
        }

    return app
