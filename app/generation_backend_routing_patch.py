from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI

from . import linux_worker_patch as worker_control
from . import model_capability_routing_patch as model_routing
from . import v13_patch


PATCH_ID = "generation-backend-routing-v1"
HEALTH_MAX_AGE_SECONDS = 300


def _health(worker: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(worker, dict):
        return None
    metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
    value = metadata.get("generation_backend_health") if isinstance(metadata.get("generation_backend_health"), dict) else None
    if not isinstance(value, dict) or not isinstance(value.get("ready"), bool):
        return None
    try:
        checked = int(value.get("checked_at_epoch") or 0)
    except (TypeError, ValueError):
        checked = 0
    if not checked:
        return None
    age = max(0, int(time.time()) - checked)
    return {
        "ready": bool(value.get("ready")),
        "checked_at_epoch": checked,
        "age_seconds": age,
        "fresh": age <= HEALTH_MAX_AGE_SECONDS,
        "source": str(value.get("source") or "")[:120],
    }


def install_generation_backend_routing_patch(app: FastAPI) -> FastAPI:
    """Keep explicitly broken Linux Worker generation transports out of routing.

    Browser login/landing-page probes can stay green while ChatGPT's generation
    backend TLS/WebSocket path is broken. The Linux Worker watchdog records that
    separate state and the Agent merges it into heartbeat metadata. Unknown or
    stale state is deliberately non-blocking for backward compatibility; only a
    fresh explicit failure is fail-closed.

    model_capability_routing and account_generation_admission both consult the
    module-level ``_compatible`` function at request time. Install one generic
    wrapper that asks the concrete registry for an app-local health resolver so
    the busy-Free queue fallback cannot accidentally bypass this final guard.
    """

    if getattr(app.state, "generation_backend_routing_patch_installed", False):
        return app

    worker_control.PROXY_ERROR_LABELS.update({
        "generation_probe_missing": "Worker 生成后端探测器缺失",
        "generation_probe_missing_rollback_failed": "Worker 生成后端探测器缺失且代理回滚失败",
        "generation_probe_command_failed": "Worker 无法执行生成后端探测",
        "generation_backend_probe_timeout": "ChatGPT 生成后端代理探测超时",
        "generation_backend_connectivity_test_failed": "代理可打开 ChatGPT，但生成后端连接失败",
        "generation_backend_connectivity_test_failed_rollback_failed": "ChatGPT 生成后端连接失败且代理回滚失败",
    })

    registry = app.state.registry
    store = getattr(app.state, "linux_workers", None)

    def generation_backend_routable(client_id: str) -> bool:
        if store is None or not hasattr(store, "worker_for_extension"):
            return True
        try:
            worker = store.worker_for_extension(str(client_id))
        except Exception:
            return True
        state = _health(worker)
        if not state or state.get("fresh") is not True:
            return True
        return state.get("ready") is not False

    registry.generation_backend_routable = generation_backend_routable

    current_compatible = model_routing._compatible
    if not getattr(current_compatible, "__chat2api_generation_backend_guard_v1__", False):
        base_compatible = current_compatible

        def compatible_with_generation_backend(registry_obj: Any, client_id: str, model: str) -> bool:
            if not base_compatible(registry_obj, client_id, model):
                return False
            checker = getattr(registry_obj, "generation_backend_routable", None)
            if callable(checker):
                try:
                    return bool(checker(client_id))
                except Exception:
                    return True
            return True

        compatible_with_generation_backend.__chat2api_generation_backend_guard_v1__ = True
        compatible_with_generation_backend.__chat2api_generation_backend_base__ = base_compatible
        model_routing._compatible = compatible_with_generation_backend
    else:
        base_compatible = getattr(
            current_compatible,
            "__chat2api_generation_backend_base__",
            current_compatible,
        )

    base_resolve_client = registry.resolve_client

    def resolve_client_with_generation_health(requested: str | None) -> str:
        try:
            return base_resolve_client(requested)
        except (ConnectionError, LookupError) as original:
            target = model_routing._MODEL_CONTEXT.get() or v13_patch._target_context.get() or {}
            model = str(target.get("model") or "").strip().lower()
            if model not in model_routing.PAID_TEXT_MODELS | {model_routing.MINI_MODEL}:
                raise
            unhealthy: list[str] = []
            for client_id in registry.online_client_ids():
                try:
                    model_ok = bool(base_compatible(registry, client_id, model))
                except Exception:
                    model_ok = False
                if model_ok and not generation_backend_routable(client_id):
                    unhealthy.append(str(client_id))
            if unhealthy:
                names: list[str] = []
                for client_id in unhealthy[:5]:
                    worker = store.worker_for_extension(client_id) if store is not None else None
                    names.append(str((worker or {}).get("name") or client_id))
                raise ConnectionError(
                    "Compatible Linux Worker generation backend proxy is unhealthy; "
                    "ChatGPT landing/login may still be reachable but generation transport is not. "
                    f"Workers: {', '.join(names)}"
                ) from original
            raise

    registry.resolve_client = resolve_client_with_generation_health
    app.state.generation_backend_routing_patch_installed = True
    app.state.generation_backend_routing_patch_id = PATCH_ID
    return app
