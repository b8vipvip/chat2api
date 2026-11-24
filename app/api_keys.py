from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from dataclasses import asdict, dataclass

# A missing quota means unlimited; 0 means exhausted immediately.
UNSET = object()
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .timezone_utils import beijing_now, beijing_now_iso, parse_datetime, to_beijing_iso
from .private_storage import private_file_lock, read_private_bytes, write_private_bytes_atomic


def utc_now() -> str:
    """Backward-compatible helper name; canonical chat2api timestamps are Asia/Shanghai."""
    return beijing_now_iso()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    value = parse_datetime(expires_at)
    if value is None:
        return True
    return value <= beijing_now()


def _fernet(secret: str) -> Fernet | None:
    if not secret:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def load_or_create_data_secret(data_dir: Path) -> str:
    """Return a server-local encryption secret independent of admin credentials."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "data_secret.key"
    # Use a distinct lock-file name so a stale directory lock from an older
    # release cannot block startup after that process crashed while holding it.
    with private_file_lock(data_dir / ".data_secret.lockfile"):
        stored = read_private_bytes(path)
        if stored:
            value = stored.decode("utf-8").strip()
            if value:
                return value
        value = secrets.token_urlsafe(48)
        write_private_bytes_atomic(path, value.encode("utf-8"))
        return value


@dataclass(slots=True)
class ApiPrincipal:
    key_id: str
    name: str
    kind: str
    scopes: tuple[str, ...]
    # The credential that authenticated a Web Chat session. The visible
    # per-user key_id remains the ownership/accounting namespace.
    auth_key_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key_id": self.key_id, "name": self.name, "kind": self.kind, "scopes": list(self.scopes)}


@dataclass
class ManagedApiKey:
    key_id: str
    name: str
    token_hash: str
    prefix: str
    created_at: str
    expires_at: str | None = None
    last_used_at: str | None = None
    enabled: bool = True
    revoked_at: str | None = None
    scopes: list[str] | None = None
    token_ciphertext: str | None = None
    token_quota: int | None = None
    tokens_used: int = 0
    quota_exhausted: bool = False
    # Durable per-request ledger prevents a telemetry replay from double charging.
    charged_requests: dict[str, int] | None = None
    # Internal account-scoped identity; never exposed as a user-manageable API key.
    internal_owner_user_id: str | None = None
    # Prevent automatic workspace rebinding after an administrator explicitly unbinds it.
    tenant_binding_initialized: bool = False

    def public(self) -> dict[str, Any]:
        scopes = self.scopes or ["chat", "models", "files", "images", "audio"]
        exhausted = self.token_quota is not None and self.tokens_used >= self.token_quota
        return {
            "key_id": self.key_id,
            "name": self.name,
            "prefix": self.prefix,
            "created_at": to_beijing_iso(self.created_at) or self.created_at,
            "expires_at": to_beijing_iso(self.expires_at) if self.expires_at else None,
            "last_used_at": to_beijing_iso(self.last_used_at) if self.last_used_at else None,
            "enabled": bool(self.enabled and not exhausted and not self.revoked_at and not _expired(self.expires_at)),
            "token_quota": self.token_quota,
            "tokens_used": self.tokens_used,
            "tokens_remaining": max(0, self.token_quota - self.tokens_used) if self.token_quota is not None else None,
            "quota_exhausted": exhausted,
            "configured_enabled": self.enabled,
            "expired": _expired(self.expires_at),
            "revoked_at": to_beijing_iso(self.revoked_at) if self.revoked_at else None,
            "scopes": scopes,
            "secret_recoverable": bool(self.token_ciphertext),
        }


class ApiKeyStore:
    """Persistent business API keys.

    API authentication uses SHA-256 hashes. Recoverable key secrets are encrypted
    with a server-local data secret stored under CHAT2API_DATA_DIR; administrator
    username/password changes therefore never invalidate managed API key ciphertext.
    """

    def __init__(self, data_dir: Path, data_secret: str = "") -> None:
        self.path = data_dir / "api_keys.json"
        self.keys: dict[str, ManagedApiKey] = {}
        self.lock = asyncio.Lock()
        self.cipher = _fernet(data_secret)

    async def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stored = read_private_bytes(self.path)
        if stored is None:
            return
        try:
            payload = json.loads(stored.decode("utf-8"))
            loaded: dict[str, ManagedApiKey] = {}
            for raw in payload.get("keys", []):
                if not isinstance(raw, dict):
                    continue
                item = ManagedApiKey(**raw)
                loaded[item.key_id] = item
            self.keys = loaded
        except (OSError, ValueError, TypeError):
            self.keys = {}

    async def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"keys": [asdict(item) for item in self.keys.values()]}
        serialized = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        write_private_bytes_atomic(self.path, serialized)

    async def migrate_cipher_from(self, legacy_secret: str) -> int:
        """Re-encrypt legacy CHAT2API_API_KEY ciphertext with the data secret.

        Already-migrated entries are left untouched. Failure to decrypt a specific
        historical entry does not revoke that API key; it only remains unrecoverable.
        """
        legacy = _fernet(str(legacy_secret or ""))
        if not legacy or not self.cipher:
            return 0
        migrated = 0
        async with self.lock:
            for item in self.keys.values():
                if not item.token_ciphertext:
                    continue
                try:
                    # If current data secret already decrypts it, it is migrated.
                    self.cipher.decrypt(item.token_ciphertext.encode("ascii"))
                    continue
                except Exception:
                    pass
                try:
                    raw = legacy.decrypt(item.token_ciphertext.encode("ascii"))
                except (InvalidToken, ValueError):
                    continue
                item.token_ciphertext = self.cipher.encrypt(raw).decode("ascii")
                migrated += 1
            if migrated:
                await self.save()
        return migrated

    async def create(self, name: str, expires_at: str | None = None) -> tuple[dict[str, Any], str]:
        clean_name = str(name or "").strip() or "API Key"
        raw = "sk-chat2api-" + secrets.token_urlsafe(32)
        key_id = "key_" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")
        ciphertext = self.cipher.encrypt(raw.encode("utf-8")).decode("ascii") if self.cipher else None
        item = ManagedApiKey(
            key_id=key_id,
            name=clean_name[:120],
            token_hash=token_hash(raw),
            prefix=raw[:20],
            created_at=utc_now(),
            expires_at=to_beijing_iso(expires_at) if expires_at else None,
            scopes=["chat", "models", "files", "images", "audio"],
            token_ciphertext=ciphertext,
        )
        async with self.lock:
            self.keys[key_id] = item
            await self.save()
        return item.public(), raw

    async def authenticate(self, token: str) -> ApiPrincipal | None:
        digest = token_hash(token)
        matched: ManagedApiKey | None = None
        for item in self.keys.values():
            if item.internal_owner_user_id:
                continue
            if secrets.compare_digest(item.token_hash, digest):
                matched = item
                break
        if not matched or not matched.enabled or matched.revoked_at or _expired(matched.expires_at) or (matched.token_quota is not None and matched.tokens_used >= matched.token_quota):
            return None
        matched.last_used_at = utc_now()
        async with self.lock:
            await self.save()
        return ApiPrincipal(
            key_id=matched.key_id,
            name=matched.name,
            kind="managed",
            scopes=tuple(matched.scopes or ["chat", "models", "files", "images", "audio"]),
        )

    async def authenticate_key_id(self, key_id: str) -> ApiPrincipal | None:
        """Resolve an administrator-configured managed key without revealing its token."""
        async with self.lock:
            item = self.keys.get(str(key_id or ""))
            if (
                item is None or item.internal_owner_user_id or not item.enabled
                or item.revoked_at or _expired(item.expires_at)
                or item.token_quota is not None
            ):
                return None
            item.last_used_at = utc_now()
            await self.save()
            return ApiPrincipal(
                key_id=item.key_id,
                name=item.name,
                kind="managed",
                scopes=tuple(item.scopes or ["chat", "models", "files", "images", "audio"]),
            )

    async def ensure_web_chat_user_key(self, user_id: str) -> ApiPrincipal:
        """Create/reuse a non-secret per-user identity for Web Chat ownership."""
        owner = str(user_id or "").strip()
        if not owner or len(owner) > 128:
            raise ValueError("Invalid Web Chat user identity")
        async with self.lock:
            for item in self.keys.values():
                if item.internal_owner_user_id == owner and not item.revoked_at:
                    return ApiPrincipal(
                        key_id=item.key_id,
                        name="Web Chat",
                        kind="web_chat",
                        scopes=tuple(item.scopes or ["chat", "models", "files", "images", "audio"]),
                    )
            raw = "internal-web-chat-" + secrets.token_urlsafe(32)
            key_id = "key_" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")
            item = ManagedApiKey(
                key_id=key_id,
                name="Web Chat",
                token_hash=token_hash(raw),
                prefix="internal",
                created_at=utc_now(),
                scopes=["chat", "models", "files", "images", "audio"],
                token_ciphertext=None,
                internal_owner_user_id=owner,
            )
            self.keys[key_id] = item
            await self.save()
            return ApiPrincipal(
                key_id=key_id,
                name="Web Chat",
                kind="web_chat",
                scopes=tuple(item.scopes or ["chat", "models", "files", "images", "audio"]),
            )

    def list_public(self) -> list[dict[str, Any]]:
        return sorted(
            (item.public() for item in self.keys.values() if not item.internal_owner_user_id),
            key=lambda row: row["created_at"], reverse=True,
        )

    def get_public(self, key_id: str) -> dict[str, Any] | None:
        item = self.keys.get(key_id)
        return item.public() if item and not item.internal_owner_user_id else None

    def reveal(self, key_id: str) -> str:
        item = self.keys.get(key_id)
        if not item:
            raise KeyError("Unknown API key")
        if item.internal_owner_user_id:
            raise ValueError("Internal Web Chat identities cannot be revealed")
        if not item.token_ciphertext:
            raise ValueError("This key predates encrypted secret storage and cannot be revealed")
        if not self.cipher:
            raise ValueError("Server data encryption key is unavailable")
        try:
            return self.cipher.decrypt(item.token_ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as error:
            raise ValueError("Stored API key secret cannot be decrypted with the current data key") from error

    async def update(self, key_id: str, *, name: str | None = None, enabled: bool | None = None,
                     token_quota: int | None | object = UNSET) -> dict[str, Any]:
        async with self.lock:
            item = self.keys.get(key_id)
            if not item:
                raise KeyError("Unknown API key")
            if item.internal_owner_user_id:
                raise ValueError("Internal Web Chat identities cannot be modified")
            if item.revoked_at:
                raise ValueError("Revoked API keys cannot be modified")
            if name is not None:
                clean_name = name.strip()
                if not clean_name:
                    raise ValueError("API key name must not be empty")
                item.name = clean_name[:120]
            if token_quota is not UNSET:
                if token_quota is not None and (type(token_quota) is not int or token_quota < 0):
                    raise ValueError("Token quota must be a non-negative integer or unlimited")
                was_auto_disabled = item.quota_exhausted
                item.token_quota = token_quota
                item.quota_exhausted = token_quota is not None and item.tokens_used >= token_quota
                if item.quota_exhausted:
                    item.enabled = False
                elif was_auto_disabled and enabled is None:
                    item.enabled = True
            if enabled is not None:
                if enabled and item.token_quota is not None and item.tokens_used >= item.token_quota:
                    raise ValueError("Token quota exhausted; increase quota before enabling")
                item.enabled = bool(enabled)
            await self.save()
            return item.public()

    async def account_completed_usage(self, record: dict[str, Any]) -> None:
        """Count a completed request once for both its user and shared service key."""
        if record.get("status") != "completed":
            return
        request_id = str(record.get("request_id") or "")
        usage = record.get("usage") or {}
        try:
            amount = max(0, int(usage.get("total_tokens") or 0))
        except (TypeError, ValueError, AttributeError):
            return
        if not request_id or not amount:
            return
        key_ids = list(dict.fromkeys(
            str(value or "") for value in (
                record.get("api_key_id"), record.get("shared_api_key_id")
            ) if value
        ))
        if not key_ids:
            return
        async with self.lock:
            touched = False
            for key_id in key_ids:
                item = self.keys.get(key_id)
                if not item:
                    continue
                ledger = item.charged_requests if isinstance(item.charged_requests, dict) else {}
                prior = max(0, int(ledger.get(request_id, 0) or 0))
                if amount > prior:
                    ledger[request_id] = amount
                    item.charged_requests = ledger
                    item.tokens_used += amount - prior
                    if item.token_quota is not None and item.tokens_used >= item.token_quota:
                        item.enabled = False
                        item.quota_exhausted = True
                # Re-save even when the ledger already contains this request:
                # a previous interrupted write may have changed memory only.
                touched = True
            if touched:
                await self.save()

    async def revoke(self, key_id: str) -> dict[str, Any]:
        async with self.lock:
            item = self.keys.get(key_id)
            if not item:
                raise KeyError("Unknown API key")
            if item.internal_owner_user_id:
                raise ValueError("Internal Web Chat identities cannot be revoked")
            if not item.revoked_at:
                item.revoked_at = utc_now()
            item.enabled = False
            item.token_ciphertext = None
            await self.save()
            return item.public()
