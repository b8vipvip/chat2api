"""C-02a workspace membership catalog; no automatic authorization for API data.

The store only owns tenant + member relationships. Existing UserAccountStore,
ApiKeyStore, Worker and BillingStore remain authoritative for their data.
Do not claim tenant isolation of files, requests, chat or keys from this module:
those owners must consume membership at every resource access separately.
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SLUG = re.compile(r"^[a-z][a-z0-9-]{2,47}$")
USER_ID = re.compile(r"^usr_[A-Za-z0-9_-]{5,128}$")
ROLES = frozenset({"owner", "admin", "member", "viewer"})


class TenantMembershipError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TenantMembershipStore:
    """Transactional owner for workspace membership (not resource authorization)."""

    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / "tenant_membership_v1.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise TenantMembershipError("Tenant store must not be a symlink")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tenant_workspaces(
              id TEXT PRIMARY KEY,
              slug TEXT NOT NULL UNIQUE,
              display_name TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tenant_memberships(
              tenant_id TEXT NOT NULL REFERENCES tenant_workspaces(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('owner','admin','member','viewer')),
              created_at TEXT NOT NULL,
              PRIMARY KEY(tenant_id,user_id)
            );
            CREATE INDEX IF NOT EXISTS tenant_member_user_idx ON tenant_memberships(user_id,tenant_id);
        """)
        os.chmod(self.path, 0o600)

    def create(self, slug: str, display_name: str) -> dict[str, Any]:
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            raise TenantMembershipError("Invalid tenant slug")
        if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 120:
            raise TenantMembershipError("Display name must contain 1-120 characters")
        row = {"tenant_id": "ten_" + secrets.token_hex(12),
               "slug": slug, "display_name": display_name.strip(), "created_at": _now()}
        with self.lock:
            try:
                with self.db:
                    self.db.execute(
                        "INSERT INTO tenant_workspaces(id,slug,display_name,created_at) VALUES (?,?,?,?)",
                        (row["tenant_id"],row["slug"],row["display_name"],row["created_at"]),
                    )
            except sqlite3.IntegrityError as exc:
                raise TenantMembershipError("Tenant slug already exists") from exc
        return row

    def list_tenants(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id,slug,display_name,created_at FROM tenant_workspaces ORDER BY slug"
            ).fetchall()
        return [{"tenant_id": i, "slug": s, "display_name": d, "created_at": c} for i, s, d, c in rows]

    def exists(self, tenant_id: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM tenant_workspaces WHERE id=?", (tenant_id,)).fetchone() is not None

    def upsert_member(self, tenant_id: str, user_id: str, role: str) -> dict[str, Any]:
        if not isinstance(user_id, str) or not USER_ID.fullmatch(user_id):
            raise TenantMembershipError("Invalid user_id")
        if role not in ROLES:
            raise TenantMembershipError("Unknown workspace role")
        with self.lock:
            if not self.exists(tenant_id):
                raise KeyError("Unknown tenant")
            with self.db:
                # Prevent removal/demotion of the final owner.
                existing = self.db.execute(
                    "SELECT role FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                    (tenant_id, user_id),
                ).fetchone()
                if existing and existing[0] == "owner" and role != "owner":
                    owners = self.db.execute(
                        "SELECT COUNT(*) FROM tenant_memberships WHERE tenant_id=? AND role='owner'",
                        (tenant_id,),
                    ).fetchone()[0]
                    if owners < 2:
                        raise TenantMembershipError("Cannot demote last workspace owner")
                self.db.execute("""
                  INSERT INTO tenant_memberships(tenant_id,user_id,role,created_at) VALUES(?,?,?,?)
                  ON CONFLICT(tenant_id,user_id) DO UPDATE SET role=excluded.role
                """, (tenant_id, user_id, role, _now()))
        return {"tenant_id": tenant_id, "user_id": user_id, "role": role}

    def remove_member(self, tenant_id: str, user_id: str) -> None:
        with self.lock:
            with self.db:
                existing = self.db.execute(
                    "SELECT role FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                    (tenant_id, user_id),
                ).fetchone()
                if not existing:
                    raise KeyError("Unknown workspace membership")
                if existing[0] == "owner":
                    owners = self.db.execute(
                        "SELECT COUNT(*) FROM tenant_memberships WHERE tenant_id=? AND role='owner'",
                        (tenant_id,),
                    ).fetchone()[0]
                    if owners < 2:
                        raise TenantMembershipError("Cannot remove last workspace owner")
                self.db.execute(
                    "DELETE FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                    (tenant_id, user_id),
                )

    def list_members(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.lock:
            if not self.exists(tenant_id):
                raise KeyError("Unknown tenant")
            rows = self.db.execute(
                "SELECT user_id,role,created_at FROM tenant_memberships WHERE tenant_id=? ORDER BY user_id",
                (tenant_id,),
            ).fetchall()
        return [{"tenant_id": tenant_id, "user_id": uid, "role": role, "created_at": created}
                for uid,role,created in rows]

    def role_for(self, tenant_id: str, user_id: str) -> str | None:
        """Return membership role for an already authenticated account identity."""
        with self.lock:
            row = self.db.execute(
                "SELECT role FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                (tenant_id, user_id),
            ).fetchone()
            return row[0] if row else None

    def close(self) -> None:
        self.db.close()
