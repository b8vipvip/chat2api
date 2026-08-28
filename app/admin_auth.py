from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


SESSION_COOKIE = "chat2api_admin_session"
SESSION_STORE_VERSION = 1
SESSION_STORE_NAME = "admin_sessions.json"
logger = logging.getLogger("chat2api.admin_auth")


@dataclass(slots=True)
class AdminSession:
    token_hash: str
    expires_at: float


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _default_storage_path() -> Path | None:
    """Return the durable session path for production deployments.

    CHAT2API_DATA_DIR is authoritative when configured. The official Docker
    deployment mounts ./data at /app/data, so use that persisted volume when the
    setting is left at its default. Non-Docker/local test processes stay in-memory
    unless they explicitly opt into persistence.
    """

    configured = str(os.getenv("CHAT2API_DATA_DIR") or "").strip()
    if configured:
        return Path(configured) / SESSION_STORE_NAME
    docker_data = Path("/app/data")
    if Path("/.dockerenv").exists() and docker_data.is_dir():
        return docker_data / SESSION_STORE_NAME
    return None


class AdminSessionStore:
    """Administrator sessions with restart-safe, hash-only persistence.

    The administrator credential is intentionally separate from business API
    keys. Browser cookies still contain the high-entropy bearer token, while the
    durable data volume stores only SHA-256 token fingerprints and expiry times.
    This lets Docker container replacement preserve a valid console login without
    making the raw session token recoverable from admin_sessions.json.
    """

    def __init__(
        self,
        username: str,
        password: str,
        ttl_seconds: int = 24 * 3600,
        storage_path: Path | str | None = None,
    ) -> None:
        self.username = str(username or "admin")
        self.password = str(password or "")
        self.ttl_seconds = max(900, int(ttl_seconds))
        resolved = Path(storage_path) if storage_path is not None else _default_storage_path()
        self.storage_path: Path | None = resolved
        self.sessions: dict[str, AdminSession] = {}
        self.last_persistence_error = ""
        if self.storage_path is not None:
            self._load()
            self.cleanup()

    def verify_credentials(self, username: str, password: str) -> bool:
        return secrets.compare_digest(str(username or ""), self.username) and secrets.compare_digest(
            str(password or ""), self.password
        )

    def create(self) -> str:
        self.cleanup()
        token = secrets.token_urlsafe(40)
        digest = _token_hash(token)
        self.sessions[digest] = AdminSession(token_hash=digest, expires_at=time.time() + self.ttl_seconds)
        self._save()
        return token

    def authenticate(self, token: str | None) -> bool:
        if not token:
            return False
        digest = _token_hash(str(token))
        item = self.sessions.get(digest)
        if not item:
            return False
        if item.expires_at <= time.time():
            self.sessions.pop(digest, None)
            self._save()
            return False
        return True

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        digest = _token_hash(str(token))
        if self.sessions.pop(digest, None) is not None:
            self._save()

    def cleanup(self) -> None:
        now = time.time()
        changed = False
        for digest, item in list(self.sessions.items()):
            if item.expires_at <= now:
                self.sessions.pop(digest, None)
                changed = True
        if changed:
            self._save()

    def enable_persistence(self, storage_path: Path | str) -> None:
        """Enable durable storage explicitly and merge any live sessions into it."""

        live = dict(self.sessions)
        self.storage_path = Path(storage_path)
        self.sessions = {}
        self._load()
        for digest, item in live.items():
            existing = self.sessions.get(digest)
            if existing is None or item.expires_at > existing.expires_at:
                self.sessions[digest] = item
        self.cleanup()
        self._save()

    def _load(self) -> None:
        path = self.storage_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("sessions") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                return
            loaded: dict[str, AdminSession] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                digest = str(row.get("token_hash") or "").strip().lower()
                try:
                    expires_at = float(row.get("expires_at") or 0)
                except (TypeError, ValueError):
                    continue
                if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                    continue
                if expires_at <= time.time():
                    continue
                loaded[digest] = AdminSession(token_hash=digest, expires_at=expires_at)
            self.sessions.update(loaded)
            self.last_persistence_error = ""
        except (OSError, json.JSONDecodeError) as exc:
            self.last_persistence_error = str(exc)
            logger.warning("Unable to load persisted administrator sessions from %s: %s", path, exc)

    def _save(self) -> None:
        path = self.storage_path
        if path is None:
            return
        payload = {
            "version": SESSION_STORE_VERSION,
            "sessions": [
                {"token_hash": item.token_hash, "expires_at": item.expires_at}
                for item in sorted(self.sessions.values(), key=lambda value: value.expires_at)
            ],
        }
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            self.last_persistence_error = ""
        except OSError as exc:
            self.last_persistence_error = str(exc)
            logger.warning("Unable to persist administrator sessions to %s: %s", path, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
