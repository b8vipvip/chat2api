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
PERSISTENT_WINDOW_IDLE_CLOSE_SECONDS = 0
MAX_RESERVE_WINDOW_TARGET = 0
MIN_WINDOW_TARGET = 1
MAX_WINDOW_TARGET = 32
PERSISTENT_WINDOW_POLICY = "persistent-prewarmed-total-window-pool-v132"
WINDOW_DECISION_AUTHORITY = "persistent-window-pool-v132"
LOGICAL_ROUTE_AUTHORITY = "conversation-routing-v30"
ROUTE_WINDOW_AUTHORITY = "conversation-routing-v30+persistent-pool-v132"


def _window_target(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(fallback)
    return max(MIN_WINDOW_TARGET, min(MAX_WINDOW_TARGET, parsed))


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

        # worker_limits_clipboard_v121_patch is installed later in the bootstrap.
        # Resolve it at request time so this compatibility endpoint reports the
        # same effective per-Worker target that windows.limit sends to v132.
        window_runtime = getattr(app.state, "worker_window_limits", {})
        window_limit_for = window_runtime.get("limit_for") if isinstance(window_runtime, dict) else None
        window_source_for = window_runtime.get("source_for") if isinstance(window_runtime, dict) else None
        if callable(window_limit_for):
            try:
                persistent_target = _window_target(window_limit_for(client_id), configured)
            except Exception:
                persistent_target = _window_target(configured, configured)
        else:
            persistent_target = _window_target(configured, configured)
        if callable(window_source_for):
            try:
                persistent_source = str(window_source_for(client_id) or "concurrency")[:40]
            except Exception:
                persistent_source = "concurrency"
        else:
            persistent_source = "concurrency"

        return {
            # Legacy speculative-reserve compatibility fields. The v132 pool is
            # persistent/prewarmed and must not be represented as the retired v29
            # speculative spare pool.
            "reserve_window_target": 0,
            "route_idle_close_seconds": ROUTE_IDLE_CLOSE_SECONDS,
            "route_idle_close_applies_to_physical_pool": False,
            "max_reserve_window_target": MAX_RESERVE_WINDOW_TARGET,
            "speculative_worker_windows": False,

            "worker_concurrency": configured,
            "persistent_worker_windows": True,
            "prewarmed_worker_windows": True,
            "persistent_window_pool": True,
            "persistent_window_pool_revision": 132,
            "persistent_window_target": persistent_target,
            "persistent_window_target_source": persistent_source,
            "persistent_window_idle_close_seconds": PERSISTENT_WINDOW_IDLE_CLOSE_SECONDS,
            "persistent_window_policy": PERSISTENT_WINDOW_POLICY,
            "window_decision_authority": WINDOW_DECISION_AUTHORITY,
            "logical_route_authority": LOGICAL_ROUTE_AUTHORITY,
            "route_window_authority": ROUTE_WINDOW_AUTHORITY,
            "server_scheduler_authority": "server-single-authority-scheduler-v58",
            "version": PATCH_VERSION,
        }

    return app
