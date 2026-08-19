from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INSTALL_STATES = frozenset({"pending", "installing", "enrolling", "installed", "failed", "disabled"})
TERMINAL_STATES = frozenset({"installed", "failed"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


class LinuxWorkerInstallStore:
    """Persistent pre-enrollment install records and progress.

    The raw enrollment code is intentionally retained in this root-owned 0600
    data file because the administrator asked for the install command to remain
    copyable after page refresh. It is only returned by administrator endpoints;
    public/worker APIs validate the SHA-256 digest instead.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "linux_worker_installs.json"
        self._lock = threading.RLock()
        self.data: dict[str, Any] = {"installs": {}}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("installs"), dict):
                self.data = loaded

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(self.path)

    def _by_code_locked(self, code: str) -> dict[str, Any] | None:
        digest = _hash(str(code or "").strip().upper())
        for item in self.data["installs"].values():
            if secrets.compare_digest(str(item.get("code_hash") or ""), digest):
                return item
        return None

    @staticmethod
    def _event(item: dict[str, Any], stage: str, state: str, message: str) -> None:
        history = item.setdefault("history", [])
        history.append({
            "at": _iso(),
            "stage": _text(stage, 80),
            "state": _text(state, 40),
            "message": _text(message, 500),
        })
        if len(history) > 80:
            del history[:-80]

    def create(self, name: str) -> dict[str, Any]:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
        now = _iso()
        install_id = "lwi_" + secrets.token_hex(10)
        item = {
            "install_id": install_id,
            "name": _text(name, 80) or "Linux Worker",
            "code": code,
            "code_hash": _hash(code),
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "failed_at": None,
            "consumed_at": None,
            "worker_id": None,
            "enabled": True,
            "state": "pending",
            "stage": "waiting",
            "message": "等待在目标服务器执行安装命令",
            "hostname": "",
            "os_version": "",
            "arch": "",
            "history": [],
        }
        self._event(item, "waiting", "pending", item["message"])
        with self._lock:
            self.data["installs"][install_id] = item
            self._save()
        return dict(item)

    def admin_public(self, item: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in item.items() if k != "code_hash"}

    def list_admin(self) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self.data["installs"].values(), key=lambda x: str(x.get("created_at") or ""), reverse=True)
            return [self.admin_public(dict(item)) for item in items]

    def get(self, install_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self.data["installs"].get(install_id)
            return self.admin_public(dict(item)) if item else None

    def record_progress(
        self,
        code: str,
        *,
        stage: str,
        state: str,
        message: str,
        facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = _text(state, 40).lower()
        if state not in INSTALL_STATES:
            state = "installing"
        with self._lock:
            item = self._by_code_locked(code)
            if not item:
                raise ValueError("Unknown install command")
            if not item.get("enabled") and state not in TERMINAL_STATES:
                raise ValueError("Install command is disabled")
            if item.get("state") == "installed":
                return self.admin_public(dict(item))

            now = _iso()
            if not item.get("started_at") and state in {"installing", "enrolling"}:
                item["started_at"] = now
            item["updated_at"] = now
            item["stage"] = _text(stage, 80)
            item["message"] = _text(message, 500)
            item["state"] = state
            if facts:
                for key in ("hostname", "os_version", "arch"):
                    if key in facts:
                        item[key] = _text(facts.get(key), 200)
            if state == "failed":
                item["enabled"] = False
                item["failed_at"] = now
            elif state == "installed":
                item["enabled"] = False
                item["completed_at"] = now
            self._event(item, item["stage"], state, item["message"])
            self._save()
            return self.admin_public(dict(item))

    def begin_enrollment(self, code: str) -> dict[str, Any]:
        with self._lock:
            item = self._by_code_locked(code)
            if not item:
                raise ValueError("Invalid install command")
            if not item.get("enabled"):
                raise ValueError("Install command is disabled")
            if item.get("consumed_at") or item.get("worker_id"):
                raise ValueError("Install command has already been consumed")
            item["state"] = "enrolling"
            item["stage"] = "enrollment"
            item["message"] = "正在创建 Worker 身份"
            item["updated_at"] = _iso()
            self._event(item, "enrollment", "enrolling", item["message"])
            self._save()
            return self.admin_public(dict(item))

    def finish_enrollment(self, code: str, worker_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._by_code_locked(code)
            if not item:
                raise ValueError("Invalid install command")
            now = _iso()
            item["consumed_at"] = now
            item["worker_id"] = _text(worker_id, 100)
            item["state"] = "installing"
            item["stage"] = "post-enrollment"
            item["message"] = "Worker 身份已创建，正在安装并启动服务"
            item["updated_at"] = now
            self._event(item, item["stage"], item["state"], item["message"])
            self._save()
            return self.admin_public(dict(item))

    def mark_failed(self, code: str, stage: str, message: str) -> dict[str, Any] | None:
        try:
            return self.record_progress(code, stage=stage, state="failed", message=message)
        except ValueError:
            return None

    def update(self, install_id: str, *, name: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
        with self._lock:
            item = self.data["installs"].get(install_id)
            if not item:
                raise KeyError(install_id)
            if name is not None:
                item["name"] = _text(name, 80) or item["name"]
            if enabled is not None:
                if enabled and item.get("consumed_at"):
                    raise ValueError("Consumed install commands cannot be re-enabled")
                item["enabled"] = bool(enabled)
                if enabled:
                    item["state"] = "pending"
                    item["stage"] = "waiting"
                    item["message"] = "安装命令已重新启用，等待执行"
                    item["failed_at"] = None
                elif item.get("state") not in TERMINAL_STATES:
                    item["state"] = "disabled"
                    item["stage"] = "disabled"
                    item["message"] = "安装命令已由管理员停用"
                self._event(item, item["stage"], item["state"], item["message"])
            item["updated_at"] = _iso()
            self._save()
            return self.admin_public(dict(item))

    def delete(self, install_id: str) -> bool:
        with self._lock:
            if install_id not in self.data["installs"]:
                return False
            del self.data["installs"][install_id]
            self._save()
            return True
