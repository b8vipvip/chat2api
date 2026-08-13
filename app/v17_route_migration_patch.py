from __future__ import annotations

from fastapi import FastAPI


def install_v17_route_migration_patch(app: FastAPI) -> FastAPI:
    """Seed API-key device affinity from historical request records on upgrade."""
    telemetry = app.state.telemetry
    registry = app.state.registry

    if getattr(telemetry, "_chat2api_v17_route_migration_wrapped", False):
        return app

    base_load = telemetry.load

    async def load_and_restore_routes() -> None:
        await base_load()
        latest: dict[str, str] = {}
        for row in telemetry.items:
            key_id = str(row.get("api_key_id") or "").strip()
            client_id = str(row.get("client_id") or "").strip()
            if not key_id or key_id == "master" or not client_id:
                continue
            if client_id not in registry.clients:
                continue
            latest[key_id] = client_id
        changed = False
        for key_id, client_id in latest.items():
            if key_id not in registry.api_key_routes:
                registry.api_key_routes[key_id] = client_id
                changed = True
        if changed:
            await registry.save()

    telemetry.load = load_and_restore_routes
    telemetry._chat2api_v17_route_migration_wrapped = True
    return app
