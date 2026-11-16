"""Single-use, email-bound registration invitations (C-04).

Only SHA256 hashes of high-entropy invitation codes are persisted. The store
uses SQLite transactions so two concurrent registrations cannot reserve the
same invitation. Reserved invitations fail closed after an interrupted
registration and require a new admin-issued invitation.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any


EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+$")
MIN_TTL_SECONDS = 300
MAX_TTL_SECONDS = 30 * 24 * 60 * 60


class InvitationError(ValueError):
    pass


class InvitationStore:
    def __init__(self, data_dir: Path | str) -> None:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "registration_invitations_v1.sqlite3"
        if self.path.is_symlink():
            raise InvitationError("Invitation database cannot be a symbolic link")
        self.lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _init_db(self) -> None:
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS invitations (
                        invitation_id TEXT PRIMARY KEY,
                        email TEXT NOT NULL,
                        token_hash TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL CHECK(status IN
                            ('issued','reserved','consumed','revoked','expired')),
                        created_by TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL,
                        reservation_id TEXT UNIQUE,
                        reserved_at INTEGER,
                        consumed_at INTEGER,
                        revoked_at INTEGER
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS invitations_created_idx ON invitations(created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS invitations_status_idx ON invitations(status, expires_at)")
            finally:
                conn.close()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _email(value: str) -> str:
        email = str(value or "").strip().casefold()
        if len(email) > 254 or not EMAIL_RE.fullmatch(email):
            raise InvitationError("A valid email address is required")
        return email

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        now = int(time.time())
        status = str(row["status"])
        if status == "issued" and int(row["expires_at"]) <= now:
            status = "expired"
        return {
            "invitation_id": str(row["invitation_id"]),
            "email": str(row["email"]),
            "status": status,
            "created_by": str(row["created_by"]),
            "created_at": int(row["created_at"]),
            "expires_at": int(row["expires_at"]),
            "reserved_at": row["reserved_at"],
            "consumed_at": row["consumed_at"],
            "revoked_at": row["revoked_at"],
        }

    def create(
        self,
        email: str,
        created_by: str,
        *,
        expires_in_seconds: int = 7 * 24 * 60 * 60,
    ) -> dict[str, Any]:
        normalized_email = self._email(email)
        actor = str(created_by or "").strip()[:128]
        if not actor:
            raise InvitationError("Invitation creator is required")
        if type(expires_in_seconds) is not int or not MIN_TTL_SECONDS <= expires_in_seconds <= MAX_TTL_SECONDS:
            raise InvitationError("Invitation expiry must be between 5 minutes and 30 days")
        code = secrets.token_urlsafe(32)
        now = int(time.time())
        invitation_id = "inv_" + uuid.uuid4().hex
        digest = hashlib.sha256(code.encode("ascii")).hexdigest()
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """INSERT INTO invitations
                       (invitation_id,email,token_hash,status,created_by,created_at,expires_at)
                       VALUES (?,?,?,'issued',?,?,?)""",
                    (invitation_id, normalized_email, digest, actor, now, now + expires_in_seconds),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return {
            "invitation": {
                "invitation_id": invitation_id,
                "email": normalized_email,
                "status": "issued",
                "created_by": actor,
                "created_at": now,
                "expires_at": now + expires_in_seconds,
            },
            "code": code,
        }

    def reserve(self, code: str, email: str) -> str:
        raw = str(code or "").strip()
        if len(raw) < 32 or len(raw) > 128:
            raise InvitationError("Invitation code is invalid or unavailable")
        normalized_email = self._email(email)
        try:
            token_bytes = raw.encode("ascii")
        except UnicodeEncodeError as exc:
            raise InvitationError("Invitation code is invalid or unavailable") from exc
        digest = hashlib.sha256(token_bytes).hexdigest()
        now = int(time.time())
        reservation_id = "invr_" + uuid.uuid4().hex
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT invitation_id,email,status,expires_at FROM invitations WHERE token_hash=?",
                    (digest,),
                ).fetchone()
                if (
                    row is None
                    or str(row["status"]) != "issued"
                    or int(row["expires_at"]) <= now
                    or not secrets.compare_digest(str(row["email"]), normalized_email)
                ):
                    if row is not None and str(row["status"]) == "issued" and int(row["expires_at"]) <= now:
                        conn.execute(
                            "UPDATE invitations SET status='expired' WHERE invitation_id=? AND status='issued'",
                            (str(row["invitation_id"]),),
                        )
                        conn.commit()
                    else:
                        conn.rollback()
                    raise InvitationError("Invitation code is invalid or unavailable")
                conn.execute(
                    """UPDATE invitations SET status='reserved',reservation_id=?,reserved_at=?
                       WHERE invitation_id=? AND status='issued'""",
                    (reservation_id, now, str(row["invitation_id"])),
                )
                conn.commit()
                return reservation_id
            except InvitationError:
                raise
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def consume(self, reservation_id: str) -> None:
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    """UPDATE invitations SET status='consumed',consumed_at=?
                       WHERE reservation_id=? AND status='reserved'""",
                    (int(time.time()), str(reservation_id)),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    raise InvitationError("Invitation reservation is no longer active")
                conn.commit()
            except InvitationError:
                raise
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def release(self, reservation_id: str) -> bool:
        now = int(time.time())
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT invitation_id,expires_at FROM invitations WHERE reservation_id=? AND status='reserved'",
                    (str(reservation_id),),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return False
                status = "issued" if int(row["expires_at"]) > now else "expired"
                cursor = conn.execute(
                    """UPDATE invitations SET status=?,reservation_id=NULL,reserved_at=NULL
                       WHERE invitation_id=? AND status='reserved' AND reservation_id=?""",
                    (status, str(row["invitation_id"]), str(reservation_id)),
                )
                conn.commit()
                return cursor.rowcount == 1
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def revoke(self, invitation_id: str) -> bool:
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    """UPDATE invitations SET status='revoked',revoked_at=?,reservation_id=NULL
                       WHERE invitation_id=? AND status='issued'""",
                    (int(time.time()), str(invitation_id)),
                )
                conn.commit()
                return cursor.rowcount == 1
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def list_invitations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        now = int(time.time())
        with self.lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE invitations SET status='expired' WHERE status='issued' AND expires_at<=?",
                    (now,),
                )
                rows = conn.execute(
                    "SELECT * FROM invitations ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
                return [self._public(row) for row in rows]
            finally:
                conn.close()
