from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Callable


LOGIN_SESSION_IDLE_SECONDS = 20 * 60


def ticket_hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


@dataclass
class LoginSession:
    worker_id: str
    expires_at: float
    baseline_login_checked_at_ms: int = 0
    last_login_checked_at_ms: int = 0
    saw_login_required: bool = False

    def observe_login(self, *, checked_at_ms: int, state: str, composer_ready: bool) -> bool:
        """Return true only for a fresh login-required -> ready transition.

        Persisted Worker telemetry can still say ``ready`` when a remote login
        session starts. That stale value must never close the operator's browser
        before the first frame is shown. A session therefore ignores telemetry
        at or before its baseline, requires a fresh login-required observation,
        and only then accepts a newer ready+composer observation as completion.
        """
        try:
            checked = max(0, int(checked_at_ms or 0))
        except (TypeError, ValueError):
            checked = 0
        if checked <= max(self.baseline_login_checked_at_ms, self.last_login_checked_at_ms):
            return False
        self.last_login_checked_at_ms = checked
        normalized = str(state or "").strip().lower()
        if normalized == "login_required":
            self.saw_login_required = True
            return False
        return normalized in {"ready", "logged_in", "authenticated"} and bool(composer_ready) and self.saw_login_required


class LoginSessionStore:
    """In-memory, single-use-capability sessions for remote browser control.

    Raw tickets are returned once and never persisted. A Worker may have only
    one active remote-control session so two admin tabs cannot fight for input.
    """

    def __init__(self, *, now: Callable[[], float] = time.monotonic, idle_seconds: int = LOGIN_SESSION_IDLE_SECONDS) -> None:
        self._now = now
        self.idle_seconds = max(60, int(idle_seconds))
        self._sessions: dict[str, LoginSession] = {}
        self._worker_keys: dict[str, str] = {}

    def _prune(self) -> None:
        now = self._now()
        for key, session in list(self._sessions.items()):
            if session.expires_at <= now:
                self._sessions.pop(key, None)
                if self._worker_keys.get(session.worker_id) == key:
                    self._worker_keys.pop(session.worker_id, None)

    def issue(self, worker_id: str, *, baseline_login_checked_at_ms: int = 0) -> str:
        self._prune()
        old_key = self._worker_keys.pop(worker_id, None)
        if old_key:
            self._sessions.pop(old_key, None)
        ticket = "lgn_" + secrets.token_urlsafe(32)
        key = ticket_hash(ticket)
        try:
            baseline = max(0, int(baseline_login_checked_at_ms or 0))
        except (TypeError, ValueError):
            baseline = 0
        self._sessions[key] = LoginSession(
            worker_id=worker_id,
            expires_at=self._now() + self.idle_seconds,
            baseline_login_checked_at_ms=baseline,
            last_login_checked_at_ms=baseline,
        )
        self._worker_keys[worker_id] = key
        return ticket

    def require(self, worker_id: str, ticket: str, *, touch: bool = True) -> LoginSession:
        self._prune()
        key = ticket_hash(str(ticket or ""))
        session = self._sessions.get(key)
        if not session or session.worker_id != worker_id:
            raise KeyError("invalid_login_session")
        if touch:
            session.expires_at = self._now() + self.idle_seconds
        return session

    def revoke(self, worker_id: str, ticket: str | None = None) -> bool:
        self._prune()
        if ticket:
            key = ticket_hash(ticket)
            session = self._sessions.get(key)
            if not session or session.worker_id != worker_id:
                return False
        else:
            key = self._worker_keys.get(worker_id, "")
            if not key:
                return False
            session = self._sessions.get(key)
        self._sessions.pop(key, None)
        if session and self._worker_keys.get(worker_id) == key:
            self._worker_keys.pop(worker_id, None)
        return True

    def has_worker_session(self, worker_id: str) -> bool:
        self._prune()
        return worker_id in self._worker_keys
