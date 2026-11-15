"""C-track control-plane authority: administrator identities and client policy.

This intentionally does not own Worker routing, API-key quotas, tenant billing or
plugin execution. Those retain their existing authorities until independently
migrated and tested. Single-instance JSON persistence; multi-instance is a
separate production gate.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import uuid
from pathlib import Path
from typing import Any


POLICY_VERSION = 1
ROLES: dict[str, frozenset[str]] = {
    "super_admin": frozenset({"*"}),
    "system_admin": frozenset({"system:read", "system:write", "clients:read", "clients:write", "models:read", "models:write", "users:read", "diagnostics:read", "tenants:read", "tenants:write", "quota:read", "quota:write", "admin:self_password"}),
    "user_admin": frozenset({"users:read", "users:write", "admin:self_password"}),
    "api_key_admin": frozenset({"keys:read", "keys:write", "quota:read", "quota:write", "admin:self_password"}),
    "billing_admin": frozenset({"billing:read", "billing:write", "quota:read", "admin:self_password"}),
    "plugin_admin": frozenset({"plugins:read", "plugins:write", "admin:self_password"}),
    "support": frozenset({"users:read", "keys:read", "models:read", "clients:read", "diagnostics:read", "admin:self_password"}),
}
ADMIN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")
ITERATIONS = 310_000
DEFAULT_CLIENT = {"registration_mode": "open", "web_chat_enabled": True, "announcement": ""}


class ControlPlaneError(ValueError):
    pass


def permission_for_admin_route(path: str, method: str) -> str | None:
    """Explicit allowlist for delegated admin endpoint patterns.

    A new child route (including a secret/export endpoint) must never inherit a
    broad prefix rule accidentally. Root super_admin retains legacy access.
    """
    write = method.upper() not in {"GET", "HEAD", "OPTIONS"}
    suffix = "write" if write else "read"
    if path == "/api/clients" or path == "/api/admin/extensions":
        return "system:" + suffix
    if re.fullmatch(r"/api/admin/extensions/[^/]+/(disconnect|enable)", path):
        return "system:write"
    if path == "/api/admin/pairing-codes" or re.fullmatch(r"/api/admin/pairing-codes/[^/]+", path):
        return "system:" + suffix
    if path == "/api/admin/control/admins" or re.fullmatch(r"/api/admin/control/admins/[^/]+", path):
        return "admins:write" if write else "admins:read"
    if re.fullmatch(r"/api/admin/control/admins/[^/]+/password", path):
        # This compatibility route also supports self-service. The handler
        # requires the authenticated actor to match the target username and
        # verify the current password; only root may target another admin.
        return "users:write"
    if path == "/api/admin/control/self/password":
        return "admin:self_password"
    if path == "/api/admin/control/client":
        return "clients:" + suffix
    if path == "/api/admin/control/invitations" or re.fullmatch(
        r"/api/admin/control/invitations/[^/]+/revoke", path
    ):
        return "users:" + suffix
    if path == "/api/admin/control/roles":
        return "admins:read"
    if path == "/api/admin/model-entitlements" or re.fullmatch(
        r"/api/admin/model-entitlements/(global|key|user|tenant|tenant_role)/[^/]+", path
    ):
        return "models:" + suffix
    if path == "/api/admin/audit":
        return "audit:read"
    if path == "/api/admin/quota-reservations":
        return "quota:read"
    if path == "/api/admin/quota-reservations/reconcile":
        return "quota:write"
    if re.fullmatch(r"/api/admin/billing/charges/[^/]+/refund", path):
        return "billing:write" if method.upper() == "POST" else None
    if path == "/api/admin/tenants" or re.fullmatch(
        r"/api/admin/tenants/[^/]+/(?:members(?:/[^/]+)?|keys(?:/[^/]+)?)", path
    ):
        return "tenants:" + suffix
    if path == "/api/admin/users" or re.fullmatch(
        r"/api/admin/users/[^/]+(?:/sessions/revoke)?", path
    ):
        return "users:" + suffix
    # User-owned API keys have their own permission boundary. A user manager
    # must not gain key visibility or mutation rights through /users/{id}/keys.
    if re.fullmatch(r"/api/admin/users/[^/]+/keys(?:/[^/]+)?", path):
        return "keys:" + suffix
    if re.fullmatch(r"/api/admin/keys/[^/]+/secret", path):
        return "secrets:read"
    if re.fullmatch(r"/api/admin/keys/[^/]+/settings", path):
        return "keys:write"
    if path == "/api/admin/keys" or re.fullmatch(r"/api/admin/keys/[^/]+", path):
        return "keys:" + suffix
    if path == "/api/admin/user-pricing":
        return "billing:" + suffix
    if path == "/api/admin/user-payments":
        # Payment provider private keys remain root-only until G0 DataKeyring
        # migration and its rotation/rollback evidence are accepted.
        return "secrets:write" if write else "billing:read"
    if path == "/api/admin/user-payments/test":
        return "billing:write"
    if path == "/api/admin/payment-channels-v106":
        # This settings document contains provider credentials; keep writes
        # root-only until the G0 keyring migration is accepted.
        return "secrets:write" if write else "billing:read"
    if path in {
        "/api/admin/payment-channels-v106/usdt-orders",
        "/api/admin/payment-channels-v106/test",
    } or re.fullmatch(r"/api/admin/payment-channels-v106/usdt-orders/[^/]+/(confirm|reject)", path):
        return "billing:" + suffix
    if path in {"/api/admin/models", "/api/admin/text-models", "/api/admin/text-model-minimum"} or re.fullmatch(
        r"/api/admin/text-models/[^/]+", path
    ):
        return "models:" + suffix
    # Raw request detail and media can contain user content or credentials.
    # Do not grant delegated support access until a redacted projection exists.
    if path == "/api/admin/worker-tools/catalog":
        return "diagnostics:read"
    if path == "/api/admin/worker-tools/policies":
        return "diagnostics:read"
    if re.fullmatch(r"/api/admin/worker-tools/policies/[^/]+", path):
        return "system:write"
    if path == "/api/admin/worker-tools/runs":
        return "diagnostics:read"
    if path == "/api/admin/overview":
        return "system:read"
    if path == "/api/admin/modes":
        return "system:" + suffix
    if path == "/api/admin/plugins" or re.fullmatch(
        r"/api/admin/plugins/[^/]+/versions/[^/]+", path
    ):
        return "plugins:" + suffix
    return None

class ControlPlaneStore:
    """Single-process durable administrator directory and client flags.

    Root super_admin is the configured legacy administrator, not a persisted
    password duplicate. Never write plaintext credentials to disk or an API.
    """

    def __init__(self, data_dir: Path | str, root_username: str) -> None:
        self.path = Path(data_dir) / "control_plane_v1.json"
        self.root_username = str(root_username or "admin").casefold()
        self.lock = threading.RLock()
        self.admins: dict[str, dict[str, Any]] = {}
        self.client: dict[str, Any] = dict(DEFAULT_CLIENT)
        self._load()

    def _load(self) -> None:
        if self.path.is_symlink():
            raise ControlPlaneError("Control-plane storage cannot be a symbolic link")
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != POLICY_VERSION:
                raise ValueError("unsupported control-plane schema")
            client = payload["client"]
            admins = payload["admins"]
            if not isinstance(client, dict) or not isinstance(admins, list):
                raise ValueError("invalid control-plane structure")
            if set(client) != set(DEFAULT_CLIENT):
                raise ValueError("unexpected client policy keys")
            self._validate_client(client)
            loaded = {}
            for row in admins:
                if not isinstance(row, dict):
                    raise ValueError("invalid administrator entry")
                name = row.get("username")
                role = row.get("role")
                salt = row.get("salt")
                digest = row.get("password_hash")
                enabled = row.get("enabled")
                if (not isinstance(name, str) or not ADMIN_NAME.fullmatch(name)
                    or name.casefold() == self.root_username or role not in ROLES
                    or not isinstance(salt, str) or not re.fullmatch(r"[0-9a-f]{32}", salt)
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or type(enabled) is not bool or name.casefold() in loaded):
                    raise ValueError("invalid administrator identity")
                loaded[name.casefold()] = dict(row)
            self.admins = loaded
            self.client = dict(client)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ControlPlaneError("Control-plane storage is corrupt or unreadable; refusing to reset") from exc

    def _save(self) -> None:
        payload = {"version": POLICY_VERSION, "client": self.client, "admins": list(self.admins.values())}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ControlPlaneError("Control-plane storage cannot be a symbolic link")
        tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, sort_keys=True, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _validate_client(payload: dict[str, Any]) -> None:
        if payload.get("registration_mode") not in {"open", "closed", "invite_only"}:
            raise ControlPlaneError("registration_mode must be open, closed or invite_only")
        if type(payload.get("web_chat_enabled")) is not bool:
            raise ControlPlaneError("web_chat_enabled must be a boolean")
        announcement = payload.get("announcement")
        if not isinstance(announcement, str) or len(announcement) > 500:
            raise ControlPlaneError("announcement must be a string up to 500 characters")

    def public_client(self) -> dict[str, Any]:
        with self.lock:
            return {"policy_version": POLICY_VERSION, **self.client}

    def update_client(self, changes: dict[str, Any]) -> dict[str, Any]:
        if not changes or set(changes) - set(DEFAULT_CLIENT):
            raise ControlPlaneError("Unknown or empty client policy update")
        with self.lock:
            next_client = {**self.client, **changes}
            self._validate_client(next_client)
            previous = self.client
            self.client = next_client
            try:
                self._save()
            except Exception:
                self.client = previous
                raise
            return self.public_client()

    def role_for(self, username: str | None) -> str | None:
        if not username:
            return None
        with self.lock:
            if username.casefold() == self.root_username:
                return "super_admin"
            row = self.admins.get(username.casefold())
            return str(row["role"]) if row and row["enabled"] else None

    def permitted(self, username: str | None, path: str, method: str) -> bool:
        role = self.role_for(username)
        if role is None:
            return False
        if role == "super_admin":
            return True
        permission = permission_for_admin_route(path, method)
        return permission is not None and permission in ROLES[role]

    def verify_admin(self, username: str, password: str) -> bool:
        with self.lock:
            row = self.admins.get(str(username or "").casefold())
            if not row or not row["enabled"]:
                return False
            salt = bytes.fromhex(row["salt"])
            expected = bytes.fromhex(row["password_hash"])
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
            return hmac.compare_digest(actual, expected)

    @staticmethod
    def _public_admin(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row[key] for key in ("username", "role", "enabled")}

    def list_admins(self) -> list[dict[str, Any]]:
        with self.lock:
            return sorted((self._public_admin(row) for row in self.admins.values()), key=lambda row: row["username"])

    def create_admin(self, username: str, password: str, role: str) -> dict[str, Any]:
        if not isinstance(username, str) or not ADMIN_NAME.fullmatch(username):
            raise ControlPlaneError("Username must be 3-64 ASCII identifier characters")
        if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
            raise ControlPlaneError("Password must contain 12-256 characters")
        if role not in ROLES:
            raise ControlPlaneError("Unknown administrator role")
        key = username.casefold()
        with self.lock:
            if key == self.root_username or key in self.admins:
                raise ControlPlaneError("Administrator already exists")
            salt = secrets.token_bytes(16)
            self.admins[key] = {
                "username": username,
                "role": role,
                "enabled": True,
                "salt": salt.hex(),
                "password_hash": hashlib.pbkdf2_hmac(
                    "sha256", password.encode("utf-8"), salt, ITERATIONS
                ).hex(),
            }
            try:
                self._save()
            except Exception:
                del self.admins[key]
                raise
            return self._public_admin(self.admins[key])

    def set_admin_password(self, username: str, password: str) -> dict[str, Any]:
        if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
            raise ControlPlaneError("Password must contain 12-256 characters")
        key = str(username or "").casefold()
        if not key or key == self.root_username:
            raise ControlPlaneError("The environment-managed root password cannot be changed here")
        with self.lock:
            row = self.admins.get(key)
            if not row:
                raise KeyError(username)
            previous = dict(row)
            salt = secrets.token_bytes(16)
            row["salt"] = salt.hex()
            row["password_hash"] = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, ITERATIONS
            ).hex()
            try:
                self._save()
            except Exception:
                self.admins[key] = previous
                raise
            return self._public_admin(row)

    def update_admin(self, username: str, *, role: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
        if role is None and enabled is None:
            raise ControlPlaneError("No administrator change supplied")
        if role is not None and role not in ROLES:
            raise ControlPlaneError("Unknown administrator role")
        if enabled is not None and type(enabled) is not bool:
            raise ControlPlaneError("enabled must be a boolean")
        with self.lock:
            row = self.admins.get(username.casefold())
            if not row:
                raise KeyError(username)
            previous = dict(row)
            if role is not None:
                row["role"] = role
            if enabled is not None:
                row["enabled"] = enabled
            try:
                self._save()
            except Exception:
                self.admins[username.casefold()] = previous
                raise
            return self._public_admin(row)
