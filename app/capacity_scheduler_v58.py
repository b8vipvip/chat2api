from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from .broker import RequestState


PATCH_ID = "server-single-authority-scheduler-v58"
CONFIG_FILENAME = "capacity_v57.json"  # reuse persisted Worker concurrency settings
DEFAULT_WORKER_CONCURRENCY = 3
MIN_LIMIT = 1
MAX_LIMIT = 32
RATE_LIMIT_DEFAULT_SECONDS = 300
RATE_LIMIT_PATTERNS = (
    "chatgpt is temporarily rate limited",
    "too many requests",
    "requests too quickly",
    "requests are too frequent",
    "temporarily limited access",
    "请求过于频繁",
    "请稍等几分钟",
)


def _limit(value: Any, default: int = DEFAULT_WORKER_CONCURRENCY) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(MIN_LIMIT, min(MAX_LIMIT, parsed))


def _load_worker_limits(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "default_worker_concurrency": DEFAULT_WORKER_CONCURRENCY,
        "workers": {},
    }
    if not path.exists():
        return result
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    if not isinstance(raw, dict):
        return result
    result["default_worker_concurrency"] = _limit(
        raw.get("default_worker_concurrency"), DEFAULT_WORKER_CONCURRENCY
    )
    workers: dict[str, dict[str, int]] = {}
    for client_id, row in (raw.get("workers") or {}).items():
        if not isinstance(row, dict):
            continue
        workers[str(client_id)] = {
            "max_concurrency": _limit(
                row.get("max_concurrency"), result["default_worker_concurrency"]
            )
        }
    result["workers"] = workers
    return result


def _save_worker_limits(path: Path, config: dict[str, Any]) -> None:
    """Persist only the surviving server scheduling knob.

    The historical file also held reserve-window and per-key concurrency values.
    They are intentionally retired: per-key concurrency is fixed at one and
    speculative reserve windows are disabled by the single-authority runtime.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "version": 58,
        "scheduler_authority": PATCH_ID,
        "default_worker_concurrency": int(config["default_worker_concurrency"]),
        "default_reserve_windows": 0,
        "default_key_concurrency": 1,
        "workers": {
            str(client_id): {
                "max_concurrency": int(row["max_concurrency"]),
                "reserve_windows": 0,
            }
            for client_id, row in sorted(config["workers"].items())
        },
        "keys": {},
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install_capacity_scheduler_v58(app: FastAPI) -> FastAPI:
    """Install the one server-side request-admission authority.

    The invariant is deliberately simple:

    * one logical API key has exactly one active request;
    * excess requests for that key wait in FIFO order on the server;
    * different API keys may run concurrently up to the Worker's configured
      total concurrency;
    * Chrome receives only already-admitted requests and therefore does not own
      a second FIFO or speculative per-key worker allocator.
    """
    if getattr(app.state, "capacity_scheduler_v58_installed", False):
        return app

    broker = app.state.broker
    registry = app.state.registry
    settings = app.state.settings
    config_path = Path(settings.data_dir) / CONFIG_FILENAME
    config = _load_worker_limits(config_path)

    broker.client_active_requests = getattr(broker, "client_active_requests", {})
    condition = getattr(broker, "_chat2api_v21_condition", None)
    if condition is None:
        condition = asyncio.Condition()
        broker._chat2api_v21_condition = condition

    worker_queues: dict[str, deque[str]] = defaultdict(deque)
    key_queues: dict[str, deque[str]] = defaultdict(deque)
    queued_keys: dict[str, str] = {}
    key_active: dict[str, int] = defaultdict(int)
    request_keys: dict[str, str] = {}
    cooldown_until: dict[str, float] = {}

    base_release = broker.release
    base_publish = broker.publish
    base_send = registry.send
    base_summaries = registry.summaries

    def worker_limit(client_id: str) -> int:
        row = config["workers"].get(str(client_id)) or {}
        return _limit(row.get("max_concurrency"), config["default_worker_concurrency"])

    def logical_key() -> str:
        return str(registry.routing_key_context.get() or "master")

    def used_units(client_id: str) -> int:
        active = broker.client_active_requests.get(str(client_id), {})
        return len(active) if isinstance(active, dict) else 0

    def queue_depth(client_id: str) -> int:
        return len(worker_queues.get(str(client_id), ()))

    def rate_limit_remaining(client_id: str) -> float:
        return max(0.0, float(cooldown_until.get(str(client_id), 0.0)) - time.time())

    def first_eligible(client_id: str) -> str | None:
        """Oldest queued request whose key has no active predecessor."""
        for request_id in worker_queues.get(str(client_id), ()):
            key_id = queued_keys.get(request_id, "master")
            key_queue = key_queues.get(key_id)
            if not key_queue or key_queue[0] != request_id:
                continue
            if int(key_active.get(key_id, 0)) != 0:
                continue
            return request_id
        return None

    def capacity_snapshot(client_id: str) -> dict[str, Any]:
        limit = worker_limit(client_id)
        used = used_units(client_id)
        remaining = rate_limit_remaining(client_id)
        return {
            "limit_units": limit,
            "configured_limit_units": limit,
            "used_units": used,
            "available_units": max(0, limit - used),
            "active_requests": used,
            "request_weights": dict(broker.client_active_requests.get(str(client_id), {}) or {}),
            "queued_requests": queue_depth(client_id),
            "limit_source": "server-distinct-api-capacity-v58",
            "account_generation_limit": limit,
            "account_generation_queue": True,
            "account_generation_queue_wait_seconds": None,
            "reserve_window_target": 0,
            "speculative_window_pool": False,
            "per_api_key_limit": 1,
            "scheduler_authority": PATCH_ID,
            "rate_limit_cooldown_active": remaining > 0,
            "rate_limit_cooldown_remaining_seconds": round(remaining, 1),
        }

    async def create(request_id: str, client_id: str):
        request_id = str(request_id)
        client_id = str(client_id)
        key_id = logical_key()
        started = time.perf_counter()
        worker_queue = worker_queues[client_id]
        key_queue = key_queues[key_id]
        worker_queue.append(request_id)
        key_queue.append(request_id)
        queued_keys[request_id] = key_id

        try:
            async with condition:
                while True:
                    if client_id not in registry.online_client_ids():
                        raise RuntimeError(
                            "selected extension went offline while waiting in the canonical API queue"
                        )
                    remaining = rate_limit_remaining(client_id)
                    worker_ready = (
                        used_units(client_id) < worker_limit(client_id)
                        and first_eligible(client_id) == request_id
                    )
                    key_ready = (
                        bool(key_queue and key_queue[0] == request_id)
                        and int(key_active.get(key_id, 0)) == 0
                    )
                    if worker_ready and key_ready and remaining <= 0:
                        break
                    if remaining > 0:
                        try:
                            await asyncio.wait_for(condition.wait(), timeout=max(0.1, min(remaining, 30.0)))
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await condition.wait()

                if request_id in broker.requests:
                    raise RuntimeError(f"Duplicate request_id: {request_id}")
                try:
                    worker_queue.remove(request_id)
                except ValueError:
                    pass
                if key_queue and key_queue[0] == request_id:
                    key_queue.popleft()
                else:
                    try:
                        key_queue.remove(request_id)
                    except ValueError:
                        pass
                queued_keys.pop(request_id, None)

                loop = asyncio.get_running_loop()
                state = RequestState(request_id=request_id, client_id=client_id, final_future=loop.create_future())
                before = used_units(client_id)
                broker.requests[request_id] = state
                broker.client_active_requests.setdefault(client_id, {})[request_id] = 1
                broker.client_requests.setdefault(client_id, request_id)
                key_active[key_id] = 1
                request_keys[request_id] = key_id
                state.diagnostics.update({
                    "scheduler_authority": PATCH_ID,
                    "server_api_fifo_revision": 58,
                    "capacity_queue_mode": "server-per-api-fifo-v58",
                    "capacity_queue_scheduler": "oldest-eligible-distinct-key-v58",
                    "extension_capacity_limit_units": worker_limit(client_id),
                    "extension_capacity_configured_units": worker_limit(client_id),
                    "extension_capacity_weight": 1,
                    "extension_capacity_used_before": before,
                    "extension_capacity_used_after": before + 1,
                    "extension_capacity_wait_ms": round((time.perf_counter() - started) * 1000, 1),
                    "api_key_capacity_id": key_id,
                    "api_key_capacity_limit": 1,
                    "api_key_capacity_used_after": 1,
                    "same_api_parallel_requests": False,
                    "browser_side_api_fifo": False,
                    "reserve_window_target": 0,
                    "speculative_window_pool": False,
                })
                condition.notify_all()
        except BaseException:
            try:
                worker_queue.remove(request_id)
            except ValueError:
                pass
            try:
                key_queue.remove(request_id)
            except ValueError:
                pass
            queued_keys.pop(request_id, None)
            async with condition:
                condition.notify_all()
            raise

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

    async def release(request_id: str) -> None:
        key_id = request_keys.pop(str(request_id), None)
        await base_release(request_id)
        async with condition:
            if key_id:
                key_active[key_id] = 0
            condition.notify_all()

    def rate_limit_seconds(message: str) -> int:
        match = re.search(r"paused for\s+(\d+)s", message, re.IGNORECASE)
        if match:
            return max(1, min(1800, int(match.group(1))))
        return RATE_LIMIT_DEFAULT_SECONDS

    async def publish(request_id: str, event: dict[str, Any]) -> bool:
        if str(event.get("type") or "") in {"chat.error", "chat.cancelled"}:
            message = str(event.get("error") or event.get("reason") or "")
            lowered = message.lower()
            if any(token in lowered for token in RATE_LIMIT_PATTERNS):
                state = broker.requests.get(str(request_id))
                if state:
                    cooldown_until[state.client_id] = max(
                        float(cooldown_until.get(state.client_id, 0.0)),
                        time.time() + rate_limit_seconds(message),
                    )
                    async with condition:
                        condition.notify_all()
        return await base_publish(request_id, event)

    async def send(client_id: str, message: dict[str, Any]) -> None:
        if isinstance(message, dict) and str(message.get("type") or "") in {
            "chat.request", "image.request", "voice.request", "voice.live.start", "voice.live.request",
        }:
            message = dict(message)
            routing = dict(message.get("routing") or {})
            key_id = str(routing.get("logical_api_key_id") or routing.get("api_key_id") or logical_key())
            routing.update({
                "logical_api_key_id": key_id,
                "api_key_id": key_id,
                "scheduler_authority": PATCH_ID,
                "server_api_fifo_revision": 58,
                "worker_index": 1,
                "worker_limit": 1,
                "max_concurrency": 1,
                "reserve_window_target": 0,
                "strict_api_fifo": True,
                "browser_side_api_fifo": False,
            })
            message["routing"] = routing
        await base_send(client_id, message)

    def summaries() -> list[dict[str, Any]]:
        rows = base_summaries()
        for row in rows:
            client_id = str(row.get("client_id") or "")
            snapshot = capacity_snapshot(client_id)
            row["busy"] = snapshot["used_units"] >= snapshot["limit_units"]
            row["capacity"] = snapshot
            row["max_concurrency"] = snapshot["limit_units"]
            row["configured_max_concurrency"] = snapshot["limit_units"]
            row["concurrency_limit_source"] = snapshot["limit_source"]
            row["account_generation_limit"] = snapshot["limit_units"]
            row["worker_window_settings"] = {
                "max_concurrency": snapshot["limit_units"],
                "reserve_windows": 0,
            }
        return rows

    @app.get("/api/admin/capacity-v57")
    async def capacity_admin_state() -> dict[str, Any]:
        return {
            "version": 58,
            "scheduler_authority": PATCH_ID,
            "defaults": {
                "worker_max_concurrency": int(config["default_worker_concurrency"]),
                "reserve_windows": 0,
                "api_key_max_concurrency": 1,
            },
            "workers": {
                str(client_id): {
                    "max_concurrency": worker_limit(client_id),
                    "reserve_windows": 0,
                    "active": used_units(client_id),
                    "queued": queue_depth(client_id),
                    "rate_limit_cooldown": capacity_snapshot(client_id)["rate_limit_cooldown_active"],
                    "rate_limit_remaining_seconds": capacity_snapshot(client_id)["rate_limit_cooldown_remaining_seconds"],
                }
                for client_id in registry.clients
            },
            "keys": {key_id: 1 for key_id in set(key_queues) | set(key_active)},
            "key_active": dict(key_active),
            "speculative_window_pool": False,
        }

    @app.put("/api/admin/extensions/{client_id}/capacity-v57")
    async def update_worker_capacity(client_id: str, body: dict[str, Any]) -> dict[str, Any]:
        client_id = str(client_id or "").strip()
        if client_id not in registry.clients:
            raise HTTPException(status_code=404, detail="Unknown client_id")
        maximum = _limit(body.get("max_concurrency"), config["default_worker_concurrency"])
        config["workers"][client_id] = {"max_concurrency": maximum}
        _save_worker_limits(config_path, config)
        async with condition:
            condition.notify_all()
        return {
            "ok": True,
            "client_id": client_id,
            "max_concurrency": maximum,
            "reserve_windows": 0,
            "scheduler_authority": PATCH_ID,
        }

    @app.put("/api/admin/keys/{key_id}/concurrency-v57")
    async def update_key_capacity(key_id: str, _body: dict[str, Any]) -> dict[str, Any]:
        key_id = str(key_id or "").strip()
        if not key_id:
            raise HTTPException(status_code=400, detail="key_id is required")
        return {
            "ok": True,
            "key_id": key_id,
            "max_concurrency": 1,
            "active": int(key_active.get(key_id, 0)),
            "locked": True,
            "reason": "single logical API FIFO is authoritative in v58",
        }

    broker.client_used_units = used_units
    broker.can_accept = lambda client_id, weight=1: used_units(client_id) + max(1, int(weight or 1)) <= worker_limit(client_id)
    broker.capacity_snapshot = capacity_snapshot
    broker.create = create
    broker.release = release
    broker.publish = publish
    broker.account_generation_limit_for = worker_limit
    broker.account_generation_configured_limit_for = worker_limit
    broker._chat2api_capacity_scheduler_v58 = True
    registry.send = send
    registry.summaries = summaries
    registry._chat2api_capacity_scheduler_v58 = True

    # Explicitly mark older admission owners as retired. Their functions are not
    # installed after this point and no browser-side same-key queue is required.
    app.state.capacity_queue_v57_installed = False
    app.state.capacity_scheduler_v58_installed = True
    app.state.capacity_scheduler_v58_id = PATCH_ID
    app.state.capacity_scheduler_v58_runtime = {
        "worker_queues": worker_queues,
        "key_queues": key_queues,
        "key_active": key_active,
        "request_keys": request_keys,
        "cooldown_until": cooldown_until,
    }
    return app
