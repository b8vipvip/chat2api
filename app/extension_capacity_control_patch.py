from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


MIN_TARGET = 1
MAX_TARGET = 32
CONTROL_RESULT_KEY = "extension_control_result"
MIN_CONTROL_VERSION = 36
logger = logging.getLogger("chat2api.capacity")


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


def _control_diagnostics(item: Any) -> dict[str, Any]:
    metadata = item.metadata if item and isinstance(getattr(item, "metadata", None), dict) else {}
    bridge_version = str(metadata.get("extension_version") or getattr(item, "version", "") or "unknown")
    try:
        control_version = int(metadata.get("extension_control_version") or 0)
    except (TypeError, ValueError):
        control_version = 0
    control_ready = metadata.get("extension_control_ready")
    return {
        "bridge_version": bridge_version,
        "control_version": control_version,
        "control_ready": control_ready if isinstance(control_ready, bool) else None,
        "control_transport": str(metadata.get("extension_control_transport") or ""),
        "control_last_error": str(metadata.get("extension_control_last_error") or ""),
        "capability_reporter": metadata.get("extension_control_capability_reporter"),
        "capability_reported_at": metadata.get("extension_control_capability_reported_at"),
    }


def _control_protocol_ready(item: Any) -> tuple[bool, dict[str, Any]]:
    diagnostics = _control_diagnostics(item)
    return (
        int(diagnostics["control_version"] or 0) >= MIN_CONTROL_VERSION
        and diagnostics["control_ready"] is True,
        diagnostics,
    )


def _stale_control_error(diagnostics: dict[str, Any]) -> str:
    bridge = diagnostics.get("bridge_version") or "unknown"
    control = diagnostics.get("control_version") or 0
    detail = diagnostics.get("control_last_error") or ""
    suffix = f" ({detail})" if detail else ""
    return (
        f"Chrome Bridge capacity control is not ready: extension={bridge}, control=v{control}; "
        f"required control=v{MIN_CONTROL_VERSION}. Update/reload this Linux Worker Bridge bundle; "
        "the saved concurrency setting is unchanged."
        f"{suffix}"
    )


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
        logger.warning(
            "Capacity control rejected because extension connection is disabled client=%s action=%s",
            client_id,
            action,
            extra={"client_id": client_id, "action": action},
        )
        return {
            "ok": False,
            "action": action,
            "error": "Chrome extension connection is disabled",
            "error_code": "extension_disabled",
            "data": {},
        }
    if client_id not in registry.sockets:
        logger.warning(
            "Capacity control rejected because extension is offline client=%s action=%s",
            client_id,
            action,
            extra={"client_id": client_id, "action": action},
        )
        return {
            "ok": False,
            "action": action,
            "error": "Chrome extension is offline",
            "error_code": "extension_offline",
            "data": {},
        }

    capability, capability_diagnostics = _control_protocol_ready(item)
    if not capability:
        logger.warning(
            "Capacity control capability not ready client=%s action=%s diagnostics=%s",
            client_id,
            action,
            capability_diagnostics,
            extra={"client_id": client_id, "action": action},
        )
        return {
            "ok": False,
            "action": action,
            "error": _stale_control_error(capability_diagnostics),
            "error_code": "extension_control_not_ready",
            "data": {},
            "diagnostics": capability_diagnostics,
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
                "minimum_control_version": MIN_CONTROL_VERSION,
            },
        )
        logger.info(
            "Capacity control sent client=%s action=%s control_id=%s payload=%s diagnostics=%s",
            client_id,
            action,
            control_id,
            dict(payload or {}),
            capability_diagnostics,
            extra={"client_id": client_id, "action": action, "control_id": control_id},
        )
    except Exception as error:
        logger.exception(
            "Capacity control send failed client=%s action=%s control_id=%s",
            client_id,
            action,
            control_id,
            extra={"client_id": client_id, "action": action, "control_id": control_id},
        )
        return {
            "ok": False,
            "control_id": control_id,
            "action": action,
            "error": str(error),
            "error_code": "extension_control_send_failed",
            "data": {},
            "diagnostics": capability_diagnostics,
        }

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout_seconds))
    while loop.time() < deadline:
        current = registry.clients.get(client_id)
        metadata = current.metadata if current and isinstance(current.metadata, dict) else {}
        result = metadata.get(CONTROL_RESULT_KEY)
        if isinstance(result, dict) and str(result.get("control_id") or "") == control_id:
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            diagnostics = _control_diagnostics(current)
            round_trip_ms = round((time.time() - sent_at) * 1000, 1)
            log = logger.info if result.get("ok") is True else logger.warning
            log(
                "Capacity control confirmation client=%s action=%s control_id=%s ok=%s round_trip_ms=%s data=%s error=%s diagnostics=%s",
                client_id,
                action,
                control_id,
                result.get("ok") is True,
                round_trip_ms,
                data,
                str(result.get("error") or ""),
                diagnostics,
                extra={"client_id": client_id, "action": action, "control_id": control_id},
            )
            return {
                "ok": result.get("ok") is True,
                "control_id": control_id,
                "action": action,
                "error": str(result.get("error") or ""),
                "error_code": "" if result.get("ok") is True else "extension_control_failed",
                "data": dict(data),
                "observed_at": result.get("observed_at"),
                "round_trip_ms": round_trip_ms,
                "diagnostics": diagnostics,
            }
        await asyncio.sleep(0.1)

    current = registry.clients.get(client_id)
    diagnostics = _control_diagnostics(current)
    logger.error(
        "Capacity control confirmation timeout client=%s action=%s control_id=%s timeout_seconds=%s diagnostics=%s",
        client_id,
        action,
        control_id,
        timeout_seconds,
        diagnostics,
        extra={"client_id": client_id, "action": action, "control_id": control_id},
    )
    return {
        "ok": False,
        "control_id": control_id,
        "action": action,
        "error": (
            f"Timed out after {round(timeout_seconds, 1)}s waiting for the Extension control confirmation "
            f"(extension={diagnostics['bridge_version']}, control=v{diagnostics['control_version']})."
        ),
        "error_code": "extension_control_timeout",
        "data": {},
        "diagnostics": diagnostics,
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
            logger.warning(
                "Capacity apply configuration race client=%s configured=%s requested=%s",
                client_id,
                configured,
                body.target,
                extra={"client_id": client_id, "action": "workers.resize"},
            )
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
            "error_code": str(control.get("error_code") or ""),
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
            "error_code": str(control.get("error_code") or ""),
            "control": control,
        }

    return app
