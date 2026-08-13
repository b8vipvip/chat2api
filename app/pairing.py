from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .api_keys import load_or_create_data_secret
from .timezone_utils import beijing_now_iso, to_beijing_iso


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cipher(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


@dataclass
class PairingCode:
    pairing_id: str
    name: str
    code_hash: str
    prefix: str
    created_at: str
    code_ciphertext: str | None = None
    enabled: bool = True
    bound_client_id: str | None = None
    bound_device_id: str | None = None
    last_paired_at: str | None = None
    source: str = "console"

    def public(self) -> dict[str, Any]:
        return {
            "pairing_id": self.pairing_id,
            "name": self.name,
            "prefix": self.prefix,
            "created_at": to_beijing_iso(self.created_at) or self.created_at,
            "enabled": self.enabled,
            "bound_client_id": self.bound_client_id,
            "bound_device_id": self.bound_device_id,
            "last_paired_at": to_beijing_iso(self.last_paired_at) if self.last_paired_at else None,
            "source": self.source,
            "secret_recoverable": bool(self.code_ciphertext),
        }


class PairingStore:
    """Persistent one-device pairing codes.

    Pairing authentication still compares SHA-256 digests. Starting with v0.18,
    the raw pairing code is additionally encrypted with the server-local data key
    so an authenticated administrator can copy it from the console later. Older
    hash-only records are safely rotated to a new code on first copy request.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "pairing_codes.json"
        self.items: dict[str, PairingCode] = {}
        self.lock = asyncio.Lock()
        self.cipher = _cipher(load_or_create_data_secret(data_dir))
        self.loaded = False

    async def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.loaded = True
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            loaded: dict[str, PairingCode] = {}
            for raw in payload.get("pairing_codes", []):
                if not isinstance(raw, dict):
                    continue
                item = PairingCode(**raw)
                loaded[item.pairing_id] = item
            self.items = loaded
        except (OSError, ValueError, TypeError):
            self.items = {}
        self.loaded = True

    async def ensure_loaded(self) -> None:
        if not self.loaded:
            await self.load()

    async def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pairing_codes": [asdict(item) for item in self.items.values()]}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def _encrypt(self, raw: str) -> str:
        return self.cipher.encrypt(raw.encode("utf-8")).decode("ascii")

    async def create(self, name: str = "Chrome 扩展") -> tuple[dict[str, Any], str]:
        await self.ensure_loaded()
        raw = "pair-" + secrets.token_urlsafe(18)
        item = PairingCode(
            pairing_id="pair_" + secrets.token_urlsafe(8).replace("-", "").replace("_", ""),
            name=(str(name or "Chrome 扩展").strip() or "Chrome 扩展")[:120],
            code_hash=_hash(raw),
            prefix=raw[:12],
            created_at=beijing_now_iso(),
            code_ciphertext=self._encrypt(raw),
        )
        async with self.lock:
            self.items[item.pairing_id] = item
            await self.save()
        return item.public(), raw

    async def seed_legacy(self, code: str) -> None:
        await self.ensure_loaded()
        clean = str(code or "").strip()
        if not clean or clean == "change-me-pairing":
            return
        digest = _hash(clean)
        for item in self.items.values():
            if item.code_hash and secrets.compare_digest(item.code_hash, digest):
                if not item.code_ciphertext:
                    async with self.lock:
                        item.code_ciphertext = self._encrypt(clean)
                        await self.save()
                return
        item = PairingCode(
            pairing_id="pair_legacy_" + secrets.token_hex(4),
            name="旧版 .env 配对码",
            code_hash=digest,
            prefix=clean[:12],
            created_at=beijing_now_iso(),
            code_ciphertext=self._encrypt(clean),
            source="legacy-env",
        )
        async with self.lock:
            self.items[item.pairing_id] = item
            await self.save()

    def authorize(self, raw_code: str, device_id: str) -> PairingCode | None:
        digest = _hash(str(raw_code or ""))
        clean_device = str(device_id or "").strip()
        if not clean_device:
            return None
        for item in self.items.values():
            if not item.enabled or not item.code_hash:
                continue
            if not secrets.compare_digest(item.code_hash, digest):
                continue
            if item.bound_device_id and item.bound_device_id != clean_device:
                raise PermissionError("Pairing code is already bound to another extension device")
            return item
        return None

    async def bind(self, pairing_id: str, client_id: str, device_id: str) -> PairingCode:
        await self.ensure_loaded()
        async with self.lock:
            item = self.items[pairing_id]
            if item.bound_device_id and item.bound_device_id != device_id:
                raise PermissionError("Pairing code is already bound to another extension device")
            item.bound_client_id = client_id
            item.bound_device_id = device_id
            item.last_paired_at = beijing_now_iso()
            await self.save()
            return item

    async def set_enabled(self, pairing_id: str, enabled: bool) -> dict[str, Any]:
        await self.ensure_loaded()
        async with self.lock:
            item = self.items.get(pairing_id)
            if not item:
                raise KeyError("Unknown pairing code")
            item.enabled = bool(enabled)
            await self.save()
            return item.public()

    async def reveal_or_rotate(self, pairing_id: str) -> tuple[str, bool]:
        """Return the pairing secret, rotating legacy hash-only records if needed."""
        await self.ensure_loaded()
        async with self.lock:
            item = self.items.get(pairing_id)
            if not item:
                raise KeyError("Unknown pairing code")
            if item.code_ciphertext:
                try:
                    raw = self.cipher.decrypt(item.code_ciphertext.encode("ascii")).decode("utf-8")
                    if secrets.compare_digest(_hash(raw), item.code_hash):
                        return raw, False
                except (InvalidToken, ValueError, UnicodeDecodeError):
                    pass
            raw = "pair-" + secrets.token_urlsafe(18)
            item.code_hash = _hash(raw)
            item.prefix = raw[:12]
            item.code_ciphertext = self._encrypt(raw)
            await self.save()
            return raw, True

    async def delete(self, pairing_id: str) -> dict[str, Any]:
        await self.ensure_loaded()
        async with self.lock:
            item = self.items.pop(pairing_id, None)
            if not item:
                raise KeyError("Unknown pairing code")
            await self.save()
            return item.public()

    def list_public(self) -> list[dict[str, Any]]:
        return sorted((item.public() for item in self.items.values()), key=lambda x: x["created_at"], reverse=True)

    def get(self, pairing_id: str) -> PairingCode | None:
        return self.items.get(pairing_id)
