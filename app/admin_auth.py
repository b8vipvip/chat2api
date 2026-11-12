from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def same_origin_admin_request(request) -> bool:
    """Require a browser mutation to originate from this exact admin origin.

    X-Forwarded-Proto is consulted only for the scheme because deployments may
    terminate TLS at a reverse proxy. The browser-supplied Origin host must still
    match the request Host exactly; Fetch Metadata, when present, must say
    same-origin. This helper does not make arbitrary forwarded hosts trusted.
    """
    origin = str(request.headers.get("origin") or "").strip()
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    request_host = str(request.headers.get("host") or "").strip().lower()
    forwarded_proto = (
        str(request.headers.get("x-forwarded-proto") or "")
        .split(",", 1)[0]
        .strip()
        .lower()
    )
    expected_scheme = (
        forwarded_proto
        if forwarded_proto in {"http", "https"}
        else str(request.url.scheme or "").lower()
    )
    fetch_site = str(request.headers.get("sec-fetch-site") or "").strip().lower()
    return bool(
        parsed.scheme.lower() == expected_scheme
        and parsed.scheme.lower() in {"http", "https"}
        and parsed.netloc
        and parsed.netloc.lower() == request_host
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and fetch_site in {"", "same-origin"}
    )


class AdminLoginRateLimiter:
    """Bound repeated admin login failures per username/IP pair and per IP.

    Process-local by design; multi-instance enforcement requires a shared
    transactional limiter and is a production/D-P gate.
    """

    def __init__(
        self,
        *,
        per_identity_limit: int = 5,
        per_ip_limit: int = 20,
        window_seconds: int = 900,
    ) -> None:
        self.per_identity_limit = max(1, int(per_identity_limit))
        self.per_ip_limit = max(1, int(per_ip_limit))
        self.window_seconds = max(1, int(window_seconds))
        self.lock = threading.RLock()
        self.failures: dict[str, list[float]] = {}

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()

    def _keys(self, username: str, remote_addr: str) -> tuple[str, str]:
        ip = str(remote_addr or "unknown").strip()[:128]
        user = str(username or "").strip().casefold()[:128]
        return (
            "identity:" + self._digest(user),
            "ip:" + self._digest(ip),
        )

    def retry_after(self, username: str, remote_addr: str) -> int:
        now = time.monotonic()
        identity_key, ip_key = self._keys(username, remote_addr)
        with self.lock:
            for key, limit in (
                (identity_key, self.per_identity_limit),
                (ip_key, self.per_ip_limit),
            ):
                values = [stamp for stamp in self.failures.get(key, []) if now - stamp < self.window_seconds]
                if values:
                    self.failures[key] = values
                else:
                    self.failures.pop(key, None)
                if len(values) >= limit:
                    return max(1, int(self.window_seconds - (now - values[0]) + 0.999))
        return 0

    def record_failure(self, username: str, remote_addr: str) -> None:
        now = time.monotonic()
        identity_key, ip_key = self._keys(username, remote_addr)
        with self.lock:
            for key in (identity_key, ip_key):
                values = [stamp for stamp in self.failures.get(key, []) if now - stamp < self.window_seconds]
                values.append(now)
                self.failures[key] = values
            if len(self.failures) > 10_000:
                cutoff = now - self.window_seconds
                for key, values in list(self.failures.items()):
                    kept = [stamp for stamp in values if stamp > cutoff]
                    if kept:
                        self.failures[key] = kept
                    else:
                        self.failures.pop(key, None)
                while len(self.failures) > 10_000:
                    self.failures.pop(next(iter(self.failures)))

    def record_success(self, username: str, remote_addr: str) -> None:
        identity_key, _ip_key = self._keys(username, remote_addr)
        with self.lock:
            # A successful account login must not erase the source-IP budget
            # for attempts against other administrator identities.
            self.failures.pop(identity_key, None)


SESSION_COOKIE = "chat2api_admin_session"
SESSION_STORE_VERSION = 1
SESSION_STORE_NAME = "admin_sessions.json"
logger = logging.getLogger("chat2api.admin_auth")


@dataclass(slots=True)
class AdminSession:
    token_hash: str
    expires_at: float
    username: str = ""


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

    def create(self, username: str | None = None) -> str:
        self.cleanup()
        token = secrets.token_urlsafe(40)
        digest = _token_hash(token)
        self.sessions[digest] = AdminSession(token_hash=digest, expires_at=time.time() + self.ttl_seconds, username=str(username or self.username))
        self._save()
        return token

    def authenticate(self, token: str | None) -> bool:
        return self.identity(token) is not None

    def identity(self, token: str | None) -> str | None:
        if not token:
            return None
        digest = _token_hash(str(token))
        item = self.sessions.get(digest)
        if not item:
            return None
        if item.expires_at <= time.time():
            self.sessions.pop(digest, None)
            self._save()
            return None
        # Historical persisted v1 sessions lack username and belong to the
        # configured root administrator only, never to delegated users.
        return item.username or self.username

    def revoke_username(self, username: str) -> int:
        count = 0
        for digest, item in list(self.sessions.items()):
            if (item.username or self.username).casefold() == str(username).casefold():
                del self.sessions[digest]
                count += 1
        if count:
            self._save()
        return count

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
                loaded[digest] = AdminSession(token_hash=digest, expires_at=expires_at, username=str(row.get("username") or self.username))
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
                {"token_hash": item.token_hash, "expires_at": item.expires_at, "username": item.username or self.username}
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
