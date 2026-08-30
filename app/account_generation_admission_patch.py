from __future__ import annotations

import asyncio
import os
import secrets
import time
from typing import Any

from fastapi import FastAPI

from .broker import RequestState
from . import model_capability_routing_patch as model_routing
from . import mini_multimodal_quota_patch as mini_quota
from . import v13_patch, v21_patch


PATCH_ID = "account-generation-admission-v1"
FREE_ACCOUNT_GENERATION_LIMIT = 1
DEFAULT_FREE_QUEUE_WAIT_SECONDS = 180.0
MIN_FREE_QUEUE_WAIT_SECONDS = 30.0
MAX_FREE_QUEUE_WAIT_SECONDS = 300.0


def _account_type(registry: Any, client_id: str) -> str:
    item = registry.clients.get(str(client_id))
    metadata = getattr(item, "metadata", None) if item else None
    value = str((metadata or {}).get("account_type") or "unknown").strip().lower()
    return value if value in {"free", "paid"} else "unknown"


def _free_queue_wait_seconds() -> float:
    raw = os.getenv("CHAT2API_FREE_ACCOUNT_QUEUE_WAIT_SECONDS", "")
    try:
        value = float(raw) if raw.strip() else DEFAULT_FREE_QUEUE_WAIT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_FREE_QUEUE_WAIT_SECONDS
    return max(MIN_FREE_QUEUE_WAIT_SECONDS, min(MAX_FREE_QUEUE_WAIT_SECONDS, value))


def install_account_generation_admission_patch(app: FastAPI) -> FastAPI:
    """Own final account-wide generation admission after historical concurrency patches.

    Browser-route capacity and ChatGPT-account generation capacity are deliberately
    different concepts. A Free account may keep several warm tabs, but production
    evidence shows that dispatching several simultaneous generations to the same
    Free account causes all of them to enter ChatGPT's non-idle intermediate state.
    Keep the warm pool untouched and serialize real generations at the broker.
    """

    if getattr(app.state, "account_generation_admission_patch_installed", False):
        return app

    broker = app.state.broker
    registry = app.state.registry
    runtime = getattr(app.state, "concurrency_config", {})
    configured_limit_for = runtime.get("limit_for") if isinstance(runtime, dict) else None
    base_resolve_client = registry.resolve_client
    base_summaries = registry.summaries

    def configured_limit(client_id: str) -> int:
        if callable(configured_limit_for):
            try:
                return max(1, int(configured_limit_for(str(client_id))))
            except (TypeError, ValueError):
                pass
        try:
            return max(1, int(getattr(broker, "max_concurrency", 1) or 1))
        except (TypeError, ValueError):
            return 1

    def effective_limit(client_id: str) -> int:
        configured = configured_limit(client_id)
        if _account_type(registry, client_id) == "free":
            return min(configured, FREE_ACCOUNT_GENERATION_LIMIT)
        return configured

    def used_units(client_id: str) -> int:
        active = getattr(broker, "client_active_requests", {}).get(str(client_id), {})
        if not isinstance(active, dict):
            return 0
        return sum(max(1, int(weight or 1)) for weight in active.values())

    def can_accept(client_id: str, weight: int = 1) -> bool:
        weight = max(1, int(weight or 1))
        return used_units(client_id) + weight <= effective_limit(client_id)

    def capacity_snapshot(client_id: str) -> dict[str, Any]:
        client_id = str(client_id)
        active = getattr(broker, "client_active_requests", {}).get(client_id, {})
        if not isinstance(active, dict):
            active = {}
        used = used_units(client_id)
        configured = configured_limit(client_id)
        effective = effective_limit(client_id)
        account = _account_type(registry, client_id)
        source = "account-free-safety" if account == "free" and effective < configured else "configured"
        return {
            "limit_units": effective,
            "configured_limit_units": configured,
            "used_units": used,
            "available_units": max(0, effective - used),
            "active_requests": len(active),
            "request_weights": dict(active),
            "limit_source": source,
            "account_type": account,
            "account_generation_limit": effective,
            "account_generation_queue": account == "free",
            "account_generation_queue_wait_seconds": _free_queue_wait_seconds() if account == "free" else float(v21_patch.CAPACITY_WAIT_SECONDS),
        }

    async def create_account_aware(request_id: str, client_id: str):
        request_id = str(request_id)
        client_id = str(client_id)
        started = time.perf_counter()
        condition = broker._chat2api_v21_condition
        account = _account_type(registry, client_id)
        queue_wait = _free_queue_wait_seconds() if account == "free" else float(v21_patch.CAPACITY_WAIT_SECONDS)
        deadline = asyncio.get_running_loop().time() + queue_wait

        async with condition:
            while not can_accept(client_id, 1):
                if client_id not in registry.online_client_ids():
                    raise RuntimeError("selected extension went offline while waiting for account generation capacity")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    snapshot = capacity_snapshot(client_id)
                    raise RuntimeError(
                        "account generation capacity exhausted "
                        f"(account={snapshot['account_type']}, used={snapshot['used_units']}/{snapshot['limit_units']}, "
                        f"configured={snapshot['configured_limit_units']}, waited={round(queue_wait, 1)}s)"
                    )
                try:
                    await asyncio.wait_for(condition.wait(), timeout=remaining)
                except asyncio.TimeoutError as error:
                    snapshot = capacity_snapshot(client_id)
                    raise RuntimeError(
                        "account generation capacity exhausted "
                        f"(account={snapshot['account_type']}, used={snapshot['used_units']}/{snapshot['limit_units']}, "
                        f"configured={snapshot['configured_limit_units']}, waited={round(queue_wait, 1)}s)"
                    ) from error

            if request_id in broker.requests:
                raise RuntimeError(f"Duplicate request_id: {request_id}")
            loop = asyncio.get_running_loop()
            state = RequestState(request_id=request_id, client_id=client_id, final_future=loop.create_future())
            before = used_units(client_id)
            snapshot = capacity_snapshot(client_id)
            broker.requests[request_id] = state
            broker.client_active_requests.setdefault(client_id, {})[request_id] = 1
            broker.client_requests.setdefault(client_id, request_id)
            state.diagnostics.update({
                "extension_capacity_limit_units": snapshot["limit_units"],
                "extension_capacity_configured_units": snapshot["configured_limit_units"],
                "extension_capacity_weight": 1,
                "extension_capacity_used_before": before,
                "extension_capacity_used_after": before + 1,
                "extension_capacity_wait_ms": round((time.perf_counter() - started) * 1000, 1),
                "extension_concurrency_v21": True,
                "extension_concurrency_per_client": True,
                "account_generation_admission": PATCH_ID,
                "account_generation_account_type": account,
                "account_generation_limit": snapshot["limit_units"],
                "account_generation_configured_limit": snapshot["configured_limit_units"],
                "account_generation_queue_wait_seconds": queue_wait,
            })

        tracked = getattr(broker, "_chat2api_v19_tracked_states", None)
        if isinstance(tracked, dict):
            try:
                from .v19_patch import _http_request_marker
                marker = _http_request_marker.get()
                if marker:
                    tracked[marker] = state
            except Exception:
                pass
        return state

    def busy_compatible_fallback(model: str, needs_multimodal: bool) -> str | None:
        candidates: list[str] = []
        for client_id in registry.online_client_ids():
            # This fallback is only for a genuinely saturated compatible Worker.
            # Do not bypass other routing failures such as disabled, offline,
            # capability, or quota guards when no request is currently occupying it.
            if used_units(client_id) <= 0 or can_accept(client_id, 1):
                continue
            if not model_routing._compatible(registry, client_id, model):
                continue
            if model == model_routing.MINI_MODEL and needs_multimodal:
                if _account_type(registry, client_id) == "free" and not mini_quota._multimodal_available(registry, client_id):
                    continue
            candidates.append(client_id)
        if not candidates:
            return None

        key_id = registry.routing_key_context.get()
        if key_id:
            sticky = registry.api_key_routes.get(key_id)
            if sticky in candidates:
                return sticky

        # Keep gpt-5.5-mini on Free when possible, matching the existing native
        # Free routing policy. Otherwise choose the least-loaded compatible client.
        if model == model_routing.MINI_MODEL:
            free = [client_id for client_id in candidates if _account_type(registry, client_id) == "free"]
            if free:
                candidates = free
        minimum = min(used_units(client_id) for client_id in candidates)
        least_loaded = [client_id for client_id in candidates if used_units(client_id) == minimum]
        selected = secrets.choice(least_loaded)
        registry._remember_route(key_id, selected)
        return selected

    def resolve_client_account_aware(requested: str | None) -> str:
        try:
            return base_resolve_client(requested)
        except (ConnectionError, LookupError) as original:
            if requested:
                raise
            target = model_routing._MODEL_CONTEXT.get() or v13_patch._target_context.get() or {}
            model = str(target.get("model") or "").strip().lower()
            if model not in model_routing.PAID_TEXT_MODELS | {model_routing.MINI_MODEL}:
                raise
            selected = busy_compatible_fallback(model, bool(target.get("needs_multimodal")))
            if not selected:
                raise original
            return selected

    def summaries_account_aware() -> list[dict[str, Any]]:
        rows = base_summaries()
        for row in rows:
            client_id = str(row.get("client_id") or "")
            snapshot = capacity_snapshot(client_id)
            row["busy"] = snapshot["used_units"] >= snapshot["limit_units"]
            row["capacity"] = snapshot
            row["max_concurrency"] = snapshot["limit_units"]
            row["configured_max_concurrency"] = snapshot["configured_limit_units"]
            row["concurrency_limit_source"] = snapshot["limit_source"]
            row["account_generation_limit"] = snapshot["account_generation_limit"]
        return rows

    broker.client_used_units = used_units
    broker.can_accept = can_accept
    broker.capacity_snapshot = capacity_snapshot
    broker.create = create_account_aware
    broker.account_generation_limit_for = effective_limit
    broker.account_generation_configured_limit_for = configured_limit
    broker._chat2api_account_generation_admission_v1 = True
    registry.resolve_client = resolve_client_account_aware
    registry.summaries = summaries_account_aware
    registry._chat2api_account_generation_admission_v1 = True

    app.state.account_generation_admission_patch_installed = True
    app.state.account_generation_admission_patch_id = PATCH_ID
    return app
