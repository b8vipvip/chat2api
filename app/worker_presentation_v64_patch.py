from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .admin_auth import SESSION_COOKIE


PATCH_REVISION = 66


class PairingNameUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def install_worker_presentation_v64_patch(app: FastAPI) -> FastAPI:
    """Expose Worker presentation data without installing a second DOM renderer.

    v64/v65 appended a second admin-console presentation asset after the canonical
    Worker list renderer. Even after making its own DOM ordering convergent, that
    layer still maintained independent observers/refresh timers against the same
    table. v66 keeps the server-side device-name decoration and rename API only;
    the existing canonical admin_extension_columns renderer is the sole DOM owner.
    """
    if getattr(app.state, "worker_presentation_v66_installed", False):
        return app
    app.state.worker_presentation_v66_installed = True

    registry = app.state.registry
    pairings = app.state.pairings
    sessions = app.state.admin_sessions

    if not getattr(registry, "_chat2api_worker_device_name_v64", False):
        base_summaries = registry.summaries

        def summaries_with_device_names() -> list[dict[str, Any]]:
            rows = base_summaries()
            by_pairing: dict[str, str] = {}
            by_client: dict[str, tuple[str, str]] = {}
            for pairing in pairings.items.values():
                name = str(pairing.name or "").strip()
                pairing_id = str(pairing.pairing_id or "").strip()
                if pairing_id and name:
                    by_pairing[pairing_id] = name
                client_id = str(pairing.bound_client_id or "").strip()
                if client_id and name:
                    by_client[client_id] = (pairing_id, name)

            decorated: list[dict[str, Any]] = []
            for raw in rows:
                row = dict(raw)
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                pairing_id = str(row.get("pairing_id") or metadata.get("pairing_id") or "").strip()
                client_id = str(row.get("client_id") or "").strip()
                fallback_pairing, fallback_name = by_client.get(client_id, ("", ""))
                if not pairing_id:
                    pairing_id = fallback_pairing
                device_name = by_pairing.get(pairing_id) or fallback_name
                row["device_code_id"] = pairing_id or None
                row["device_name"] = device_name or None
                decorated.append(row)
            return decorated

        registry.summaries = summaries_with_device_names
        registry._chat2api_worker_device_name_v64 = True

    @app.patch("/api/admin/pairing-codes/{pairing_id}/name")
    async def rename_pairing_code(pairing_id: str, body: PairingNameUpdate, request: Request) -> dict[str, Any]:
        if not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="Administrator login required")
        clean = str(body.name or "").strip()
        if not clean:
            raise HTTPException(status_code=422, detail="设备名称不能为空")
        await pairings.ensure_loaded()
        async with pairings.lock:
            item = pairings.items.get(pairing_id)
            if not item:
                raise HTTPException(status_code=404, detail="Unknown pairing code")
            item.name = clean[:120]
            await pairings.save()
            payload = item.public()
        return {"pairing": payload, "device_name": payload.get("name"), "revision": PATCH_REVISION}

    return app
