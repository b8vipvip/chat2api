from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        value = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value <= datetime.now(timezone.utc)
    except ValueError:
        return True


def _fernet(master_secret: str) -> Fernet | None:
    if not master_secret:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(master_secret.encode("utf-8")).digest())
    return Fernet(key)


@dataclass(slots=True)
class ApiPrincipal:
    key_id: str
    name: str
    kind: str
    scopes: tuple[str, ...]

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

    def public(self) -> dict[str, Any]:
        scopes = self.scopes or ["chat", "models", "files", "images", "audio"]
        return {
            "key_id": self.key_id,
            "name": self.name,
            "prefix": self.prefix,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "enabled": bool(self.enabled and not self.revoked_at and not _expired(self.expires_at)),
            "configured_enabled": self.enabled,
            "expired": _expired(self.expires_at),
            "revoked_at": self.revoked_at,
            "scopes": scopes,
            "secret_recoverable": bool(self.token_ciphertext),
        }


class ApiKeyStore:
    """Persistent managed API keys.

    Authentication always uses a SHA-256 hash. For administrator convenience the
    original secret can also be stored encrypted at rest using a key derived from
    CHAT2API_API_KEY. Old v0.4 keys without ciphertext remain valid but cannot be
    revealed; create a new key if a recoverable copy is needed.
    """

    def __init__(self, data_dir: Path, master_secret: str = "") -> None:
        self.path = data_dir / "api_keys.json"
        self.keys: dict[str, ManagedApiKey] = {}
        self.lock = asyncio.Lock()
        self.cipher = _fernet(master_secret)

    async def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
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
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

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
            expires_at=expires_at,
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
            if secrets.compare_digest(item.token_hash, digest):
                matched = item
                break
        if not matched or not matched.enabled or matched.revoked_at or _expired(matched.expires_at):
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

    def list_public(self) -> list[dict[str, Any]]:
        return sorted((item.public() for item in self.keys.values()), key=lambda row: row["created_at"], reverse=True)

    def get_public(self, key_id: str) -> dict[str, Any] | None:
        item = self.keys.get(key_id)
        return item.public() if item else None

    def reveal(self, key_id: str) -> str:
        item = self.keys.get(key_id)
        if not item:
            raise KeyError("Unknown API key")
        if not item.token_ciphertext:
            raise ValueError("This key predates encrypted secret storage and cannot be revealed")
        if not self.cipher:
            raise ValueError("Server master key is unavailable for decryption")
        try:
            return self.cipher.decrypt(item.token_ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as error:
            raise ValueError("Stored API key secret cannot be decrypted with the current master key") from error

    async def update(self, key_id: str, *, name: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
        async with self.lock:
            item = self.keys.get(key_id)
            if not item:
                raise KeyError("Unknown API key")
            if item.revoked_at:
                raise ValueError("Revoked API keys cannot be modified")
            if name is not None:
                clean_name = name.strip()
                if not clean_name:
                    raise ValueError("API key name must not be empty")
                item.name = clean_name[:120]
            if enabled is not None:
                item.enabled = bool(enabled)
            await self.save()
            return item.public()

    async def revoke(self, key_id: str) -> dict[str, Any]:
        async with self.lock:
            item = self.keys.get(key_id)
            if not item:
                raise KeyError("Unknown API key")
            if not item.revoked_at:
                item.revoked_at = utc_now()
            item.enabled = False
            item.token_ciphertext = None
            await self.save()
            return item.public()
