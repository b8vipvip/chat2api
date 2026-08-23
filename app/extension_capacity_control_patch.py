from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


MIN_TARGET = 1
MAX_TARGET = 32
CONTROL_RESULT_KEY = "extension_control_result"


class CapacityApplyRequest(BaseModel):
    target: int = Field(ge=MIN_TARGET, le=MAX_TARGET)


def _configured_limit(app: FastAPI, client_id: str) -> int:
    runtime = getattr(app.state, "concurrency_config", {})
    limit_for = runtime.get("limit_for") if isinstance(runtime, dict) else None
    if callable(limit_for):
        return max(MIN_TARGET, min(MAX_TARGET, int(limit_for(client_id))))
    broker = getattr(app.state, "broker", None)
    fallback = int(
        (runtime.get("max_concurrency") if isinstance(runtime, dict) else 0)
        or getattr(broker, "max_concurrency", 0)
        or MIN_TARGET
    )
    return max(MIN_TARGET, min(MAX_TARGET, fallback))


def _ensure_client(app: FastAPI, client_id: str) -> str:
    client_id = str(client_id or "").strip()
    registry = app.state.registry
    if not client_id or client_id not in registry.clients:
        raise HTTPException(status_code=404, detail="Unknown extension ID")
    return client_id


async def _request_extension_control(
    app: FastAPI,
    client_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    registry = app.state.registry
    item = registry.clients.get(client_id)
    if not item or not item.connection_enabled:
        return {
            "ok": False,
            "action": action,
            "error": "Chrome extension connection is disabled",
            "data": {},
        }
    if client_id not in registry.sockets:
        return {
            "ok": False,
            "action": action,
            "error": "Chrome extension is offline",
            "data": {},
        }

    control_id = "ctl_" + uuid.uuid4().hex
    sent_at = time.time()
    try:
        await registry.send(
            client_id,
            {
                "type": "extension.control",
                "control_id": control_id,
                "action": str(action),
                "payload": dict(payload or {}),
                "sent_at": sent_at,
            },
        )
    except Exception as error:
        return {
            "ok": False,
            "control_id": control_id,
            "action": action,
            "error": str(error),
            "data": {},
        }

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout_seconds))
    while loop.time() < deadline:
        current = registry.clients.get(client_id)
        metadata = current.metadata if current and isinstance(current.metadata, dict) else {}
        result = metadata.get(CONTROL_RESULT_KEY)
        if isinstance(result, dict) and str(result.get("control_id") or "") == control_id:
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            return {
                "ok": result.get("ok") is True,
                "control_id": control_id,
                "action": action,
                "error": str(result.get("error") or ""),
                "data": dict(data),
                "observed_at": result.get("observed_at"),
                "round_trip_ms": round((time.time() - sent_at) * 1000, 1),
            }
        await asyncio.sleep(0.1)

    return {
        "ok": False,
        "control_id": control_id,
        "action": action,
        "error": f"Timed out after {round(timeout_seconds, 1)}s waiting for the Extension control confirmation",
        "data": {},
    }


def install_extension_capacity_control_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "extension_capacity_control_patch_installed", False):
        return app
    app.state.extension_capacity_control_patch_installed = True

    @app.post("/api/admin/extensions/{client_id}/capacity/apply")
    async def apply_extension_capacity(client_id: str, body: CapacityApplyRequest) -> dict[str, Any]:
        client_id = _ensure_client(app, client_id)
        configured = _configured_limit(app, client_id)
        if int(body.target) != configured:
            raise HTTPException(
                status_code=409,
                detail=f"Concurrency configuration changed before apply (configured={configured}, requested={body.target})",
            )

        control = await _request_extension_control(
            app,
            client_id,
            "workers.resize",
            {"target": configured},
            timeout_seconds=70.0,
        )
        data = control.get("data") if isinstance(control.get("data"), dict) else {}
        snapshot = data.get("window_snapshot") if isinstance(data.get("window_snapshot"), dict) else {}
        return {
            "ok": control.get("ok") is True,
            "saved": True,
            "applied": control.get("ok") is True,
            "target_reached": data.get("target_reached") is True,
            "client_id": client_id,
            "configured_limit": configured,
            "window_snapshot": snapshot,
            "pending_reason": str(data.get("pending_reason") or ""),
            "error": str(control.get("error") or ""),
            "control": control,
        }

    @app.post("/api/admin/extensions/{client_id}/windows/refresh")
    async def refresh_extension_windows(client_id: str) -> dict[str, Any]:
        client_id = _ensure_client(app, client_id)
        control = await _request_extension_control(
            app,
            client_id,
            "windows.snapshot",
            {},
            timeout_seconds=15.0,
        )
        data = control.get("data") if isinstance(control.get("data"), dict) else {}
        snapshot = data.get("window_snapshot") if isinstance(data.get("window_snapshot"), dict) else {}
        return {
            "ok": control.get("ok") is True,
            "client_id": client_id,
            "window_snapshot": snapshot,
            "error": str(control.get("error") or ""),
            "control": control,
        }

    return app
