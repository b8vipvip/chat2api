from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from .admin_auth import SESSION_COOKIE
from .linux_worker_upgrade_patch import TARGET_AGENT_VERSION
from .linux_workers import iso, utcnow
from .runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION


PATCH_ID = "server-worker-auto-sync-v1"
DEPLOYMENT_NAME = "deployment.json"
SERVER_UPDATE_STATUS_NAME = "admin-update-status.json"
SYNC_STATUS_NAME = "linux-worker-auto-sync-status.json"
GITHUB_REPOSITORY = "b8vipvip/chat2api"
GITHUB_COMPARE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/compare"
POLL_SECONDS = 5.0
COMPARE_RETRY_SECONDS = 300.0
WORKER_RETRY_SECONDS = 60.0
MAX_AUTO_ATTEMPTS_PER_COMMIT = 3
logger = logging.getLogger("chat2api.server_worker_sync")

# Files copied to a Linux Worker, or server-side bootstrap transformers whose
# output becomes part of the Worker installation, require a Worker refresh.
# Pure console/UI/server changes deliberately do not match this list.
_WORKER_PATH_PREFIXES = (
    "chrome_extension/",
    "scripts/linux_worker_",
    "scripts/linux_extension_",
)
_WORKER_PATH_EXACT = frozenset(
    {
        "scripts/bootstrap_linux_worker.sh",
        "app/linux_worker_patch.py",
        "app/linux_worker_install_patch.py",
        "app/linux_worker_xray_patch.py",
        "app/linux_worker_initialize_patch.py",
        "app/linux_worker_upgrade_patch.py",
        "app/linux_worker_diagnostics_patch.py",
        "app/linux_worker_repair_command_patch.py",
    }
)
_UNSUPPORTED_UPGRADE_ERRORS = frozenset(
    {
        "command_not_allowed",
        "not_implemented",
        "upgrade_helper_missing",
        "upgrade_schedule_failed",
        "upgrade_schedule_timeout",
        "upgrade_helper_launch_failed",
    }
)


def worker_update_path(path: str) -> bool:
    value = str(path or "").strip().replace("\\", "/")
    if not value:
        return False
    if value in _WORKER_PATH_EXACT:
        return True
    return any(value.startswith(prefix) for prefix in _WORKER_PATH_PREFIXES)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _safe_paths(paths: list[str], limit: int = 80) -> list[str]:
    return [str(item)[:300] for item in paths[:limit]]


class ServerWorkerSyncCoordinator:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.store = app.state.linux_workers
        self.data_dir = Path(app.state.settings.data_dir)
        self.active_workers: set[str] = set()
        self.scan_lock = asyncio.Lock()

    @property
    def status_path(self) -> Path:
        return self.data_dir / SYNC_STATUS_NAME

    def status(self) -> dict[str, Any]:
        payload = _read_json(self.status_path)
        if not payload:
            return {
                "patch": PATCH_ID,
                "state": "idle",
                "server_runtime": SERVER_RUNTIME_VERSION,
                "target_agent_version": TARGET_AGENT_VERSION,
                "target_worker_bundle_version": CHROME_BRIDGE_BUNDLE_VERSION,
            }
        payload.setdefault("patch", PATCH_ID)
        payload.setdefault("server_runtime", SERVER_RUNTIME_VERSION)
        payload.setdefault("target_agent_version", TARGET_AGENT_VERSION)
        payload.setdefault("target_worker_bundle_version", CHROME_BRIDGE_BUNDLE_VERSION)
        return payload

    def _save_status(self, **changes: Any) -> dict[str, Any]:
        current = self.status()
        current.update(changes)
        current["updated_at"] = iso(utcnow())
        _write_json_atomic(self.status_path, current)
        return current

    def _server_update_running(self) -> bool:
        value = _read_json(self.data_dir / SERVER_UPDATE_STATUS_NAME)
        return str(value.get("status") or "") in {"queued", "running"}

    def _deployment(self) -> dict[str, Any]:
        return _read_json(self.data_dir / DEPLOYMENT_NAME)

    async def _compare_decision(self, previous: str, target: str) -> dict[str, Any]:
        if not previous or not target or previous == target:
            return {
                "known": True,
                "required": False,
                "changed_paths": [],
                "worker_changed_paths": [],
                "compare_error": "",
                "truncated": False,
            }

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "chat2api-server-worker-sync",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = str(os.getenv("CHAT2API_GITHUB_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"{GITHUB_COMPARE_API}/{previous}...{target}"
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json()
            files = payload.get("files") if isinstance(payload, dict) else []
            files = files if isinstance(files, list) else []
            changed = [str(item.get("filename") or "") for item in files if isinstance(item, dict)]
            worker_changed = [path for path in changed if worker_update_path(path)]
            # GitHub Compare can cap the file list. If the response is at the cap
            # and none of the visible paths matched, prefer one harmless Worker
            # refresh over silently missing a Worker payload change.
            truncated = len(changed) >= 300
            return {
                "known": True,
                "required": bool(worker_changed) or truncated,
                "changed_paths": _safe_paths(changed),
                "worker_changed_paths": _safe_paths(worker_changed),
                "compare_error": "",
                "truncated": truncated,
            }
        except Exception as exc:
            return {
                "known": False,
                "required": False,
                "changed_paths": [],
                "worker_changed_paths": [],
                "compare_error": str(exc)[:500],
                "truncated": False,
            }

    async def _decision(self, deployment: dict[str, Any]) -> dict[str, Any]:
        target = str(deployment.get("commit") or "")
        previous = str(deployment.get("previous_commit") or "")
        current = self.status()
        same = (
            str(current.get("decision_commit") or "") == target
            and str(current.get("decision_previous_commit") or "") == previous
        )
        if same and current.get("decision_known") is True:
            return {
                "known": True,
                "required": bool(current.get("worker_update_required")),
                "changed_paths": list(current.get("changed_paths") or []),
                "worker_changed_paths": list(current.get("worker_changed_paths") or []),
                "compare_error": "",
                "truncated": bool(current.get("compare_truncated")),
            }
        if same and current.get("decision_known") is False:
            checked_epoch = float(current.get("decision_checked_epoch") or 0.0)
            if time.time() - checked_epoch < COMPARE_RETRY_SECONDS:
                return {
                    "known": False,
                    "required": False,
                    "changed_paths": [],
                    "worker_changed_paths": [],
                    "compare_error": str(current.get("compare_error") or ""),
                    "truncated": False,
                }

        decision = await self._compare_decision(previous, target)
        self._save_status(
            decision_commit=target,
            decision_previous_commit=previous,
            decision_known=bool(decision.get("known")),
            decision_checked_epoch=time.time(),
            worker_update_required=bool(decision.get("required")),
            changed_paths=list(decision.get("changed_paths") or []),
            worker_changed_paths=list(decision.get("worker_changed_paths") or []),
            compare_error=str(decision.get("compare_error") or ""),
            compare_truncated=bool(decision.get("truncated")),
        )
        return decision

    @staticmethod
    def _metadata(worker: dict[str, Any]) -> dict[str, Any]:
        return dict(worker.get("metadata") or {}) if isinstance(worker.get("metadata"), dict) else {}

    @classmethod
    def _worker_bundle_version(cls, worker: dict[str, Any]) -> str:
        direct = str(worker.get("chrome_bridge_version") or "").strip()
        if direct:
            return direct
        bridge = cls._metadata(worker).get("bridge")
        if isinstance(bridge, dict):
            return str(bridge.get("extension_version") or "").strip()
        return ""

    @classmethod
    def _versions_current(cls, worker: dict[str, Any]) -> bool:
        return (
            str(worker.get("agent_version") or "").strip() == TARGET_AGENT_VERSION
            and cls._worker_bundle_version(worker) == CHROME_BRIDGE_BUNDLE_VERSION
        )

    @classmethod
    def _sync_meta(cls, worker: dict[str, Any]) -> dict[str, Any]:
        value = cls._metadata(worker).get("server_update_sync")
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _upgrade_meta(cls, worker: dict[str, Any]) -> dict[str, Any]:
        value = cls._metadata(worker).get("worker_upgrade")
        return dict(value) if isinstance(value, dict) else {}

    def _update_worker_meta(self, worker_id: str, **changes: Any) -> dict[str, Any]:
        with self.store._lock:
            worker = self.store.data["workers"].get(worker_id)
            if not worker:
                return {}
            metadata = dict(worker.get("metadata") or {}) if isinstance(worker.get("metadata"), dict) else {}
            sync = dict(metadata.get("server_update_sync") or {}) if isinstance(metadata.get("server_update_sync"), dict) else {}
            target_commit = str(changes.get("target_commit") or sync.get("target_commit") or "")
            if target_commit and target_commit != str(sync.get("target_commit") or ""):
                sync = {
                    "target_commit": target_commit,
                    "attempts": 0,
                    "last_synced_commit": str(sync.get("last_synced_commit") or ""),
                }
            sync.update(changes)
            sync["updated_at"] = iso(utcnow())
            metadata["server_update_sync"] = sync
            worker["metadata"] = metadata
            self.store._save()
            return dict(sync)

    def _write_upgrade_state(
        self,
        worker_id: str,
        *,
        state: str,
        stage: str,
        message: str,
        percent: int,
        reset: bool,
    ) -> None:
        now = iso(utcnow())
        with self.store._lock:
            worker = self.store.data["workers"].get(worker_id)
            if not worker:
                return
            metadata = dict(worker.get("metadata") or {}) if isinstance(worker.get("metadata"), dict) else {}
            previous = metadata.get("worker_upgrade") if isinstance(metadata.get("worker_upgrade"), dict) else {}
            history = [] if reset else list(previous.get("history") or [])[-79:]
            history.append(
                {
                    "at": now,
                    "state": state,
                    "stage": stage,
                    "percent": max(0, min(int(percent), 100)),
                    "message": message[:700],
                }
            )
            metadata["worker_upgrade"] = {
                "state": state,
                "stage": stage,
                "percent": max(0, min(int(percent), 100)),
                "message": message[:700],
                "started_at": now if reset or not previous.get("started_at") else str(previous.get("started_at")),
                "updated_at": now,
                "completed_at": now if state in {"succeeded", "failed", "unsupported"} else "",
                "target_server_runtime": SERVER_RUNTIME_VERSION,
                "target_agent_version": TARGET_AGENT_VERSION,
                "target_chrome_bridge_version": CHROME_BRIDGE_BUNDLE_VERSION,
                "history": history[-80:],
            }
            worker["metadata"] = metadata
            self.store._save()

    def _refresh_completed_sync(self, worker_id: str, worker: dict[str, Any], target_commit: str, force: bool) -> dict[str, Any]:
        sync = self._sync_meta(worker)
        if str(sync.get("target_commit") or "") != target_commit:
            return sync
        scheduled_at = str(sync.get("attempt_started_at") or "")
        upgrade = self._upgrade_meta(worker)
        upgrade_started = str(upgrade.get("started_at") or "")
        terminal_for_attempt = bool(scheduled_at and upgrade_started and upgrade_started >= scheduled_at)

        if self._versions_current(worker) and not force:
            return self._update_worker_meta(
                worker_id,
                target_commit=target_commit,
                state="synced",
                last_synced_commit=target_commit,
                last_error="",
            )
        if terminal_for_attempt and str(upgrade.get("state") or "") == "succeeded":
            return self._update_worker_meta(
                worker_id,
                target_commit=target_commit,
                state="synced",
                last_synced_commit=target_commit,
                last_error="",
            )
        if terminal_for_attempt and str(upgrade.get("state") or "") in {"failed", "unsupported"}:
            error = str(upgrade.get("message") or upgrade.get("stage") or "Worker upgrade failed")[:500]
            return self._update_worker_meta(
                worker_id,
                target_commit=target_commit,
                state="failed",
                last_error=error,
            )
        return sync

    def _needs_sync(self, worker: dict[str, Any], target_commit: str, force: bool) -> tuple[bool, str]:
        if not self._versions_current(worker):
            return True, "version-mismatch"
        sync = self._sync_meta(worker)
        if force and str(sync.get("last_synced_commit") or "") != target_commit:
            return True, "worker-payload-changed"
        return False, "up-to-date"

    def _retry_allowed(self, sync: dict[str, Any], target_commit: str) -> bool:
        if str(sync.get("target_commit") or "") != target_commit:
            return True
        state = str(sync.get("state") or "")
        if state in {"scheduled", "running", "queued", "manual-repair-required"}:
            return False
        attempts = int(sync.get("attempts") or 0)
        if attempts >= MAX_AUTO_ATTEMPTS_PER_COMMIT:
            return False
        last_epoch = float(sync.get("last_attempt_epoch") or 0.0)
        return not last_epoch or time.time() - last_epoch >= WORKER_RETRY_SECONDS

    async def _schedule_worker(self, worker_id: str, target_commit: str, reason: str) -> None:
        if worker_id in self.active_workers:
            return
        self.active_workers.add(worker_id)
        try:
            with self.store._lock:
                worker = self.store.data["workers"].get(worker_id)
                if not worker or worker.get("revoked_at"):
                    return
                prior = self._sync_meta(worker)
                attempts = int(prior.get("attempts") or 0) + 1

            started_at = iso(utcnow())
            self._update_worker_meta(
                worker_id,
                target_commit=target_commit,
                state="queued",
                reason=reason,
                attempts=attempts,
                attempt_started_at=started_at,
                last_attempt_at=started_at,
                last_attempt_epoch=time.time(),
                target_agent_version=TARGET_AGENT_VERSION,
                target_worker_bundle_version=CHROME_BRIDGE_BUNDLE_VERSION,
                last_error="",
            )
            self._write_upgrade_state(
                worker_id,
                state="queued",
                stage="server-update-sync",
                message="服务端更新协调器已排队 Linux Worker 在线更新",
                percent=1,
                reset=True,
            )
            try:
                command = await self.app.state.send_linux_worker_command(
                    worker_id,
                    "upgrade_worker",
                    {},
                    wait=True,
                    timeout=20,
                )
            except HTTPException as exc:
                detail = str(exc.detail)[:500]
                if exc.status_code == 409:
                    state = "pending-offline"
                else:
                    state = "failed"
                self._update_worker_meta(
                    worker_id,
                    target_commit=target_commit,
                    state=state,
                    last_error=detail,
                )
                self._write_upgrade_state(
                    worker_id,
                    state="failed",
                    stage="server-update-sync",
                    message=detail,
                    percent=1,
                    reset=False,
                )
                return

            result = command.get("result") if isinstance(command, dict) else None
            if isinstance(result, dict) and result.get("ok"):
                self._update_worker_meta(
                    worker_id,
                    target_commit=target_commit,
                    state="scheduled",
                    scheduled_commit=target_commit,
                    scheduled_at=iso(utcnow()),
                    last_error="",
                )
                self._write_upgrade_state(
                    worker_id,
                    state="running",
                    stage="scheduled",
                    message="Worker 已接受服务端更新后的自动在线更新任务",
                    percent=2,
                    reset=False,
                )
                return

            error = str((result or {}).get("error") or "unknown_error") if isinstance(result, dict) else "unknown_error"
            detail = str((result or {}).get("detail") or "") if isinstance(result, dict) else ""
            diagnostic = error + (f" · {detail}" if detail else "")
            if error in _UNSUPPORTED_UPGRADE_ERRORS:
                state = "manual-repair-required"
                upgrade_state = "unsupported"
                message = f"当前 Worker 缺少安全在线更新能力，需要执行一次幂等 bootstrap 修复：{diagnostic}"
            else:
                state = "failed"
                upgrade_state = "failed"
                message = f"Worker 自动在线更新启动失败：{diagnostic}"
            self._update_worker_meta(
                worker_id,
                target_commit=target_commit,
                state=state,
                last_error=message[:500],
            )
            self._write_upgrade_state(
                worker_id,
                state=upgrade_state,
                stage="server-update-sync",
                message=message,
                percent=1,
                reset=False,
            )
        finally:
            self.active_workers.discard(worker_id)

    def _summary(self, target_commit: str, force: bool, decision: dict[str, Any]) -> dict[str, Any]:
        counts = {
            "total": 0,
            "up_to_date": 0,
            "scheduled": 0,
            "pending_offline": 0,
            "busy": 0,
            "retry_wait": 0,
            "manual_repair_required": 0,
            "failed": 0,
        }
        rows: list[dict[str, Any]] = []
        with self.store._lock:
            items = [(worker_id, dict(worker)) for worker_id, worker in self.store.data["workers"].items()]
        for worker_id, worker in items:
            if worker.get("revoked_at"):
                continue
            counts["total"] += 1
            sync = self._sync_meta(worker)
            needs, reason = self._needs_sync(worker, target_commit, force)
            state = str(sync.get("state") or "")
            online = worker_id in self.app.state.worker_sockets
            if not needs:
                bucket = "up_to_date"
            elif state == "manual-repair-required":
                bucket = "manual_repair_required"
            elif state in {"scheduled", "running", "queued"}:
                bucket = "scheduled"
            elif not online:
                bucket = "pending_offline"
            elif state == "failed" and int(sync.get("attempts") or 0) >= MAX_AUTO_ATTEMPTS_PER_COMMIT:
                bucket = "failed"
            elif state == "failed":
                bucket = "retry_wait"
            else:
                bucket = "busy" if worker_id in self.active_workers else "retry_wait"
            counts[bucket] += 1
            rows.append(
                {
                    "worker_id": worker_id,
                    "name": str(worker.get("name") or worker.get("hostname") or worker_id)[:120],
                    "online": online,
                    "agent_version": str(worker.get("agent_version") or ""),
                    "worker_bundle_version": self._worker_bundle_version(worker),
                    "needs_sync": needs,
                    "reason": reason,
                    "state": state or ("up-to-date" if not needs else "pending"),
                    "attempts": int(sync.get("attempts") or 0),
                    "last_error": str(sync.get("last_error") or "")[:300],
                }
            )
        return {
            **counts,
            "workers": rows[:200],
            "worker_update_required_by_commit": force,
            "decision_known": bool(decision.get("known")),
            "compare_error": str(decision.get("compare_error") or ""),
        }

    async def run_once(self) -> dict[str, Any]:
        async with self.scan_lock:
            deployment = self._deployment()
            target_commit = str(deployment.get("commit") or "")
            if not target_commit:
                return self._save_status(state="idle", message="尚无已确认的服务端 deployment，不执行 Worker 自动更新")
            if self._server_update_running():
                return self._save_status(
                    state="waiting-server",
                    target_commit=target_commit,
                    message="服务端更新尚未通过健康检查，暂不更新 Linux Worker",
                )

            decision = await self._decision(deployment)
            force = bool(decision.get("known") and decision.get("required"))

            with self.store._lock:
                items = [(worker_id, dict(worker)) for worker_id, worker in self.store.data["workers"].items()]

            schedule: list[asyncio.Task[None]] = []
            for worker_id, worker in items:
                if worker.get("revoked_at"):
                    continue
                sync = self._refresh_completed_sync(worker_id, worker, target_commit, force)
                with self.store._lock:
                    live = self.store.data["workers"].get(worker_id)
                    worker = dict(live) if live else worker
                needs, reason = self._needs_sync(worker, target_commit, force)
                if not needs:
                    continue

                if worker_id not in self.app.state.worker_sockets:
                    self._update_worker_meta(
                        worker_id,
                        target_commit=target_commit,
                        state="pending-offline",
                        reason=reason,
                        target_agent_version=TARGET_AGENT_VERSION,
                        target_worker_bundle_version=CHROME_BRIDGE_BUNDLE_VERSION,
                    )
                    continue

                upgrade = self._upgrade_meta(worker)
                if str(upgrade.get("state") or "") in {"queued", "running"}:
                    self._update_worker_meta(
                        worker_id,
                        target_commit=target_commit,
                        state="running",
                        reason=reason,
                    )
                    continue

                if not self._retry_allowed(sync, target_commit):
                    continue
                schedule.append(asyncio.create_task(self._schedule_worker(worker_id, target_commit, reason)))

            if schedule:
                await asyncio.gather(*schedule, return_exceptions=True)

            summary = self._summary(target_commit, force, decision)
            outstanding = (
                summary["scheduled"]
                + summary["pending_offline"]
                + summary["busy"]
                + summary["retry_wait"]
                + summary["manual_repair_required"]
                + summary["failed"]
            )
            if summary["manual_repair_required"] or summary["failed"]:
                state = "attention-required"
            elif outstanding:
                state = "syncing"
            else:
                state = "completed"
            return self._save_status(
                state=state,
                target_commit=target_commit,
                previous_commit=str(deployment.get("previous_commit") or ""),
                message=(
                    "Linux Worker 自动同步已完成"
                    if state == "completed"
                    else "正在自动同步需要更新的 Linux Worker；离线 Worker 会在重新上线后继续"
                ),
                **summary,
            )

    async def loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("server worker auto-sync iteration failed")
                self._save_status(state="error", message="Linux Worker 自动同步协调器发生异常，请查看运行日志")
            await asyncio.sleep(POLL_SECONDS)


def install_server_worker_sync_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "server_worker_sync_patch_installed", False):
        return app
    if not getattr(app.state, "linux_worker_upgrade_patch_installed", False):
        raise RuntimeError("linux worker upgrade patch must be installed before server worker sync")

    app.state.server_worker_sync_patch_installed = True
    coordinator = ServerWorkerSyncCoordinator(app)
    app.state.server_worker_sync = coordinator
    app.state.server_worker_sync_task = None

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    @app.get("/api/admin/server-worker-sync")
    async def server_worker_sync_status(request: Request) -> dict[str, Any]:
        admin(request)
        return coordinator.status()

    @app.post("/api/admin/server-worker-sync/run")
    async def server_worker_sync_run(request: Request) -> dict[str, Any]:
        admin(request)
        return await coordinator.run_once()

    async def startup() -> None:
        task = getattr(app.state, "server_worker_sync_task", None)
        if task is None or task.done():
            app.state.server_worker_sync_task = asyncio.create_task(
                coordinator.loop(),
                name="chat2api-server-worker-auto-sync",
            )

    async def shutdown() -> None:
        task = getattr(app.state, "server_worker_sync_task", None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app.state.server_worker_sync_task = None

    app.add_event_handler("startup", startup)
    app.add_event_handler("shutdown", shutdown)
    return app
