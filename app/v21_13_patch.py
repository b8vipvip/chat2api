from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException


PATCH_VERSION = "0.21.13"
# The historical reserve-window fields remain zero because the speculative
# reserve pool is retired. The configured Worker window target is now an
# explicit persistent/prewarmed physical pool owned by v132, while the legacy
# five-minute value describes logical route compatibility only and never closes
# physical pooled windows.
ROUTE_IDLE_CLOSE_SECONDS = 5 * 60
MAX_RESERVE_WINDOW_TARGET = 0
PERSISTENT_WINDOW_POLICY = "persistent-prewarmed-total-window-pool-v132"
WINDOW_DECISION_AUTHORITY = "persistent-window-pool-v132"
ROUTE_WINDOW_AUTHORITY = "conversation-routing-v30+persistent-pool-v132"


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
            "route_idle_close_applies_to_physical_pool": False,
            "max_reserve_window_target": MAX_RESERVE_WINDOW_TARGET,
            "worker_concurrency": configured,
            "speculative_worker_windows": False,
            "persistent_worker_windows": True,
            "prewarmed_worker_windows": True,
            "persistent_window_policy": PERSISTENT_WINDOW_POLICY,
            "window_decision_authority": WINDOW_DECISION_AUTHORITY,
            "route_window_authority": ROUTE_WINDOW_AUTHORITY,
            "server_scheduler_authority": "server-single-authority-scheduler-v58",
            "version": PATCH_VERSION,
        }

    return app
