"""Authoritative end-user subscription lifecycle for C-03 model entitlements.

This store is deliberately separate from BillingStore: wallet balance, estimated
usage charges and payment orders do not prove that a user owns a subscription.
It is a single-instance SQLite authority until the D/P multi-instance migration.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .private_storage import secure_private_sqlite_files


PLAN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
DEFAULT_PLAN_ID = "free"


class UserSubscriptionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str | None, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise UserSubscriptionError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UserSubscriptionError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UserSubscriptionError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


class UserSubscriptionStore:
    """SQLite owner for plan definitions and per-user subscription lifecycle."""

    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / "user_subscriptions_v1.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        secure_private_sqlite_files(self.path, create_database=True)
        self.db = sqlite3.connect(
            str(self.path), timeout=10, check_same_thread=False, isolation_level=None
        )
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS subscription_plans(
              plan_id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_subscriptions(
              subscription_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              plan_id TEXT NOT NULL REFERENCES subscription_plans(plan_id),
              status TEXT NOT NULL CHECK(status IN ('active','cancelled')),
              starts_at TEXT NOT NULL,
              expires_at TEXT,
              assigned_by TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS user_subscriptions_current_idx
              ON user_subscriptions(user_id,status,starts_at,expires_at);
        """)
        secure_private_sqlite_files(self.path)
        self._ensure_default_plan()

    def _ensure_default_plan(self) -> None:
        now = _now()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    """INSERT OR IGNORE INTO subscription_plans
                       (plan_id,name,enabled,created_at,updated_at)
                       VALUES (?,?,?,?,?)""",
                    (DEFAULT_PLAN_ID, "Free", 1, now, now),
                )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    @staticmethod
    def _plan_id(value: str) -> str:
        plan_id = str(value or "").strip().lower()
        if not PLAN_ID_RE.fullmatch(plan_id):
            raise UserSubscriptionError("plan_id must be a lowercase plan slug")
        return plan_id

    def upsert_plan(self, plan_id: str, name: str, *, enabled: bool = True) -> dict[str, Any]:
        plan_id = self._plan_id(plan_id)
        display_name = str(name or "").strip()
        if not 1 <= len(display_name) <= 100:
            raise UserSubscriptionError("Plan name must contain 1-100 characters")
        if type(enabled) is not bool:
            raise UserSubscriptionError("enabled must be a boolean")
        if plan_id == DEFAULT_PLAN_ID and not enabled:
            raise UserSubscriptionError("The default free plan cannot be disabled")
        now = _now()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    """INSERT INTO subscription_plans
                       (plan_id,name,enabled,created_at,updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(plan_id) DO UPDATE SET
                         name=excluded.name,enabled=excluded.enabled,updated_at=excluded.updated_at""",
                    (plan_id, display_name, int(enabled), now, now),
                )
                row = self.db.execute(
                    "SELECT plan_id,name,enabled,created_at,updated_at FROM subscription_plans WHERE plan_id=?",
                    (plan_id,),
                ).fetchone()
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return self._plan_row(row)

    @staticmethod
    def _plan_row(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "plan_id": row[0], "name": row[1], "enabled": bool(row[2]),
            "created_at": row[3], "updated_at": row[4],
        }

    def plan_exists(self, plan_id: str) -> bool:
        wanted = self._plan_id(plan_id)
        with self.lock:
            return self.db.execute(
                "SELECT 1 FROM subscription_plans WHERE plan_id=?", (wanted,)
            ).fetchone() is not None

    def list_plans(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT plan_id,name,enabled,created_at,updated_at FROM subscription_plans ORDER BY plan_id"
            ).fetchall()
        return [self._plan_row(row) for row in rows]

    def assign(
        self, user_id: str, plan_id: str, *, expires_at: str | None = None,
        assigned_by: str,
    ) -> dict[str, Any]:
        user_id = str(user_id or "").strip()
        if not user_id or len(user_id) > 160:
            raise UserSubscriptionError("user_id is required")
        plan_id = self._plan_id(plan_id)
        expiry = _timestamp(expires_at, field="expires_at")
        now = _now()
        if expiry is not None and expiry <= now:
            raise UserSubscriptionError("expires_at must be in the future")
        actor = str(assigned_by or "").strip()[:128]
        if not actor:
            raise UserSubscriptionError("assigned_by is required")
        subscription_id = "sub_" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                plan = self.db.execute(
                    "SELECT enabled FROM subscription_plans WHERE plan_id=?", (plan_id,)
                ).fetchone()
                if plan is None:
                    raise KeyError("Subscription plan not found")
                if not bool(plan[0]):
                    raise UserSubscriptionError("Subscription plan is disabled")
                # PUT assignment is idempotent: an identical active plan and
                # expiry must not create a new history row when an admin retries
                # after a timeout or an audit-response failure.
                existing = self.db.execute(
                    """SELECT subscription_id,user_id,plan_id,status,starts_at,expires_at,
                              assigned_by,created_at,updated_at
                       FROM user_subscriptions
                       WHERE user_id=? AND plan_id=? AND status='active'
                         AND starts_at<=?
                         AND ((expires_at IS NULL AND ? IS NULL) OR expires_at=?)
                         AND (expires_at IS NULL OR expires_at>?)
                       ORDER BY starts_at DESC,created_at DESC LIMIT 1""",
                    (user_id, plan_id, now, expiry, expiry, now),
                ).fetchone()
                if existing is not None:
                    self.db.execute("COMMIT")
                    return self._subscription_row(existing)
                # Assignment is immediate and replaces the previous active
                # subscription. Historical rows remain for audit/reconciliation.
                self.db.execute(
                    """UPDATE user_subscriptions SET status='cancelled',updated_at=?
                       WHERE user_id=? AND status='active'""",
                    (now, user_id),
                )
                self.db.execute(
                    """INSERT INTO user_subscriptions
                       (subscription_id,user_id,plan_id,status,starts_at,expires_at,
                        assigned_by,created_at,updated_at)
                       VALUES (?,?,?,'active',?,?,?,?,?)""",
                    (subscription_id, user_id, plan_id, now, expiry, actor, now, now),
                )
                row = self.db.execute(
                    """SELECT subscription_id,user_id,plan_id,status,starts_at,expires_at,
                              assigned_by,created_at,updated_at
                       FROM user_subscriptions WHERE subscription_id=?""",
                    (subscription_id,),
                ).fetchone()
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return self._subscription_row(row)

    @staticmethod
    def _subscription_row(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "subscription_id": row[0], "user_id": row[1], "plan_id": row[2],
            "status": row[3], "starts_at": row[4], "expires_at": row[5],
            "assigned_by": row[6], "created_at": row[7], "updated_at": row[8],
        }

    def current_plan_for_user(self, user_id: str) -> dict[str, Any]:
        wanted = str(user_id or "").strip()
        if not wanted:
            raise UserSubscriptionError("user_id is required")
        now = _now()
        with self.lock:
            row = self.db.execute(
                """SELECT s.subscription_id,s.user_id,s.plan_id,s.status,s.starts_at,
                          s.expires_at,s.assigned_by,s.created_at,s.updated_at,p.name
                   FROM user_subscriptions s
                   JOIN subscription_plans p ON p.plan_id=s.plan_id
                   WHERE s.user_id=? AND s.status='active' AND p.enabled=1
                     AND s.starts_at<=?
                     AND (s.expires_at IS NULL OR s.expires_at>?)
                   ORDER BY s.starts_at DESC,s.created_at DESC LIMIT 1""",
                (wanted, now, now),
            ).fetchone()
            if row is not None:
                return {
                    **self._subscription_row(row[:9]),
                    "name": row[9],
                    "source": "subscription",
                }
            free = self.db.execute(
                "SELECT plan_id,name FROM subscription_plans WHERE plan_id=? AND enabled=1",
                (DEFAULT_PLAN_ID,),
            ).fetchone()
        if free is None:
            raise UserSubscriptionError("Default free plan is missing")
        return {
            "subscription_id": None, "user_id": wanted, "plan_id": free[0],
            "name": free[1], "status": "default", "starts_at": None,
            "expires_at": None, "assigned_by": None, "created_at": None,
            "updated_at": None, "source": "default",
        }

    def history_for_user(self, user_id: str) -> list[dict[str, Any]]:
        wanted = str(user_id or "").strip()
        now = _now()
        with self.lock:
            rows = self.db.execute(
                """SELECT subscription_id,user_id,plan_id,status,starts_at,expires_at,
                          assigned_by,created_at,updated_at
                   FROM user_subscriptions WHERE user_id=?
                   ORDER BY created_at DESC,subscription_id DESC""",
                (wanted,),
            ).fetchall()
        history = []
        for row in rows:
            item = self._subscription_row(row)
            if item["status"] == "active" and item["expires_at"] and item["expires_at"] <= now:
                item["status"] = "expired"
            history.append(item)
        return history

    def cancel_active(self, user_id: str) -> int:
        wanted = str(user_id or "").strip()
        if not wanted:
            raise UserSubscriptionError("user_id is required")
        now = _now()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                cursor = self.db.execute(
                    """UPDATE user_subscriptions SET status='cancelled',updated_at=?
                       WHERE user_id=? AND status='active'""",
                    (now, wanted),
                )
                self.db.execute("COMMIT")
                return int(cursor.rowcount)
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self.db.close()
