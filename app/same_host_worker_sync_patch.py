from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any

from fastapi import FastAPI

from .linux_workers import iso, utcnow


PATCH_ID = "same-host-worker-shared-runtime-sync-v1"
WAIT_STATE = "shared-host-waiting"


def _host_key(worker_id: str, worker: dict[str, Any]) -> str:
    """Stable physical-host key for Workers that share one installed runtime."""
    device_id = str(worker.get("device_id") or "").strip()
    hostname = str(worker.get("hostname") or "").strip()
    platform = str(worker.get("platform") or "linux").strip().lower() or "linux"
    identity = device_id or hostname
    return f"{platform}:{identity}" if identity else f"worker:{worker_id}"


def _created_key(item: tuple[str, dict[str, Any]]) -> tuple[str, str]:
    worker_id, worker = item
    return (str(worker.get("created_at") or "9999"), worker_id)


def install_same_host_worker_sync_patch(app: FastAPI) -> FastAPI:
    """Coalesce online upgrades for multiple Worker slots on one Linux host.

    Same-host slots intentionally share /opt/chat2api-worker and its Python venv,
    while keeping Worker identity, Chrome profile, Xray listener, display and CDP
    endpoint isolated. A server deployment must therefore update that shared host
    runtime once, not once per logical Worker. Secondary Workers wait for the
    oldest online peer to complete the host update and are marked synced only
    after their own heartbeat reports the target runtime versions.
    """

    if getattr(app.state, "same_host_worker_sync_patch_installed", False):
        return app
    coordinator = getattr(app.state, "server_worker_sync", None)
    if coordinator is None:
        raise RuntimeError("server worker sync must be installed before same-host coalescing")

    app.state.same_host_worker_sync_patch_installed = True
    locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    base_schedule_worker = coordinator._schedule_worker
    base_run_once = coordinator.run_once

    def peers_for(worker_id: str, worker: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        key = _host_key(worker_id, worker)
        with coordinator.store._lock:
            items = [
                (candidate_id, dict(candidate))
                for candidate_id, candidate in coordinator.store.data["workers"].items()
                if not candidate.get("revoked_at")
                and _host_key(candidate_id, candidate) == key
            ]
        return sorted(items, key=_created_key)

    def online_leader(peers: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]] | None:
        online = [item for item in peers if item[0] in app.state.worker_sockets]
        return min(online, key=_created_key) if online else None

    def mark_waiting(
        worker_id: str,
        target_commit: str,
        reason: str,
        leader_id: str,
    ) -> None:
        now = iso(utcnow())
        coordinator._update_worker_meta(
            worker_id,
            target_commit=target_commit,
            state=WAIT_STATE,
            reason=reason,
            shared_runtime_leader=leader_id,
            shared_runtime_host=True,
            last_attempt_at=now,
            last_attempt_epoch=time.time(),
            last_error="",
        )
        coordinator._write_upgrade_state(
            worker_id,
            state="running",
            stage=WAIT_STATE,
            message=f"同机 Worker 共用运行时，等待 {leader_id} 完成一次主机级更新",
            percent=2,
            reset=False,
        )

    def mark_shared_synced(worker_id: str, target_commit: str, leader_id: str) -> None:
        now = iso(utcnow())
        coordinator._update_worker_meta(
            worker_id,
            target_commit=target_commit,
            state="synced",
            last_synced_commit=target_commit,
            shared_runtime_leader=leader_id,
            shared_runtime_host=True,
            shared_runtime_synced_at=now,
            last_error="",
        )
        coordinator._write_upgrade_state(
            worker_id,
            state="succeeded",
            stage="shared-host-complete",
            message=f"同机共享运行时已由 {leader_id} 更新，本 Worker 已用目标版本重新上线",
            percent=100,
            reset=False,
        )

    async def schedule_worker(worker_id: str, target_commit: str, reason: str) -> None:
        with coordinator.store._lock:
            raw = coordinator.store.data["workers"].get(worker_id)
            worker = dict(raw) if raw else None
        if not worker or worker.get("revoked_at"):
            return

        host_key = _host_key(worker_id, worker)
        async with locks[host_key]:
            # Refresh under the lock because another same-host task may already
            # have transitioned the leader while this task was waiting.
            with coordinator.store._lock:
                raw = coordinator.store.data["workers"].get(worker_id)
                worker = dict(raw) if raw else None
            if not worker or worker.get("revoked_at"):
                return
            peers = peers_for(worker_id, worker)
            if len(peers) <= 1:
                await base_schedule_worker(worker_id, target_commit, reason)
                return

            leader = online_leader(peers)
            if leader is None or leader[0] == worker_id:
                await base_schedule_worker(worker_id, target_commit, reason)
                return

            leader_id, leader_worker = leader
            leader_sync = coordinator._sync_meta(leader_worker)
            if (
                str(leader_sync.get("last_synced_commit") or "") == target_commit
                and coordinator._versions_current(worker)
            ):
                mark_shared_synced(worker_id, target_commit, leader_id)
                return

            mark_waiting(worker_id, target_commit, reason, leader_id)

    def reconcile_waiters() -> None:
        with coordinator.store._lock:
            items = [
                (worker_id, dict(worker))
                for worker_id, worker in coordinator.store.data["workers"].items()
                if not worker.get("revoked_at")
            ]
        by_host: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for worker_id, worker in items:
            by_host[_host_key(worker_id, worker)].append((worker_id, worker))

        for group in by_host.values():
            if len(group) <= 1:
                continue
            group.sort(key=_created_key)
            for worker_id, worker in group:
                sync = coordinator._sync_meta(worker)
                if str(sync.get("state") or "") != WAIT_STATE:
                    continue
                target_commit = str(sync.get("target_commit") or "")
                if not target_commit or not coordinator._versions_current(worker):
                    continue
                leader_id = str(sync.get("shared_runtime_leader") or "")
                leader = next((candidate for candidate in group if candidate[0] == leader_id), None)
                if leader is None:
                    continue
                leader_sync = coordinator._sync_meta(leader[1])
                if str(leader_sync.get("last_synced_commit") or "") != target_commit:
                    continue
                mark_shared_synced(worker_id, target_commit, leader_id)

    async def run_once() -> dict[str, Any]:
        result = await base_run_once()
        reconcile_waiters()
        return result

    coordinator._schedule_worker = schedule_worker
    coordinator.run_once = run_once
    coordinator.same_host_worker_sync_patch = PATCH_ID
    return app
