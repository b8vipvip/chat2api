from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel

from . import model_capability_routing_patch as routing
from .model_catalog import normalize_concrete_model_id
from .worker_text_model_management_v146_patch import _model_rows, _response_bytes
from .worker_model_library_v145_patch import STATIC_SPECIAL_MODELS, _validated_model_rows
from .model_capability_evidence_v184 import project_capabilities

PATCH_ID = "manual-worker-text-model-tasks-v147"
ASSET = "/assets/chat2api-text-model-tasks-v147.js"
TASK_TIMEOUT_S = 150
SPECIAL = {"gpt-image", "gpt-live", "gpt-live-mini"}
TERMINAL = {"completed", "failed", "offline", "timed_out"}
MAX_TASKS = 100


class ModelTaskError(Exception):
    pass


def _observed(metadata: Any) -> list[dict[str, Any]]:
    # A reconnect invalidates the old picker observations. The v145 discovery
    # state is the single freshness authority; v147 must not revive stale
    # candidates through its compatibility catalog or supports_model wrapper.
    meta = metadata if isinstance(metadata, dict) else {}
    try:
        revision = int(meta.get("model_validation_revision") or 0)
    except (TypeError, ValueError):
        return []
    if revision < 145 or meta.get("model_validation_state") not in {"discovered", "validated"}:
        return []
    return _model_rows(meta.get("model_observed_choices"))


def _log_text(task: dict[str, Any], worker_id: str | None = None) -> str:
    lines = [
        f"chat2api Worker model task: {task['id']}",
        f"kind={task['kind']} model={task.get('model') or '-'} created_at={task['created_at']}",
    ]
    for client_id, worker in task["workers"].items():
        if worker_id and worker_id != client_id:
            continue
        lines.append(f"\n[{client_id}] state={worker['state']} progress={worker['progress']}%")
        lines.extend(str(line) for line in worker.get("logs") or [])
        if worker.get("error"):
            lines.append(f"ERROR: {worker['error']}")
    return "\n".join(lines) + "\n"


def install_manual_worker_text_model_tasks_v147_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "manual_worker_text_model_tasks_v147_installed", False):
        return app

    registry = app.state.registry
    snapshot = app.state.worker_text_model_management_v146_snapshot
    path = Path(app.state.settings.data_dir) / "worker_text_model_tasks_v147.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        tasks: list[dict[str, Any]] = list(saved.get("tasks") or [])[-MAX_TASKS:]
    except (OSError, ValueError, TypeError, AttributeError):
        tasks = []
    lock = asyncio.Lock()
    base_touch = registry.touch
    base_supports = registry.supports_model
    base_catalog = registry.model_catalog
    base_client_models = registry.client_models

    async def save() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            tmp.write_text(json.dumps({"tasks": tasks[-MAX_TASKS:]}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def task_by_id(task_id: str) -> dict[str, Any] | None:
        return next((item for item in tasks if item["id"] == task_id), None)

    def expire_tasks() -> None:
        changed = False
        now = time.time()
        for task in tasks:
            if now - task.get("started_epoch", now) <= TASK_TIMEOUT_S:
                continue
            for worker in task["workers"].values():
                if worker["state"] not in TERMINAL:
                    worker.update(state="timed_out", progress=100, error="Worker did not finish the task within 150 seconds")
                    worker["logs"].append("Server timeout: no terminal Worker task report received")
                    changed = True
            if changed:
                task["updated_at"] = datetime.now(timezone.utc).isoformat()
        return changed

    async def touch_tasks(client_id: str, metadata: dict[str, Any] | None = None) -> None:
        await base_touch(client_id, metadata)
        if not isinstance(metadata, dict):
            return
        report = metadata.get("model_task_report_v147")
        if not isinstance(report, dict):
            return
        task_id = str(report.get("task_id") or "")
        async with lock:
            task = task_by_id(task_id)
            if not task or client_id not in task["workers"]:
                return
            worker = task["workers"][client_id]
            if worker["state"] in TERMINAL:
                return
            state = str(report.get("state") or "running")[:24]
            if state not in {"queued", "running", "completed", "failed"}:
                state = "running"
            try:
                progress = min(100, max(0, int(report.get("progress") or 0)))
            except (ValueError, TypeError):
                progress = 0
            # Full Worker-originated task logs are bounded, sanitized for length,
            # and remain scoped to the server-authorized task and Worker.
            worker.update(
                state=state,
                progress=max(worker.get("progress", 0), progress),
                error=str(report.get("error") or "")[:600],
                models=[x for x in (report.get("models") or []) if isinstance(x, str)][:100],
                logs=[str(x)[:350] for x in (report.get("logs") or [])[:80]],
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            task["updated_at"] = worker["updated_at"]
            await save()

    registry.touch = touch_tasks

    def supports_model(client_id: str, requested: str) -> bool:
        model = normalize_concrete_model_id(requested)
        if not model or model in snapshot().get("disabled", []):
            return False
        if base_supports(client_id, requested):
            return True
        if model in SPECIAL:
            return False
        client = registry.clients.get(str(client_id))
        return model in {item["id"] for item in _observed(getattr(client, "metadata", {})) if item["selectable"]}

    def client_models(client_id: str):
        rows = list(base_client_models(client_id))
        ids = {item.get("id") for item in rows}
        client = registry.clients.get(str(client_id))
        for item in _observed(getattr(client, "metadata", {})):
            if item["selectable"] and item["id"] not in ids:
                rows.append({"id": item["id"], "label": item["label"], "validated": False,
                             "source": "worker-picker-observed-unverified", "capabilities": ["text"]})
        return rows

    def model_catalog(online_only: bool = True):
        catalog = {item["id"]: dict(item) for item in base_catalog(online_only=online_only)}
        online = set(registry.online_client_ids())
        disabled = set(snapshot().get("disabled", []))
        for client_id, client in registry.clients.items():
            if online_only and (client_id not in online or not registry.chatgpt_routing_ready(client_id)):
                continue
            for row in _observed(client.metadata):
                model = row["id"]
                if model in disabled or not row["selectable"]:
                    continue
                entry = catalog.setdefault(model, {
                    "id": model, "object": "model", "created": 0, "owned_by": "chat2api",
                    "label": row["label"], "capabilities": ["text"],
                    "clients": [], "validated": False, "source": "worker-picker-observed-unverified",
                })
                if client_id not in entry["clients"]:
                    entry["clients"].append(client_id)
        # v147 may add selectable-but-unverified observed models. Recompute the
        # *same* evidence projection after that existing catalog merge; otherwise
        # the v145-only projection would become stale in the production
        # entrypoint where v147 installs last. This is presentation evidence,
        # not another routing or Worker capability decision.
        candidates = [
            client_id for client_id in registry.clients
            if (not online_only or client_id in online)
            and registry.chatgpt_routing_ready(client_id)
        ]
        validated_by_worker = {
            cid: {str(row["id"]): row for row in _validated_model_rows(registry, cid)}
            for cid in candidates
        }
        for entry in catalog.values():
            entry["capability_evidence"] = project_capabilities(
                registry, entry, validated_by_worker,
                special_model=str(entry.get("id") or "") in STATIC_SPECIAL_MODELS,
            )
        return sorted(catalog.values(), key=lambda item: str(item.get("id") or ""))

    registry.supports_model = supports_model
    registry.client_models = client_models
    registry.model_catalog = model_catalog

    base_compatible = routing._compatible

    # The historical scheduler's closure was bound to strict validation.
    # Use the same single canonical registry capability check instead.
    def compatible(registry_obj: Any, client_id: str, model: str) -> bool:
        supports = getattr(registry_obj, 'supports_model', None)
        if not callable(supports):
            return bool(base_compatible(registry_obj, client_id, model))
        health = getattr(registry_obj, "generation_backend_routable", None)
        return bool(registry_obj.chatgpt_routing_ready(client_id)
                    and supports(client_id, model)
                    and (not callable(health) or health(client_id)))
    routing._compatible = compatible

    overview = next((route for route in app.routes if isinstance(route, APIRoute) and
                     route.path == "/api/admin/overview" and "GET" in (route.methods or set())), None)
    if overview is None:
        raise RuntimeError("Administrator route authentication is required")
    auth = list(overview.dependencies or [])

    async def launch(kind: str, model: str | None = None) -> dict[str, Any]:
        online = list(registry.online_client_ids())
        if kind == "validate":
            online = [cid for cid in online
                      if model in {item["id"] for item in _observed(registry.clients[cid].metadata)}]
        if not online:
            raise HTTPException(409, "No online Worker is eligible for this model task")
        task_id = "mtask_" + uuid.uuid4().hex
        started = datetime.now(timezone.utc).isoformat()
        task: dict[str, Any] = {
            "id": task_id, "kind": kind, "model": model,
            "created_at": started, "updated_at": started, "started_epoch": time.time(),
            "workers": {cid: {
                "state": "queued", "progress": 0, "error": "", "models": [],
                "logs": [f"{started} Server queued {kind} task for {cid}"], "updated_at": started,
            } for cid in online},
        }
        async with lock:
            tasks.append(task)
            del tasks[:-MAX_TASKS]
            await save()
        for cid in online:
            try:
                await registry.send(cid, {"type": "model.task.v147", "task_id": task_id, "action": kind, "model": model})
            except Exception as error:
                worker = task["workers"][cid]
                worker.update(state="failed", progress=100, error=f"Dispatch failed: {str(error)[:300]}")
                worker["logs"].append(worker["error"])
        async with lock:
            await save()
        return task

    @app.post("/api/admin/text-model-tasks/discover", dependencies=auth)
    async def start_discover_task():
        return await launch("discover")

    @app.post("/api/admin/text-models/{model_id}/validate", dependencies=auth)
    async def start_validation_task(model_id: str):
        model = normalize_concrete_model_id(model_id)
        if not model or model in SPECIAL:
            raise HTTPException(400, "Invalid text model")
        return await launch("validate", model)

    @app.get("/api/admin/text-model-tasks", dependencies=auth)
    async def list_model_tasks():
        async with lock:
            if expire_tasks():
                await save()
            return {
                "data": list(reversed(tasks[-MAX_TASKS:])),
                "revision": 147,
                # A read-only projection of the same final registry catalog:
                # the administrator can inspect current capability evidence
                # without a second model list owner or a duplicate API route.
                "capability_evidence": {
                    str(row["id"]): row.get("capability_evidence") or {}
                    for row in registry.model_catalog()
                },
            }

    @app.get("/api/admin/text-model-tasks/{task_id}/logs", dependencies=auth)
    async def model_task_logs(task_id: str, worker: str | None = None):
        task = task_by_id(task_id)
        if not task:
            raise HTTPException(404, "Unknown model task")
        if worker and worker not in task["workers"]:
            raise HTTPException(404, "Worker does not belong to this task")
        return PlainTextResponse(
            _log_text(task, worker), headers={
                "Content-Disposition": f'attachment; filename="{task_id}-worker-model.log"',
                "Cache-Control": "no-store",
            },
        )

    @app.get(ASSET, include_in_schema=False)
    async def model_task_asset():
        return Response(Path(__file__).with_name("admin_text_model_tasks_v147.js").read_text(encoding="utf-8"),
                        media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def task_console(request: Request, call_next):
        response = await call_next(request)
        if request.url.path not in {"/admin", "/developers"} or "text/html" not in response.headers.get("content-type", ""):
            return response
        html = (await _response_bytes(response)).decode("utf-8", errors="replace")
        tag = f'<script src="{ASSET}?v=147"></script>'
        if tag not in html:
            html = html.replace("</body>", tag + "</body>")
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(html, media_type="text/html", status_code=response.status_code, headers=headers)

    app.state.manual_worker_text_model_tasks_v147_installed = True
    return app
