from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


SESSION_COOKIE = "chat2api_admin_session"


@dataclass(slots=True)
class AdminSession:
    token: str
    expires_at: float


class AdminSessionStore:
    """Small in-memory administrator session store.

    The administrator credential is intentionally separate from business API keys.
    Sessions are invalidated on server restart and are transported only through an
    HttpOnly cookie.
    """

    def __init__(self, username: str, password: str, ttl_seconds: int = 24 * 3600) -> None:
        self.username = str(username or "admin")
        self.password = str(password or "")
        self.ttl_seconds = max(900, int(ttl_seconds))
        self.sessions: dict[str, AdminSession] = {}

    def verify_credentials(self, username: str, password: str) -> bool:
        return secrets.compare_digest(str(username or ""), self.username) and secrets.compare_digest(
            str(password or ""), self.password
        )

    def create(self) -> str:
        self.cleanup()
        token = secrets.token_urlsafe(40)
        self.sessions[token] = AdminSession(token=token, expires_at=time.time() + self.ttl_seconds)
        return token

    def authenticate(self, token: str | None) -> bool:
        if not token:
            return False
        item = self.sessions.get(str(token))
        if not item:
            return False
        if item.expires_at <= time.time():
            self.sessions.pop(str(token), None)
            return False
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self.sessions.pop(str(token), None)

    def cleanup(self) -> None:
        now = time.time()
        for token, item in list(self.sessions.items()):
            if item.expires_at <= now:
                self.sessions.pop(token, None)
