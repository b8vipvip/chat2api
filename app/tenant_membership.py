"""C-02a workspace membership catalog; no automatic authorization for API data.

The store only owns tenant + member relationships. Existing UserAccountStore,
ApiKeyStore, Worker and BillingStore remain authoritative for their data.
Do not claim tenant isolation of files, requests, chat or keys from this module:
those owners must consume membership at every resource access separately.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SLUG = re.compile(r"^[a-z][a-z0-9-]{2,47}$")
USER_ID = re.compile(r"^usr_[A-Za-z0-9_-]{5,128}$")
KEY_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+$")
INVITATION_ID = re.compile(r"^tinv_[0-9a-f]{24}$")
ROLES = frozenset({"owner", "admin", "member", "viewer"})
# Workspace ownership is a transfer-sensitive privilege. Invitations may grant
# delegated roles, but an owner must be assigned through the explicit admin
# membership operation so a routine invitation cannot silently create owners.
INVITABLE_ROLES = frozenset({"admin", "member", "viewer"})


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
            CREATE TABLE IF NOT EXISTS tenant_api_keys(
              tenant_id TEXT NOT NULL,
              key_id TEXT NOT NULL UNIQUE,
              user_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(tenant_id,key_id),
              FOREIGN KEY(tenant_id,user_id)
                REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS tenant_api_keys_user_idx ON tenant_api_keys(user_id,tenant_id);
            CREATE TABLE IF NOT EXISTS tenant_invitations(
              invitation_id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL REFERENCES tenant_workspaces(id) ON DELETE CASCADE,
              email TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('owner','admin','member','viewer')),
              token_hash TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK(status IN ('issued','accepted','revoked','expired')),
              expires_at INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              created_by TEXT NOT NULL,
              accepted_by TEXT,
              accepted_at TEXT,
              revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS tenant_invites_email_idx
              ON tenant_invitations(tenant_id,email,status);
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
                    self.db.execute("BEGIN IMMEDIATE")
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

    def upsert_member(
        self, tenant_id: str, user_id: str, role: str, *,
        allow_owner_change: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(user_id, str) or not USER_ID.fullmatch(user_id):
            raise TenantMembershipError("Invalid user_id")
        if role not in ROLES:
            raise TenantMembershipError("Unknown workspace role")
        with self.lock:
            if not self.exists(tenant_id):
                raise KeyError("Unknown tenant")
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                # Prevent removal/demotion of the final owner.
                existing = self.db.execute(
                    "SELECT role FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                    (tenant_id, user_id),
                ).fetchone()
                # Enforce the privileged transition while holding the same
                # SQLite write lock as the membership mutation. An API-layer
                # preflight alone would allow a concurrent promotion to race
                # with a delegated demotion/removal.
                if not allow_owner_change and (
                    role == "owner" or (existing and existing[0] == "owner" and role != "owner")
                ):
                    raise TenantMembershipError("Workspace owner transitions require super_admin")
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

    def remove_member(
        self, tenant_id: str, user_id: str, *, allow_owner_change: bool = True,
    ) -> None:
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                existing = self.db.execute(
                    "SELECT role FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                    (tenant_id, user_id),
                ).fetchone()
                if not existing:
                    raise KeyError("Unknown workspace membership")
                if existing[0] == "owner" and not allow_owner_change:
                    raise TenantMembershipError("Workspace owner transitions require super_admin")
                bound = self.db.execute(
                    "SELECT COUNT(*) FROM tenant_api_keys WHERE tenant_id=? AND user_id=?",
                    (tenant_id, user_id),
                ).fetchone()[0]
                if bound:
                    raise TenantMembershipError("Unbind the member's API keys before removing membership")
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

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Only return workspaces joined through the authenticated user's identity."""
        with self.lock:
            rows = self.db.execute(
                """SELECT w.id,w.slug,w.display_name,m.role
                   FROM tenant_workspaces w
                   JOIN tenant_memberships m ON m.tenant_id=w.id
                   WHERE m.user_id=? ORDER BY w.slug""",
                (user_id,),
            ).fetchall()
        return [{"tenant_id": tid, "slug": slug, "display_name": name, "role": role}
                for tid, slug, name, role in rows]

    def bind_key(self, tenant_id: str, user_id: str, key_id: str) -> dict[str, Any]:
        """Bind an existing user-owned API key to exactly one workspace."""
        if not isinstance(key_id, str) or not KEY_ID.fullmatch(key_id):
            raise TenantMembershipError("Invalid API key ID")
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                member = self.db.execute(
                    "SELECT 1 FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                    (tenant_id, user_id),
                ).fetchone()
                if not member:
                    raise KeyError("User is not a member of this tenant")
                existing = self.db.execute(
                    "SELECT tenant_id,user_id FROM tenant_api_keys WHERE key_id=?",
                    (key_id,),
                ).fetchone()
                if existing:
                    if existing == (tenant_id, user_id):
                        return {"tenant_id": tenant_id, "user_id": user_id, "key_id": key_id}
                    raise TenantMembershipError("API key is already bound to another tenant")
                self.db.execute(
                    "INSERT INTO tenant_api_keys(tenant_id,key_id,user_id,created_at) VALUES(?,?,?,?)",
                    (tenant_id, key_id, user_id, _now()),
                )
        return {"tenant_id": tenant_id, "user_id": user_id, "key_id": key_id}

    def unbind_key(self, tenant_id: str, key_id: str) -> None:
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                cursor = self.db.execute(
                    "DELETE FROM tenant_api_keys WHERE tenant_id=? AND key_id=?",
                    (tenant_id, key_id),
                )
                if cursor.rowcount == 0:
                    raise KeyError("Unknown tenant API key binding")

    def tenant_for_key(self, key_id: str) -> str | None:
        """Resolve tenant only from explicit server-side key binding."""
        with self.lock:
            row = self.db.execute(
                "SELECT tenant_id FROM tenant_api_keys WHERE key_id=?",
                (str(key_id or ""),),
            ).fetchone()
        return str(row[0]) if row else None

    def user_for_key(self, key_id: str) -> str | None:
        """Return the actual UserAccountStore owner captured at key binding."""
        with self.lock:
            row = self.db.execute(
                "SELECT user_id FROM tenant_api_keys WHERE key_id=?",
                (str(key_id or ""),),
            ).fetchone()
        return str(row[0]) if row else None

    def keys_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.lock:
            if not self.exists(tenant_id):
                raise KeyError("Unknown tenant")
            rows = self.db.execute(
                "SELECT key_id,user_id,created_at FROM tenant_api_keys WHERE tenant_id=? ORDER BY created_at",
                (tenant_id,),
            ).fetchall()
        return [{"tenant_id": tenant_id, "key_id": key, "user_id": user, "created_at": created}
                for key, user, created in rows]

    def role_for(self, tenant_id: str, user_id: str) -> str | None:
        """Return membership role for an already authenticated account identity."""
        with self.lock:
            row = self.db.execute(
                "SELECT role FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                (tenant_id, user_id),
            ).fetchone()
            return row[0] if row else None

    @staticmethod
    def _normalize_invitation_email(email: str) -> str:
        normalized = str(email or "").strip().lower()
        if len(normalized) > 254 or not EMAIL.fullmatch(normalized):
            raise TenantMembershipError("Invalid invitation email")
        return normalized

    def create_invitation(
        self, tenant_id: str, email: str, role: str, created_by: str,
        *, expires_in_seconds: int = 7 * 24 * 3600,
    ) -> tuple[dict[str, Any], str]:
        normalized_email = self._normalize_invitation_email(email)
        if role not in INVITABLE_ROLES:
            raise TenantMembershipError("Invitation role must be admin, member or viewer; owner assignment requires an explicit admin action")
        actor = str(created_by or "").strip()
        if not actor or len(actor) > 128:
            raise TenantMembershipError("Invalid invitation actor")
        if type(expires_in_seconds) is not int or not 300 <= expires_in_seconds <= 30 * 24 * 3600:
            raise TenantMembershipError("Invitation expiry must be between 5 minutes and 30 days")

        invitation_id = "tinv_" + secrets.token_hex(12)
        code = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        created_at = _now()
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                if self.db.execute(
                    "SELECT 1 FROM tenant_workspaces WHERE id=?", (tenant_id,),
                ).fetchone() is None:
                    raise KeyError("Unknown tenant")
                self.db.execute(
                    """UPDATE tenant_invitations SET status='expired'
                       WHERE tenant_id=? AND email=? AND status='issued' AND expires_at<=?""",
                    (tenant_id, normalized_email, now),
                )
                active = self.db.execute(
                    """SELECT 1 FROM tenant_invitations
                       WHERE tenant_id=? AND email=? AND status='issued' AND expires_at>? LIMIT 1""",
                    (tenant_id, normalized_email, now),
                ).fetchone()
                if active:
                    raise TenantMembershipError("An active invitation already exists for this email")
                expires_at = now + expires_in_seconds
                self.db.execute(
                    """INSERT INTO tenant_invitations
                       (invitation_id,tenant_id,email,role,token_hash,status,expires_at,
                        created_at,created_by,accepted_by,accepted_at,revoked_at)
                       VALUES(?,?,?,?,?,'issued',?,?,?,NULL,NULL,NULL)""",
                    (invitation_id, tenant_id, normalized_email, role, token_hash,
                     expires_at, created_at, actor),
                )
        return ({
            "invitation_id": invitation_id, "tenant_id": tenant_id,
            "email": normalized_email, "role": role, "status": "issued",
            "expires_at": expires_at, "created_at": created_at, "created_by": actor,
        }, code)

    def list_invitations(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                if self.db.execute(
                    "SELECT 1 FROM tenant_workspaces WHERE id=?", (tenant_id,),
                ).fetchone() is None:
                    raise KeyError("Unknown tenant")
                self.db.execute(
                    """UPDATE tenant_invitations SET status='expired'
                       WHERE tenant_id=? AND status='issued' AND expires_at<=?""",
                    (tenant_id, now),
                )
                rows = self.db.execute(
                    """SELECT invitation_id,email,role,status,expires_at,created_at,created_by,
                              accepted_by,accepted_at,revoked_at
                       FROM tenant_invitations WHERE tenant_id=? ORDER BY created_at DESC""",
                    (tenant_id,),
                ).fetchall()
        return [{
            "invitation_id": row[0], "tenant_id": tenant_id, "email": row[1],
            "role": row[2], "status": row[3], "expires_at": row[4],
            "created_at": row[5], "created_by": row[6],
            "accepted_by": row[7], "accepted_at": row[8], "revoked_at": row[9],
        } for row in rows]

    def revoke_invitation(self, tenant_id: str, invitation_id: str) -> bool:
        if not isinstance(invitation_id, str) or not INVITATION_ID.fullmatch(invitation_id):
            raise TenantMembershipError("Invalid invitation ID")
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                row = self.db.execute(
                    """SELECT status,expires_at FROM tenant_invitations
                       WHERE tenant_id=? AND invitation_id=?""",
                    (tenant_id, invitation_id),
                ).fetchone()
                if row is None:
                    raise KeyError("Unknown tenant invitation")
                if row[0] != "issued":
                    return False
                if int(row[1]) <= now:
                    self.db.execute(
                        "UPDATE tenant_invitations SET status='expired' WHERE invitation_id=?",
                        (invitation_id,),
                    )
                    return False
                self.db.execute(
                    """UPDATE tenant_invitations SET status='revoked',revoked_at=?
                       WHERE tenant_id=? AND invitation_id=? AND status='issued'""",
                    (_now(), tenant_id, invitation_id),
                )
                return True

    def accept_invitation(self, code: str, user_id: str, email: str) -> dict[str, Any]:
        if not isinstance(user_id, str) or not USER_ID.fullmatch(user_id):
            raise TenantMembershipError("Invalid user_id")
        normalized_email = self._normalize_invitation_email(email)
        raw_code = str(code or "").strip()
        if not 32 <= len(raw_code) <= 256:
            raise TenantMembershipError("Invalid invitation code")
        token_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
        expired = False
        invalid_role = False
        result: dict[str, Any] | None = None
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                row = self.db.execute(
                    """SELECT invitation_id,tenant_id,email,role,status,expires_at
                       FROM tenant_invitations WHERE token_hash=?""",
                    (token_hash,),
                ).fetchone()
                if row is None:
                    raise KeyError("Unknown tenant invitation")
                invitation_id, tenant_id, invited_email, role, status, expires_at = row
                if status != "issued":
                    raise TenantMembershipError("Invitation is no longer available")
                if int(expires_at) <= now:
                    self.db.execute(
                        "UPDATE tenant_invitations SET status='expired' WHERE invitation_id=?",
                        (invitation_id,),
                    )
                    expired = True
                elif str(invited_email) != normalized_email:
                    raise TenantMembershipError("Invitation email does not match the signed-in account")
                elif role not in INVITABLE_ROLES:
                    # Revoke invitations created by older versions that could
                    # grant owner. Verify the intended email first so a bearer
                    # of a leaked code cannot invalidate someone else's invite.
                    self.db.execute(
                        """UPDATE tenant_invitations
                           SET status='revoked',revoked_at=?
                           WHERE invitation_id=? AND status='issued'""",
                        (_now(), invitation_id),
                    )
                    invalid_role = True
                else:
                    existing = self.db.execute(
                        "SELECT role FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                        (tenant_id, user_id),
                    ).fetchone()
                    if existing:
                        raise TenantMembershipError("User is already a member of this workspace")
                    self.db.execute(
                        """INSERT INTO tenant_memberships(tenant_id,user_id,role,created_at)
                           VALUES(?,?,?,?)""",
                        (tenant_id, user_id, role, _now()),
                    )
                    accepted_at = _now()
                    self.db.execute(
                        """UPDATE tenant_invitations
                           SET status='accepted',accepted_by=?,accepted_at=?
                           WHERE invitation_id=? AND status='issued'""",
                        (user_id, accepted_at, invitation_id),
                    )
                    result = {"tenant_id": tenant_id, "user_id": user_id, "role": role}
        if expired:
            raise TenantMembershipError("Invitation has expired")
        if invalid_role:
            raise TenantMembershipError("Invitation role is no longer permitted")
        if result is None:
            raise TenantMembershipError("Invitation could not be accepted")
        return result

    def close(self) -> None:
        self.db.close()
