from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .timezone_utils import beijing_now_iso, to_beijing_iso


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class PairingCode:
    pairing_id: str
    name: str
    code_hash: str
    prefix: str
    created_at: str
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
        }


class PairingStore:
    """Persistent one-device pairing codes.

    A code becomes bound to the first extension device that uses it. The raw code is
    returned only at creation time; disk persistence keeps only a SHA-256 digest.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "pairing_codes.json"
        self.items: dict[str, PairingCode] = {}
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
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

    async def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pairing_codes": [asdict(item) for item in self.items.values()]}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    async def create(self, name: str = "Chrome 扩展") -> tuple[dict[str, Any], str]:
        raw = "pair-" + secrets.token_urlsafe(18)
        item = PairingCode(
            pairing_id="pair_" + secrets.token_urlsafe(8).replace("-", "").replace("_", ""),
            name=(str(name or "Chrome 扩展").strip() or "Chrome 扩展")[:120],
            code_hash=_hash(raw),
            prefix=raw[:12],
            created_at=beijing_now_iso(),
        )
        async with self.lock:
            self.items[item.pairing_id] = item
            await self.save()
        return item.public(), raw

    async def seed_legacy(self, code: str) -> None:
        clean = str(code or "").strip()
        if not clean or clean == "change-me-pairing":
            return
        digest = _hash(clean)
        if any(secrets.compare_digest(item.code_hash, digest) for item in self.items.values() if item.code_hash):
            return
        item = PairingCode(
            pairing_id="pair_legacy_" + secrets.token_hex(4),
            name="旧版 .env 配对码",
            code_hash=digest,
            prefix=clean[:12],
            created_at=beijing_now_iso(),
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
        async with self.lock:
            item = self.items.get(pairing_id)
            if not item:
                raise KeyError("Unknown pairing code")
            item.enabled = bool(enabled)
            await self.save()
            return item.public()

    def list_public(self) -> list[dict[str, Any]]:
        return sorted((item.public() for item in self.items.values()), key=lambda x: x["created_at"], reverse=True)

    def get(self, pairing_id: str) -> PairingCode | None:
        return self.items.get(pairing_id)
