from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


WORKER_STATES = frozenset({"installing", "enrolling", "waiting_proxy", "proxy_checking", "waiting_login", "login_checking", "ready", "degraded", "offline", "error"})
ALLOWED_COMMANDS = frozenset({
    "health_check",
    "restart_chrome",
    "restart_xray",
    "restart_xvfb",
    "reload_extension",
    "test_proxy",
    "apply_proxy_config",
    "open_login_session",
    "close_login_session",
    "login_session_frame",
    "login_session_input",
    "get_logs",
    "reconcile_reserve_pool",
})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _safe_text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


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

    @staticmethod
    def _bridge(worker: dict[str, Any]) -> dict[str, Any]:
        metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
        bridge = metadata.get("bridge") if isinstance(metadata.get("bridge"), dict) else {}
        return dict(bridge)

    @classmethod
    def _derive_status(cls, worker: dict[str, Any], *, fallback: str | None = None) -> str:
        if worker.get("revoked_at"):
            worker["chatgpt_status"] = "offline"
            return "offline"

        metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
        services = metadata.get("services") if isinstance(metadata.get("services"), dict) else {}
        if services and any(services.get(name) is False for name in ("xray", "xvfb", "chrome")):
            worker["chatgpt_status"] = "unavailable"
            return "degraded"

        proxy_status = _safe_text(worker.get("proxy_status"), 40).lower()
        if proxy_status not in {"connected", "ready"}:
            if proxy_status in {"error", "failed", "offline"}:
                return "degraded"
            return "waiting_proxy"

        client_id = _safe_text(worker.get("extension_client_id"), 180)
        if not client_id:
            worker["chatgpt_status"] = "waiting_login"
            return "waiting_login"

        bridge = cls._bridge(worker)
        if bridge and bridge.get("client_id") and str(bridge.get("client_id")) != client_id:
            worker["chatgpt_status"] = "binding_mismatch"
            return "degraded"
        if bridge.get("connection_enabled") is False:
            worker["chatgpt_status"] = "disabled"
            return "degraded"
        if bridge.get("online") is False:
            worker["chatgpt_status"] = "offline"
            return "degraded"

        login_state = _safe_text(bridge.get("login_state"), 40).lower()
        composer_ready = bridge.get("composer_ready") is True
        if login_state == "ready" and composer_ready:
            worker["chatgpt_status"] = "ready"
            return "ready"
        if login_state == "login_required":
            worker["chatgpt_status"] = "login_required"
            return "waiting_login"
        if login_state in {"checking", "unknown", ""}:
            worker["chatgpt_status"] = login_state or "checking"
            return "login_checking"

        worker["chatgpt_status"] = login_state[:80] or "checking"
        return fallback if fallback in WORKER_STATES else "login_checking"

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
            worker = {"worker_id": worker_id, "name": item["name"], "token_hash": token_hash(token), "revoked_at": None, "created_at": now, "last_seen_at": None, "status": "enrolling", "network_status": "unknown", "proxy_status": "waiting", "chatgpt_status": "waiting_login", "extension_client_id": "", "extension_device_id": "", "metadata": {}, **{k: str(facts.get(k) or "")[:200] for k in ("device_id", "hostname", "platform", "arch", "os_version", "agent_version", "chrome_bridge_version")}}
            self.data["workers"][worker_id] = worker
            item["used_at"] = now
            item["worker_id"] = worker_id
            self._save()
        return {"worker_id": worker_id, "worker_token": token}

    def authenticate(self, worker_id: str, token: str) -> bool:
        worker = self.data["workers"].get(worker_id)
        return bool(worker and not worker.get("revoked_at") and secrets.compare_digest(worker.get("token_hash", ""), token_hash(token)))

    def heartbeat(self, worker_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Merge host heartbeat data without erasing authoritative bridge telemetry."""
        with self._lock:
            worker = self.data["workers"][worker_id]
            requested = str(payload.get("status") or worker.get("status") or "degraded")
            requested = requested if requested in WORKER_STATES else "degraded"
            worker["last_seen_at"] = iso(utcnow())

            for key in ("hostname", "platform", "arch", "os_version", "agent_version"):
                if key in payload:
                    worker[key] = _safe_text(payload[key])
            if "proxy_status" in payload:
                worker["proxy_status"] = _safe_text(payload["proxy_status"], 80)
            # Network status reported directly by the Agent is host-level only. Once
            # a bridge is bound, the browser network probe below is authoritative.
            if "network_status" in payload and not worker.get("extension_client_id"):
                worker["network_status"] = _safe_text(payload["network_status"], 80)

            incoming = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
            existing = dict(worker.get("metadata") or {}) if isinstance(worker.get("metadata"), dict) else {}
            bridge = existing.get("bridge") if isinstance(existing.get("bridge"), dict) else None
            existing.update(incoming)
            if bridge is not None:
                existing["bridge"] = bridge
            worker["metadata"] = existing
            worker["status"] = self._derive_status(worker, fallback=requested)
            self._save()
            return self.public(worker)

    def bind_extension(self, worker_id: str, client_id: str, device_id: str) -> dict[str, Any]:
        client_id = _safe_text(client_id, 180)
        device_id = _safe_text(device_id, 200)
        if not client_id or len(device_id) < 8:
            raise ValueError("Extension client_id and device_id are required")
        with self._lock:
            worker = self.data["workers"].get(worker_id)
            if not worker or worker.get("revoked_at"):
                raise ValueError("Worker is unavailable")
            for other_id, other in self.data["workers"].items():
                if other_id == worker_id or other.get("revoked_at"):
                    continue
                if _safe_text(other.get("extension_client_id"), 180) == client_id:
                    raise ValueError("Extension is already bound to another active Linux Worker")
            current = _safe_text(worker.get("extension_client_id"), 180)
            if current and current != client_id:
                raise ValueError("Linux Worker is already bound to another extension")
            worker["extension_client_id"] = client_id
            worker["extension_device_id"] = device_id
            metadata = dict(worker.get("metadata") or {})
            bridge = dict(metadata.get("bridge") or {}) if isinstance(metadata.get("bridge"), dict) else {}
            bridge.update({"client_id": client_id, "device_id": device_id})
            metadata["bridge"] = bridge
            worker["metadata"] = metadata
            worker["status"] = self._derive_status(worker, fallback="login_checking")
            self._save()
            return self.public(worker)

    def worker_for_extension(self, client_id: str) -> dict[str, Any] | None:
        target = _safe_text(client_id, 180)
        if not target:
            return None
        with self._lock:
            for worker in self.data["workers"].values():
                if worker.get("revoked_at"):
                    continue
                if _safe_text(worker.get("extension_client_id"), 180) == target:
                    return worker
        return None

    def clear_extension_binding(self, worker_id: str, *, expected_client_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            worker = self.data["workers"].get(worker_id)
            if not worker:
                return None
            current = _safe_text(worker.get("extension_client_id"), 180)
            if expected_client_id is not None and current != _safe_text(expected_client_id, 180):
                return self.public(worker)
            worker["extension_client_id"] = ""
            worker["extension_device_id"] = ""
            metadata = dict(worker.get("metadata") or {})
            metadata.pop("bridge", None)
            worker["metadata"] = metadata
            if not worker.get("revoked_at"):
                worker["status"] = self._derive_status(worker, fallback="waiting_login")
            self._save()
            return self.public(worker)

    def clear_extension_binding_by_client(self, client_id: str) -> None:
        target = _safe_text(client_id, 180)
        if not target:
            return
        with self._lock:
            changed = False
            for worker in self.data["workers"].values():
                if _safe_text(worker.get("extension_client_id"), 180) != target:
                    continue
                worker["extension_client_id"] = ""
                worker["extension_device_id"] = ""
                metadata = dict(worker.get("metadata") or {})
                metadata.pop("bridge", None)
                worker["metadata"] = metadata
                if not worker.get("revoked_at"):
                    worker["status"] = self._derive_status(worker, fallback="waiting_login")
                changed = True
            if changed:
                self._save()

    def record_extension_status(self, worker_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Store a sanitized projection of authoritative Chrome Bridge telemetry."""
        raw = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
        bridge = {
            "client_id": _safe_text(snapshot.get("client_id"), 180),
            "device_id": _safe_text(snapshot.get("device_id"), 200),
            "online": snapshot.get("online") is True,
            "connection_enabled": snapshot.get("connection_enabled") is not False,
            "extension_version": _safe_text(snapshot.get("version") or raw.get("extension_version"), 40),
            "login_state": _safe_text(raw.get("chatgpt_login_state"), 40) or "unknown",
            "composer_ready": raw.get("chatgpt_login_composer_ready") is True,
            "login_confidence": _safe_text(raw.get("chatgpt_login_confidence"), 40),
            "login_strategy": _safe_text(raw.get("chatgpt_login_strategy"), 160),
            "login_checked_at_ms": int(raw.get("chatgpt_login_checked_at_ms") or 0),
            "network_probe_status": _safe_text(raw.get("network_probe_status"), 60) or "unknown",
            "network_country_code": _safe_text(raw.get("network_country_code"), 12),
            "network_probe_error": _safe_text(raw.get("network_probe_error"), 160),
            "account_type": _safe_text(raw.get("account_type"), 40) or "unknown",
            "reserve_window_total": max(0, int(raw.get("reserve_window_total") or 0)),
            "reserve_window_active": max(0, int(raw.get("reserve_window_active") or 0)),
            "reserve_window_target": max(0, int(raw.get("reserve_window_target") or 0)),
            "reserve_window_idle_close_seconds": max(0, int(raw.get("reserve_window_idle_close_seconds") or 0)),
        }
        with self._lock:
            worker = self.data["workers"].get(worker_id)
            if not worker or worker.get("revoked_at"):
                raise ValueError("Worker is unavailable")
            expected = _safe_text(worker.get("extension_client_id"), 180)
            if expected and bridge["client_id"] != expected:
                raise ValueError("Extension status does not match Worker binding")
            if not expected:
                raise ValueError("Worker has no bound extension")
            metadata = dict(worker.get("metadata") or {})
            metadata["bridge"] = bridge
            worker["metadata"] = metadata
            worker["extension_device_id"] = bridge["device_id"] or _safe_text(worker.get("extension_device_id"), 200)
            worker["chrome_bridge_version"] = bridge["extension_version"]
            worker["network_status"] = bridge["network_probe_status"]
            worker["status"] = self._derive_status(worker, fallback=worker.get("status"))
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
            metadata = dict(worker.get("metadata") or {})
            metadata["proxy_summary"] = safe
            worker["metadata"] = metadata
            worker["status"] = self._derive_status(worker, fallback="waiting_login")
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
