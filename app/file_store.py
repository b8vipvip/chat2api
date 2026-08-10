from __future__ import annotations

import base64
import json
import mimetypes
import re
import secrets
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    name = Path(str(value or "upload.bin")).name
    name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name).strip("._")
    return (name or "upload.bin")[:180]


@dataclass
class StoredFile:
    file_id: str
    filename: str
    mime_type: str
    size: int
    created_at: str
    owner_key_id: str
    path: str
    purpose: str = "chat2api"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.file_id,
            "object": "file",
            "filename": self.filename,
            "mime_type": self.mime_type,
            "bytes": self.size,
            "created_at": self.created_at,
            "purpose": self.purpose,
        }


class FileStore:
    def __init__(self, data_dir: Path, max_file_bytes: int = 20 * 1024 * 1024) -> None:
        self.root = data_dir / "files"
        self.index_path = self.root / "index.json"
        self.max_file_bytes = max_file_bytes
        self.items: dict[str, StoredFile] = {}

    async def load(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            return
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            self.items = {
                row["file_id"]: StoredFile(**row)
                for row in payload.get("files", [])
                if isinstance(row, dict) and row.get("file_id")
            }
        except (OSError, ValueError, TypeError, KeyError):
            self.items = {}

    def save_index(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.index_path.with_suffix(".tmp")
        temp.write_text(
            json.dumps({"files": [asdict(item) for item in self.items.values()]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.index_path)

    def _decode(self, data_base64: str, mime_type: str | None) -> tuple[bytes, str]:
        raw = str(data_base64 or "").strip()
        detected = str(mime_type or "").strip()
        if raw.startswith("data:"):
            header, _, encoded = raw.partition(",")
            if not encoded:
                raise ValueError("Invalid data URL")
            match = re.match(r"data:([^;,]+)?(?:;charset=[^;,]+)?;base64$", header, re.I)
            if not match:
                raise ValueError("Only base64 data URLs are supported")
            detected = detected or (match.group(1) or "application/octet-stream")
            raw = encoded
        try:
            payload = base64.b64decode(raw, validate=True)
        except Exception as error:
            raise ValueError("data_base64 is not valid base64") from error
        if not payload:
            raise ValueError("Uploaded file is empty")
        if len(payload) > self.max_file_bytes:
            raise ValueError(f"File is too large; maximum is {self.max_file_bytes // (1024 * 1024)} MiB")
        return payload, detected or "application/octet-stream"

    async def create(
        self,
        *,
        filename: str,
        data_base64: str,
        mime_type: str | None,
        owner_key_id: str,
        purpose: str = "chat2api",
    ) -> StoredFile:
        payload, detected = self._decode(data_base64, mime_type)
        clean = safe_name(filename)
        if detected == "application/octet-stream":
            detected = mimetypes.guess_type(clean)[0] or detected
        file_id = "file_" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")
        suffix = Path(clean).suffix[:16]
        disk_name = f"{file_id}{suffix}"
        path = self.root / disk_name
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        item = StoredFile(
            file_id=file_id,
            filename=clean,
            mime_type=detected,
            size=len(payload),
            created_at=utc_now(),
            owner_key_id=owner_key_id,
            path=disk_name,
            purpose=purpose,
        )
        self.items[file_id] = item
        self.save_index()
        return item

    def get(self, file_id: str) -> StoredFile | None:
        return self.items.get(str(file_id or ""))

    def read(self, file_id: str) -> tuple[StoredFile, bytes]:
        item = self.get(file_id)
        if not item:
            raise KeyError("Unknown file_id")
        path = self.root / item.path
        if not path.exists():
            raise FileNotFoundError("Stored file content is missing")
        return item, path.read_bytes()

    async def delete(self, file_id: str, owner_key_id: str | None = None) -> None:
        item = self.get(file_id)
        if not item:
            raise KeyError("Unknown file_id")
        if owner_key_id and item.owner_key_id not in {owner_key_id, "master"} and owner_key_id != "master":
            raise PermissionError("File belongs to another API key")
        try:
            (self.root / item.path).unlink(missing_ok=True)
        finally:
            self.items.pop(file_id, None)
            self.save_index()
