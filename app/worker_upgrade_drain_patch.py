from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from typing import Any

from fastapi import FastAPI


PATCH_ID = "worker-upgrade-drain-v1"
UPGRADE_DRAIN_TIMEOUT_SECONDS = 960.0
UPGRADE_DRAIN_POLL_SECONDS = 0.25
UPGRADE_DRAIN_SETTLE_SECONDS = 0.75
UPGRADE_DRAIN_TTL_SECONDS = 1800.0
TERMINAL_UPGRADE_STATES = frozenset({"succeeded", "failed", "unsupported"})
_PROGRESS_PATH = re.compile(r"^/api/workers/([^/]+)/upgrade-progress$")


def _host_key(worker_id: str, worker: dict[str, Any]) -> str:
    platform = str(worker.get("platform") or "linux").strip().lower() or "linux"
    identity = str(
        worker.get("device_id")
        or worker.get("extension_device_id")
        or worker.get("hostname")
        or ""
    ).strip()
    return f"{platform}:{identity}" if identity else f"worker:{worker_id}"


def _active_request_ids(broker: Any, client_id: str) -> list[str]:
    active_by_client = getattr(broker, "client_active_requests", None)
    if isinstance(active_by_client, dict):
        active = active_by_client.get(str(client_id))
        if isinstance(active, dict):
            return [str(request_id) for request_id in active]
        if isinstance(active, (set, list, tuple)):
            return [str(request_id) for request_id in active]
        return []

    client_requests = getattr(broker, "client_requests", None)
    request_id = client_requests.get(str(client_id)) if isinstance(client_requests, dict) else None
    if request_id and request_id in getattr(broker, "requests", {}):
        return [str(request_id)]

    requests = getattr(broker, "requests", {})
    if isinstance(requests, dict):
        return [
            str(request_id)
            for request_id, state in requests.items()
            if str(getattr(state, "client_id", "") or "") == str(client_id)
            and getattr(state, "completed_mono", None) is None
        ]
    return []


def install_worker_upgrade_drain_patch(app: FastAPI) -> FastAPI:
    """Drain every in-flight request on a Linux host before restarting its shared Worker runtime."""
    if getattr(app.state, "worker_upgrade_drain_patch_installed", False):
        return app

    store = app.state.linux_workers
    registry = app.state.registry
    broker = app.state.broker
    base_send = app.state.send_linux_worker_command
    base_routing_ready = registry.chatgpt_routing_ready
    base_online_client_ids = registry.online_client_ids
    host_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def host_workers(worker_id: str) -> list[tuple[str, dict[str, Any]]]:
        with store._lock:
            rows = [
                (str(candidate_id), dict(candidate))
                for candidate_id, candidate in store.data.get("workers", {}).items()
                if isinstance(candidate, dict) and not candidate.get("revoked_at")
            ]
        target = next((row for row in rows if row[0] == str(worker_id)), None)
        if target is None:
            return []
        key = _host_key(target[0], target[1])
        if key == f"worker:{worker_id}":
            return [target]
        return [row for row in rows if _host_key(row[0], row[1]) == key]

    def host_client_ids(worker_id: str) -> list[str]:
        result: set[str] = set()
        for _, worker in host_workers(worker_id):
            direct = str(worker.get("extension_client_id") or "").strip()
            if direct:
                result.add(direct)
            metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
            bridge = metadata.get("bridge") if isinstance(metadata.get("bridge"), dict) else {}
            bridged = str(bridge.get("extension_id") or bridge.get("client_id") or "").strip()
            if bridged:
                result.add(bridged)
        return sorted(result)

    def maintenance_active(client_id: str) -> bool:
        client = registry.clients.get(str(client_id))
        if client is None:
            return False
        metadata = getattr(client, "metadata", None)
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        marker = metadata.get("worker_maintenance")
        if not isinstance(marker, dict) or marker.get("active") is not True:
            return False
        try:
            expires_at = float(marker.get("expires_at_epoch") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at and expires_at <= time.time():
            metadata.pop("worker_maintenance", None)
            client.metadata = metadata
            return False
        return True

    def set_maintenance(client_id: str, marker: dict[str, Any]) -> None:
        client = registry.clients.get(str(client_id))
        if client is None:
            return
        metadata = getattr(client, "metadata", None)
        next_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        next_metadata["worker_maintenance"] = dict(marker)
        client.metadata = next_metadata

    def clear_client_maintenance(client_id: str) -> None:
        client = registry.clients.get(str(client_id))
        if client is None:
            return
        metadata = getattr(client, "metadata", None)
        next_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if "worker_maintenance" in next_metadata:
            next_metadata.pop("worker_maintenance", None)
            client.metadata = next_metadata

    def clear_worker_upgrade_drain(worker_id: str) -> None:
        for client_id in host_client_ids(str(worker_id)):
            clear_client_maintenance(client_id)

    def routing_ready(client_id: str) -> bool:
        return not maintenance_active(str(client_id)) and bool(base_routing_ready(str(client_id)))

    def online_client_ids() -> list[str]:
        return [
            str(client_id)
            for client_id in base_online_client_ids()
            if not maintenance_active(str(client_id))
        ]

    registry.chatgpt_routing_ready = routing_ready
    registry.online_client_ids = online_client_ids

    async def begin_worker_upgrade_drain(
        worker_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        records = host_workers(str(worker_id))
        if not records:
            return {"ok": False, "error": "worker_not_found"}
        client_ids = host_client_ids(str(worker_id))
        if not client_ids:
            return {"ok": False, "error": "worker_extension_not_bound"}

        timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(getattr(app.state, "worker_upgrade_drain_timeout_seconds", UPGRADE_DRAIN_TIMEOUT_SECONDS))
        )
        settle = max(
            0.0,
            float(getattr(app.state, "worker_upgrade_drain_settle_seconds", UPGRADE_DRAIN_SETTLE_SECONDS)),
        )
        key = _host_key(records[0][0], records[0][1])

        async with host_locks[key]:
            if any(maintenance_active(client_id) for client_id in client_ids):
                return {"ok": False, "error": "worker_upgrade_already_draining"}

            marker = {
                "active": True,
                "reason": "worker-upgrade",
                "owner_worker_id": str(worker_id),
                "started_at_epoch": int(time.time()),
                "expires_at_epoch": int(time.time() + UPGRADE_DRAIN_TTL_SECONDS),
                "patch": PATCH_ID,
            }
            for client_id in client_ids:
                set_maintenance(client_id, marker)

            loop = asyncio.get_running_loop()
            deadline = loop.time() + max(0.1, timeout)
            idle_since: float | None = None
            try:
                while True:
                    active = [
                        (client_id, request_id)
                        for client_id in client_ids
                        for request_id in _active_request_ids(broker, client_id)
                    ]
                    now = loop.time()
                    if not active:
                        if idle_since is None:
                            idle_since = now
                        elif now - idle_since >= settle:
                            return {
                                "ok": True,
                                "worker_ids": [item[0] for item in records],
                                "client_ids": client_ids,
                                "drained_request_count": 0,
                            }
                    else:
                        idle_since = None

                    remaining = deadline - now
                    if remaining <= 0:
                        clear_worker_upgrade_drain(str(worker_id))
                        return {
                            "ok": False,
                            "error": "worker_active_requests_drain_timeout",
                            "active_request_count": len(active),
                            "active_request_ids": [request_id for _, request_id in active[:20]],
                        }
                    await asyncio.sleep(min(UPGRADE_DRAIN_POLL_SECONDS, remaining))
            except asyncio.CancelledError:
                clear_worker_upgrade_drain(str(worker_id))
                raise

    app.state.begin_worker_upgrade_drain = begin_worker_upgrade_drain
    app.state.clear_worker_upgrade_drain = clear_worker_upgrade_drain

    async def send_with_worker_upgrade_drain(
        worker_id: str,
        command: str,
        arguments: dict[str, Any],
        *,
        wait: bool,
        timeout: float = 75.0,
    ):
        if str(command) != "upgrade_worker":
            return await base_send(worker_id, command, arguments, wait=wait, timeout=timeout)

        with store._lock:
            worker = store.data.get("workers", {}).get(str(worker_id))
            worker = dict(worker) if isinstance(worker, dict) else None
        if not worker or str(worker.get("platform") or "linux").strip().lower() != "linux":
            return await base_send(worker_id, command, arguments, wait=wait, timeout=timeout)

        client_ids = host_client_ids(str(worker_id))
        if any(maintenance_active(client_id) for client_id in client_ids):
            return {
                "accepted": True,
                "result": {
                    "ok": True,
                    "scheduled": True,
                    "already_scheduled": True,
                    "unit": "existing",
                },
            }

        drain = await begin_worker_upgrade_drain(str(worker_id))
        if str(drain.get("error") or "") == "worker_upgrade_already_draining":
            return {
                "accepted": True,
                "result": {
                    "ok": True,
                    "scheduled": True,
                    "already_scheduled": True,
                    "unit": "existing",
                },
            }
        if not drain.get("ok"):
            error = str(drain.get("error") or "worker_upgrade_drain_failed")
            detail = (
                f"仍有 {int(drain.get('active_request_count') or 0)} 个 API 请求未完成"
                if error == "worker_active_requests_drain_timeout"
                else error
            )
            return {
                "accepted": False,
                "result": {
                    "ok": False,
                    "error": error,
                    "detail": detail,
                    "retryable": error in {"worker_active_requests_drain_timeout", "worker_upgrade_already_draining"},
                    "active_request_count": int(drain.get("active_request_count") or 0),
                    "active_request_ids": list(drain.get("active_request_ids") or [])[:20],
                },
            }

        try:
            result = await base_send(worker_id, command, arguments, wait=wait, timeout=timeout)
        except BaseException:
            clear_worker_upgrade_drain(str(worker_id))
            raise

        payload = result.get("result") if isinstance(result, dict) else None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            clear_worker_upgrade_drain(str(worker_id))
        return result

    app.state.send_linux_worker_command = send_with_worker_upgrade_drain

    @app.middleware("http")
    async def clear_worker_upgrade_drain_after_terminal_progress(request, call_next):
        match = _PROGRESS_PATH.fullmatch(request.url.path) if request.method == "POST" else None
        response = await call_next(request)
        if match and response.status_code < 400:
            worker_id = match.group(1)
            with store._lock:
                worker = store.data.get("workers", {}).get(worker_id)
                metadata = worker.get("metadata") if isinstance(worker, dict) else {}
                upgrade = metadata.get("worker_upgrade") if isinstance(metadata, dict) else {}
                state = str(upgrade.get("state") or "") if isinstance(upgrade, dict) else ""
            if state in TERMINAL_UPGRADE_STATES:
                clear_worker_upgrade_drain(worker_id)
        return response

    app.state.worker_upgrade_drain_patch_installed = True
    return app
