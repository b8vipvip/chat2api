from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


WORKER_STATES = frozenset({"installing", "enrolling", "waiting_proxy", "proxy_checking", "waiting_login", "login_checking", "ready", "degraded", "offline", "error"})
ALLOWED_COMMANDS = frozenset({"health_check", "restart_chrome", "restart_xray", "restart_xvfb", "reload_extension", "test_proxy", "apply_proxy_config", "open_login_session", "close_login_session", "get_logs", "reconcile_reserve_pool"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class LinuxWorkerStore:
    """Durable worker identities; only credential hashes reach disk."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "linux_workers.json"
        self._lock = threading.RLock()
        self.data: dict[str, Any] = {"workers": {}, "enrollments": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(self.path)

    def create_enrollment(self, name: str, ttl_minutes: int = 30) -> dict[str, Any]:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
        now = utcnow()
        item = {"code_hash": token_hash(code), "name": str(name).strip()[:80] or "Linux Worker", "created_at": iso(now), "expires_at": iso(now + timedelta(minutes=max(1, min(ttl_minutes, 1440)))), "used_at": None}
        with self._lock:
            self.data["enrollments"][item["code_hash"]] = item
            self._save()
        return {**item, "code": code, "code_hash": None}

    def enroll(self, code: str, facts: dict[str, Any]) -> dict[str, str]:
        digest = token_hash(str(code).strip().upper())
        with self._lock:
            item = self.data["enrollments"].get(digest)
            if not item or item.get("used_at"):
                raise ValueError("Invalid or already used enrollment code")
            if datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00")) <= utcnow():
                raise ValueError("Enrollment code has expired")
            worker_id = "wrk_" + secrets.token_hex(12)
            token = "wkt_" + secrets.token_urlsafe(32)
            now = iso(utcnow())
            worker = {"worker_id": worker_id, "name": item["name"], "token_hash": token_hash(token), "revoked_at": None, "created_at": now, "last_seen_at": None, "status": "enrolling", "network_status": "unknown", "proxy_status": "waiting", "chatgpt_status": "waiting_login", "metadata": {}, **{k: str(facts.get(k) or "")[:200] for k in ("device_id", "hostname", "platform", "arch", "os_version", "agent_version", "chrome_bridge_version")}}
            self.data["workers"][worker_id] = worker
            item["used_at"] = now
            item["worker_id"] = worker_id
            self._save()
        return {"worker_id": worker_id, "worker_token": token}

    def authenticate(self, worker_id: str, token: str) -> bool:
        worker = self.data["workers"].get(worker_id)
        return bool(worker and not worker.get("revoked_at") and secrets.compare_digest(worker.get("token_hash", ""), token_hash(token)))

    def heartbeat(self, worker_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            worker = self.data["workers"][worker_id]
            requested = str(payload.get("status") or worker["status"])
            worker["status"] = requested if requested in WORKER_STATES else "degraded"
            worker["last_seen_at"] = iso(utcnow())
            for key in ("hostname", "platform", "arch", "os_version", "agent_version", "chrome_bridge_version", "network_status", "proxy_status", "chatgpt_status", "extension_client_id"):
                if key in payload:
                    worker[key] = str(payload[key])[:200]
            worker["metadata"] = dict(payload.get("metadata") or {})
            self._save()
            return self.public(worker)

    def record_proxy_success(self, worker_id: str, summary: dict[str, Any]) -> dict[str, Any]:
        """Persist only non-secret proxy facts after the Worker reports success."""
        safe = {
            "protocol": str(summary.get("protocol") or "")[:32],
            "server": str(summary.get("server") or "")[:200],
            "port": int(summary.get("port") or 0),
            "transport": str(summary.get("transport") or "")[:32],
            "security": str(summary.get("security") or "")[:64],
        }
        with self._lock:
            worker = self.data["workers"][worker_id]
            worker["proxy_status"] = "connected"
            if worker.get("status") in {"installing", "enrolling", "waiting_proxy", "proxy_checking", "degraded"}:
                worker["status"] = "waiting_login"
            metadata = dict(worker.get("metadata") or {})
            metadata["proxy_summary"] = safe
            worker["metadata"] = metadata
            self._save()
            return self.public(worker)

    def revoke(self, worker_id: str) -> None:
        with self._lock:
            self.data["workers"][worker_id]["revoked_at"] = iso(utcnow())
            self.data["workers"][worker_id]["status"] = "offline"
            self._save()

    @staticmethod
    def public(worker: dict[str, Any]) -> dict[str, Any]:
        def clean(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: ("***" if any(word in k.lower() for word in ("secret", "token", "password", "credential", "proxy_config")) else clean(v)) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value
        return clean({k: v for k, v in worker.items() if k != "token_hash"})

    def list_public(self) -> list[dict[str, Any]]:
        return [self.public(x) for x in sorted(self.data["workers"].values(), key=lambda x: x["created_at"], reverse=True)]
