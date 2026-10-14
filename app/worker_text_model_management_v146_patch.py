from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel

from .model_catalog import normalize_concrete_model_id

PATCH_ID = "worker-text-model-management-v146"
ASSET_PATH = "/assets/chat2api-text-model-management-v146.js"
TEXT_SPECIAL_IDS = {"gpt-image", "gpt-live", "gpt-live-mini"}
MODEL_KEYS = {
    "models", "model_observed_choices", "model_validation_state",
    "model_validation_revision", "model_validation_error",
    "model_validation_trigger", "model_validation_updated_at",
    "model_validation_candidate_count", "model_validation_validated_count",
}


class ModelSwitch(BaseModel):
    enabled: bool


def _model_rows(values: Any, *, verified_only: bool = False) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values[:150]:
        if not isinstance(value, dict):
            continue
        model = normalize_concrete_model_id(value.get("id"))
        if not model or model in TEXT_SPECIAL_IDS or model in seen:
            continue
        if verified_only and not (
            value.get("validated") is True
            and int(value.get("validation_revision") or 0) >= 145
        ):
            continue
        seen.add(model)
        rows.append({
            "id": model,
            "label": str(value.get("label") or model)[:140],
            "validated": bool(value.get("validated")) if verified_only else False,
            "method": str(value.get("validation_method") or value.get("source") or "")[:90],
            "selectable": value.get("selectable") is not False,
            "at": str(value.get("validated_at") or value.get("observed_at") or "")[:48],
        })
    return rows


def _worker_snapshot(metadata: Any) -> dict[str, Any]:
    meta = metadata if isinstance(metadata, dict) else {}
    observed = _model_rows(meta.get("model_observed_choices"))
    validated = _model_rows(meta.get("models"), verified_only=True) if meta.get("model_validation_state") == "validated" else []
    # Legacy / Free defaults are diagnostic-only, never verified picker evidence.
    return {
        "validation_state": str(meta.get("model_validation_state") or "unknown")[:48],
        "validation_error": str(meta.get("model_validation_error") or "")[:400],
        "validation_trigger": str(meta.get("model_validation_trigger") or "")[:80],
        "updated_at": str(meta.get("model_validation_updated_at") or "")[:48],
        "account_type": str(meta.get("account_type") or "unknown")[:40],
        "candidates": int(meta.get("model_validation_candidate_count") or 0),
        "verified_count": int(meta.get("model_validation_validated_count") or 0),
        "observed": observed,
        "validated": validated,
    }


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except (OSError, ValueError, TypeError):
        pass
    return {"disabled": [], "history": []}


def _persist(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


async def _response_bytes(response: Response) -> bytes:
    if getattr(response, "body", None) is not None:
        return bytes(response.body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for part in iterator:
            chunks.append(part.encode("utf-8") if isinstance(part, str) else bytes(part))
    return b"".join(chunks)


def install_worker_text_model_management_v146_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "worker_text_model_management_v146_installed", False):
        return app
    registry = app.state.registry
    path = Path(app.state.settings.data_dir) / "worker_text_model_audit_v146.json"
    data = _load(path)
    lock = asyncio.Lock()
    disabled = {x for x in data.get("disabled", []) if normalize_concrete_model_id(x) == x}
    history = list(data.get("history") or [])[-800:]
    base_touch = registry.touch
    base_supports = registry.supports_model
    base_catalog = registry.model_catalog

    async def record(event: dict[str, Any]) -> None:
        async with lock:
            history.append({
                "at": datetime.now(timezone.utc).isoformat(),
                **event,
            })
            del history[:-800]
            _persist(path, {"disabled": sorted(disabled), "history": history})

    async def touch_with_history(client_id: str, metadata: dict[str, Any] | None = None):
        before = _worker_snapshot(getattr(registry.clients.get(client_id), "metadata", {}))
        await base_touch(client_id, metadata)
        if not isinstance(metadata, dict) or not MODEL_KEYS.intersection(metadata):
            return
        after = _worker_snapshot(getattr(registry.clients.get(client_id), "metadata", {}))
        if before != after:
            try:
                await record({
                    "event": "worker-model-report",
                    "client_id": client_id,
                    "previous": before,
                    "current": after,
                })
            except OSError:
                # An unavailable audit volume must not prevent the Worker heartbeat.
                import logging
                logging.getLogger(__name__).exception("Failed to persist Worker model audit")

    def supports_with_management(client_id: str, model_id: str) -> bool:
        normalized = normalize_concrete_model_id(model_id)
        if normalized in disabled:
            return False
        return bool(base_supports(client_id, model_id))

    def catalog_with_management(online_only: bool = True):
        return [
            row for row in base_catalog(online_only=online_only)
            if str(row.get("id") or "") not in disabled
        ]

    registry.touch = touch_with_history
    registry.supports_model = supports_with_management
    registry.model_catalog = catalog_with_management

    def snapshot() -> dict[str, Any]:
        workers: list[dict[str, Any]] = []
        models: dict[str, dict[str, Any]] = {}
        online = set(registry.online_client_ids())
        for client_id, client in sorted(registry.clients.items()):
            state = _worker_snapshot(getattr(client, "metadata", {}))
            ready = client_id in online and registry.chatgpt_routing_ready(client_id)
            worker = {
                "client_id": client_id,
                "name": str(getattr(client, "name", "") or client_id)[:120],
                "online": client_id in online,
                "routing_ready": ready,
                "extension_version": str((getattr(client, "metadata", {}) or {}).get("extension_version") or ""),
                **state,
            }
            workers.append(worker)
            for kind in ("observed", "validated"):
                for item in state[kind]:
                    model_id = item["id"]
                    entry = models.setdefault(model_id, {
                        "id": model_id, "label": item["label"], "enabled": model_id not in disabled,
                        "observed_by": [], "verified_by": [], "routable_by": [],
                    })
                    if client_id not in entry["observed_by"]:
                        entry["observed_by"].append(client_id)
                    if kind == "validated":
                        entry["verified_by"].append(client_id)
                        if ready and model_id not in disabled:
                            entry["routable_by"].append(client_id)
            # Free's hardcoded old default must never create a fake discovered model.
        for entry in models.values():
            entry["state"] = (
                "disabled" if not entry["enabled"]
                else "routable" if entry["routable_by"]
                else "verified-offline" if entry["verified_by"]
                else "observed-unverified"
            )
        return {
            "authority": PATCH_ID, "revision": 146,
            "data": sorted(models.values(), key=lambda item: item["id"]),
            "workers": workers,
            "history": list(reversed(history[-200:])),
            "disabled": sorted(disabled),
        }

    # Reuse the exact administrator dependency (including future auth updates).
    overview = next((
        route for route in app.routes if isinstance(route, APIRoute)
        and route.path == "/api/admin/overview" and "GET" in (route.methods or set())
    ), None)
    if overview is None:
        raise RuntimeError("admin overview route is required for model-management authentication")
    guard = list(overview.dependencies or [])

    @app.get("/api/admin/text-models", dependencies=guard)
    async def admin_text_models():
        return snapshot()

    @app.patch("/api/admin/text-models/{model_id}", dependencies=guard)
    async def admin_toggle_text_model(model_id: str, body: ModelSwitch):
        model = normalize_concrete_model_id(model_id)
        if not model or model in TEXT_SPECIAL_IDS:
            raise HTTPException(400, "Only concrete text models may be managed")
        if body.enabled:
            disabled.discard(model)
        else:
            disabled.add(model)
        await record({"event": "admin-model-toggle", "model_id": model, "enabled": body.enabled})
        return {"model_id": model, "enabled": body.enabled, "authority": PATCH_ID}

    @app.get(ASSET_PATH, include_in_schema=False)
    async def admin_models_asset():
        from pathlib import Path as FilePath
        script = FilePath(__file__).with_name("admin_text_model_management_v146.js")
        return Response(script.read_text(encoding="utf-8"), media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def inject_text_model_admin_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path not in {"/admin", "/developers"} or "text/html" not in response.headers.get("content-type", ""):
            return response
        html = (await _response_bytes(response)).decode("utf-8", errors="replace")
        script = f'<script src="{ASSET_PATH}?v=146"></script>'
        if script not in html:
            html = html.replace("</body>", script + "</body>")
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-type", "content-length"}}
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(html, status_code=response.status_code, media_type="text/html", headers=headers)

    app.state.worker_text_model_management_v146_installed = True
    app.state.worker_text_model_management_v146_snapshot = snapshot
    return app
