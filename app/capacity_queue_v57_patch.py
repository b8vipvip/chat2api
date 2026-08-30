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


PATCH_ID = "worker-key-capacity-queue-v57"
CONFIG_FILENAME = "capacity_v57.json"
DEFAULT_WORKER_CONCURRENCY = 3
DEFAULT_RESERVE_WINDOWS = 3
DEFAULT_KEY_CONCURRENCY = 3
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


def _limit(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(MIN_LIMIT, min(MAX_LIMIT, parsed))


def _load(path: Path) -> dict[str, Any]:
    defaults = {
        "version": 57,
        "default_worker_concurrency": DEFAULT_WORKER_CONCURRENCY,
        "default_reserve_windows": DEFAULT_RESERVE_WINDOWS,
        "default_key_concurrency": DEFAULT_KEY_CONCURRENCY,
        "workers": {},
        "keys": {},
    }
    if not path.exists():
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults

    result = dict(defaults)
    result["default_worker_concurrency"] = _limit(
        raw.get("default_worker_concurrency"), DEFAULT_WORKER_CONCURRENCY
    )
    result["default_reserve_windows"] = _limit(
        raw.get("default_reserve_windows"), DEFAULT_RESERVE_WINDOWS
    )
    result["default_key_concurrency"] = _limit(
        raw.get("default_key_concurrency"), DEFAULT_KEY_CONCURRENCY
    )
    workers: dict[str, dict[str, int]] = {}
    for client_id, row in (raw.get("workers") or {}).items():
        if not isinstance(row, dict):
            continue
        workers[str(client_id)] = {
            "max_concurrency": _limit(
                row.get("max_concurrency"), result["default_worker_concurrency"]
            ),
            "reserve_windows": _limit(
                row.get("reserve_windows"), result["default_reserve_windows"]
            ),
        }
    result["workers"] = workers
    result["keys"] = {
        str(key_id): _limit(value, result["default_key_concurrency"])
        for key_id, value in (raw.get("keys") or {}).items()
        if str(key_id)
    }
    return result


def _save(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "version": 57,
        "default_worker_concurrency": int(config["default_worker_concurrency"]),
        "default_reserve_windows": int(config["default_reserve_windows"]),
        "default_key_concurrency": int(config["default_key_concurrency"]),
        "workers": {
            str(client_id): {
                "max_concurrency": int(row["max_concurrency"]),
                "reserve_windows": int(row["reserve_windows"]),
            }
            for client_id, row in sorted(config["workers"].items())
        },
        "keys": {
            str(key_id): int(value)
            for key_id, value in sorted(config["keys"].items())
        },
    }
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _save_reserve_compat(runtime: dict[str, Any]) -> None:
    path_value = str(runtime.get("config_path") or "").strip()
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    default_limit = _limit(
        runtime.get("default_max_concurrency"), DEFAULT_RESERVE_WINDOWS
    )
    client_limits = (
        runtime.get("client_limits")
        if isinstance(runtime.get("client_limits"), dict)
        else {}
    )
    tmp.write_text(
        json.dumps(
            {
                "version": 2,
                "mode": "per-extension",
                "default_max_concurrency": default_limit,
                "max_concurrency": default_limit,
                "clients": {
                    str(client_id): _limit(value, default_limit)
                    for client_id, value in sorted(client_limits.items())
                },
                "request_weight": 1,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def install_capacity_queue_v57_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "capacity_queue_v57_installed", False):
        return app

    broker = app.state.broker
    registry = app.state.registry
    settings = app.state.settings
    config_path = Path(settings.data_dir) / CONFIG_FILENAME
    config = _load(config_path)
    legacy_runtime = getattr(app.state, "concurrency_config", {})
    if not isinstance(legacy_runtime, dict):
        legacy_runtime = {}

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
    base_summaries = registry.summaries
    base_send = registry.send

    def worker_settings(client_id: str) -> dict[str, int]:
        client_id = str(client_id or "")
        row = config["workers"].get(client_id) or {}
        return {
            "max_concurrency": _limit(
                row.get("max_concurrency"), config["default_worker_concurrency"]
            ),
            "reserve_windows": _limit(
                row.get("reserve_windows"), config["default_reserve_windows"]
            ),
        }

    def worker_limit(client_id: str) -> int:
        return worker_settings(client_id)["max_concurrency"]

    def reserve_limit(client_id: str) -> int:
        return worker_settings(client_id)["reserve_windows"]

    def key_limit(key_id: str | None) -> int:
        # Managed API keys are placed in routing_key_context by the v17 auth
        # boundary. Requests without that context are authenticated by the
        # server master key (or an admin-internal path), so use the same public
        # key id shown in the API-key table instead of an unconfigurable
        # "anonymous" bucket.
        normalized = str(key_id or "master")
        return _limit(
            config["keys"].get(normalized), config["default_key_concurrency"]
        )

    def used_units(client_id: str) -> int:
        active = broker.client_active_requests.get(str(client_id), {})
        if not isinstance(active, dict):
            return 0
        return sum(max(1, int(weight or 1)) for weight in active.values())

    def is_rate_limited(client_id: str) -> tuple[bool, float]:
        remaining = max(
            0.0,
            float(cooldown_until.get(str(client_id), 0.0)) - time.time(),
        )
        return remaining > 0, remaining

    def queue_depth(client_id: str) -> int:
        return len(worker_queues.get(str(client_id), ()))

    def capacity_snapshot(client_id: str) -> dict[str, Any]:
        client_id = str(client_id)
        active = broker.client_active_requests.get(client_id, {})
        if not isinstance(active, dict):
            active = {}
        used = used_units(client_id)
        limit = worker_limit(client_id)
        limited, remaining = is_rate_limited(client_id)
        client = registry.clients.get(client_id)
        metadata = getattr(client, "metadata", {}) if client else {}
        return {
            "limit_units": limit,
            "configured_limit_units": limit,
            "used_units": used,
            "available_units": max(0, limit - used),
            "active_requests": len(active),
            "request_weights": dict(active),
            "queued_requests": queue_depth(client_id),
            "limit_source": "worker-window-settings-v57",
            "account_type": str((metadata or {}).get("account_type") or "unknown"),
            "account_generation_limit": limit,
            "account_generation_queue": True,
            "account_generation_queue_wait_seconds": None,
            "reserve_window_target": reserve_limit(client_id),
            "rate_limit_cooldown_active": limited,
            "rate_limit_cooldown_remaining_seconds": round(remaining, 1),
        }

    def first_eligible_worker_request(client_id: str) -> str | None:
        """Return the oldest worker-queued request whose own key can run.

        A strict worker queue head would let one saturated API key block every
        other key even while the Worker still had free windows. We keep FIFO
        order within each API key and give the oldest currently eligible request
        the Worker slot, preserving fairness without head-of-line starvation.
        """
        for queued_request_id in worker_queues.get(str(client_id), ()):
            key_id = queued_keys.get(queued_request_id, "master")
            key_queue = key_queues.get(key_id)
            if not key_queue or key_queue[0] != queued_request_id:
                continue
            if int(key_active.get(key_id, 0)) >= key_limit(key_id):
                continue
            return queued_request_id
        return None

    async def create_fifo(request_id: str, client_id: str):
        request_id = str(request_id)
        client_id = str(client_id)
        key_id = str(registry.routing_key_context.get() or "master")
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
                            "selected extension went offline while waiting in the capacity queue"
                        )
                    limited, remaining = is_rate_limited(client_id)
                    worker_ready = (
                        used_units(client_id) < worker_limit(client_id)
                        and first_eligible_worker_request(client_id) == request_id
                    )
                    key_ready = (
                        bool(key_queue and key_queue[0] == request_id)
                        and int(key_active.get(key_id, 0)) < key_limit(key_id)
                    )
                    if worker_ready and key_ready and not limited:
                        break
                    if limited:
                        try:
                            await asyncio.wait_for(
                                condition.wait(),
                                timeout=max(0.1, min(remaining, 30.0)),
                            )
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
                state = RequestState(
                    request_id=request_id,
                    client_id=client_id,
                    final_future=loop.create_future(),
                )
                before = used_units(client_id)
                broker.requests[request_id] = state
                broker.client_active_requests.setdefault(client_id, {})[request_id] = 1
                broker.client_requests.setdefault(client_id, request_id)
                key_active[key_id] += 1
                request_keys[request_id] = key_id
                snapshot = capacity_snapshot(client_id)
                state.diagnostics.update(
                    {
                        "extension_capacity_limit_units": worker_limit(client_id),
                        "extension_capacity_configured_units": worker_limit(client_id),
                        "extension_capacity_weight": 1,
                        "extension_capacity_used_before": before,
                        "extension_capacity_used_after": before + 1,
                        "extension_capacity_wait_ms": round(
                            (time.perf_counter() - started) * 1000, 1
                        ),
                        "extension_concurrency_v21": True,
                        "extension_concurrency_per_client": True,
                        "account_generation_admission": PATCH_ID,
                        "account_generation_limit": worker_limit(client_id),
                        "account_generation_configured_limit": worker_limit(client_id),
                        "account_generation_queue_wait_seconds": None,
                        "capacity_queue_mode": "fifo-unbounded-v57",
                        "capacity_queue_scheduler": "oldest-eligible-cross-key-v57",
                        "api_key_capacity_id": key_id,
                        "api_key_capacity_limit": key_limit(key_id),
                        "api_key_capacity_used_after": key_active[key_id],
                        "reserve_window_target": snapshot["reserve_window_target"],
                    }
                )
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

    async def release_fifo(request_id: str) -> None:
        key_id = request_keys.pop(str(request_id), None)
        await base_release(request_id)
        async with condition:
            if key_id:
                key_active[key_id] = max(
                    0, int(key_active.get(key_id, 0)) - 1
                )
            condition.notify_all()

    def rate_limit_seconds(message: str) -> int:
        match = re.search(r"paused for\s+(\d+)s", message, re.IGNORECASE)
        if match:
            return max(1, min(1800, int(match.group(1))))
        return RATE_LIMIT_DEFAULT_SECONDS

    async def publish_rate_aware(
        request_id: str, event: dict[str, Any]
    ) -> bool:
        event_type = str(event.get("type") or "")
        if event_type in {"chat.error", "chat.cancelled"}:
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

    async def send_with_worker_limit(
        client_id: str, message: dict[str, Any]
    ) -> None:
        if isinstance(message, dict) and str(message.get("type") or "") in {
            "chat.request",
            "image.request",
            "voice.request",
            "voice.live.start",
            "voice.live.request",
        }:
            message = dict(message)
            routing = dict(message.get("routing") or {})
            routing["worker_limit"] = max(
                worker_limit(client_id), reserve_limit(client_id)
            )
            routing["max_concurrency"] = worker_limit(client_id)
            routing["reserve_window_target"] = reserve_limit(client_id)
            message["routing"] = routing
        await base_send(client_id, message)

    def summaries_v57() -> list[dict[str, Any]]:
        rows = base_summaries()
        for row in rows:
            client_id = str(row.get("client_id") or "")
            snapshot = capacity_snapshot(client_id)
            row["busy"] = snapshot["used_units"] >= snapshot["limit_units"]
            row["capacity"] = snapshot
            row["max_concurrency"] = snapshot["limit_units"]
            row["configured_max_concurrency"] = snapshot["limit_units"]
            row["default_max_concurrency"] = int(
                config["default_worker_concurrency"]
            )
            row["concurrency_limit_source"] = snapshot["limit_source"]
            row["account_generation_limit"] = snapshot["limit_units"]
            row["worker_window_settings"] = worker_settings(client_id)
        return rows

    async def persist() -> None:
        _save(config_path, config)
        async with condition:
            condition.notify_all()

    async def persist_reserve(client_id: str, reserve: int) -> None:
        if legacy_runtime:
            legacy_runtime.setdefault("client_limits", {})[str(client_id)] = int(reserve)
            _save_reserve_compat(legacy_runtime)
        await persist()

    @app.get("/api/admin/capacity-v57")
    async def capacity_admin_state() -> dict[str, Any]:
        workers = {
            str(client_id): {
                **worker_settings(client_id),
                "active": used_units(client_id),
                "queued": queue_depth(client_id),
                "rate_limit_cooldown": capacity_snapshot(client_id)[
                    "rate_limit_cooldown_active"
                ],
                "rate_limit_remaining_seconds": capacity_snapshot(client_id)[
                    "rate_limit_cooldown_remaining_seconds"
                ],
            }
            for client_id in registry.clients
        }
        return {
            "version": 57,
            "defaults": {
                "worker_max_concurrency": int(
                    config["default_worker_concurrency"]
                ),
                "reserve_windows": int(config["default_reserve_windows"]),
                "api_key_max_concurrency": int(
                    config["default_key_concurrency"]
                ),
            },
            "workers": workers,
            "keys": dict(config["keys"]),
            "key_active": dict(key_active),
        }

    @app.put("/api/admin/extensions/{client_id}/capacity-v57")
    async def update_worker_capacity(
        client_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        client_id = str(client_id or "").strip()
        if client_id not in registry.clients:
            raise HTTPException(status_code=404, detail="Unknown client_id")
        maximum = _limit(
            body.get("max_concurrency"), DEFAULT_WORKER_CONCURRENCY
        )
        reserve = _limit(body.get("reserve_windows"), DEFAULT_RESERVE_WINDOWS)
        config["workers"][client_id] = {
            "max_concurrency": maximum,
            "reserve_windows": reserve,
        }
        await persist_reserve(client_id, reserve)
        return {
            "ok": True,
            "client_id": client_id,
            **worker_settings(client_id),
        }

    @app.put("/api/admin/keys/{key_id}/concurrency-v57")
    async def update_key_capacity(
        key_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        key_id = str(key_id or "").strip()
        if not key_id:
            raise HTTPException(status_code=400, detail="key_id is required")
        maximum = _limit(body.get("max_concurrency"), DEFAULT_KEY_CONCURRENCY)
        config["keys"][key_id] = maximum
        await persist()
        return {
            "ok": True,
            "key_id": key_id,
            "max_concurrency": maximum,
            "active": int(key_active.get(key_id, 0)),
        }

    broker.client_used_units = used_units
    broker.can_accept = lambda client_id, weight=1: (
        used_units(client_id) + max(1, int(weight or 1)) <= worker_limit(client_id)
    )
    broker.capacity_snapshot = capacity_snapshot
    broker.create = create_fifo
    broker.release = release_fifo
    broker.publish = publish_rate_aware
    broker.account_generation_limit_for = worker_limit
    broker.account_generation_configured_limit_for = worker_limit
    broker._chat2api_capacity_queue_v57 = True
    registry.send = send_with_worker_limit
    registry.summaries = summaries_v57
    registry._chat2api_capacity_queue_v57 = True

    app.state.capacity_queue_v57_installed = True
    app.state.capacity_queue_v57_config = config
    app.state.capacity_queue_v57_id = PATCH_ID
    app.state.capacity_queue_v57_runtime = {
        "worker_queues": worker_queues,
        "key_queues": key_queues,
        "key_active": key_active,
        "request_keys": request_keys,
        "cooldown_until": cooldown_until,
    }
    return app
