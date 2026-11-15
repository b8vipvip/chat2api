from __future__ import annotations

import base64
import errno
import hashlib
import json
import mimetypes
import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .timezone_utils import beijing_now_iso, to_beijing_iso


def utc_now() -> str:
    """Backward-compatible helper name; canonical timestamps are Asia/Shanghai."""
    return beijing_now_iso()


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
    # Persisted content digest; empty only for legacy index rows whose content
    # was already missing when the index migration ran.
    sha256: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.file_id,
            "object": "file",
            "filename": self.filename,
            "mime_type": self.mime_type,
            "bytes": self.size,
            "created_at": to_beijing_iso(self.created_at) or self.created_at,
            "purpose": self.purpose,
        }


class FileStore:
    def __init__(self, data_dir: Path, max_file_bytes: int = 20 * 1024 * 1024) -> None:
        self.root = data_dir / "files"
        self.index_path = self.root / "index.json"
        self.max_file_bytes = max_file_bytes
        self.items: dict[str, StoredFile] = {}
        # Uploaded content and its index contain user data and must not be
        # readable by other local accounts on a shared host.
        # The JSON index is a single snapshot file. Serialize every catalog
        # mutation and read/delete boundary so concurrent uploads cannot race
        # on the shared temp path or publish a stale snapshot over newer files.
        self.lock = threading.RLock()

    def _ensure_private_root(self) -> None:
        if self.root.is_symlink():
            raise RuntimeError("File storage directory cannot be a symbolic link")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or not self.root.is_dir():
            raise RuntimeError("File storage directory is not a regular directory")
        if os.name != "nt":
            os.chmod(self.root, 0o700)

    async def load(self) -> None:
        self._ensure_private_root()
        if self.index_path.is_symlink():
            raise RuntimeError("File index cannot be a symbolic link")
        if not self.index_path.exists():
            return
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.index_path, flags)
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ValueError("file index must be a private, single-link regular file")
                if os.name != "nt" and hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "rb") as handle:
                    fd = -1
                    raw = handle.read()
            finally:
                if fd >= 0:
                    os.close(fd)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
                raise ValueError("file index root/files collection is invalid")

            loaded: dict[str, StoredFile] = {}
            disk_paths: set[str] = set()
            migrated = False
            for row in payload["files"]:
                if not isinstance(row, dict):
                    raise ValueError("file index contains a non-object row")
                value = dict(row)
                created_at = value.get("created_at")
                if not isinstance(created_at, str) or not created_at:
                    raise ValueError("file index created_at is invalid")
                value["created_at"] = to_beijing_iso(created_at) or created_at
                item = StoredFile(**value)
                if (
                    not isinstance(item.file_id, str) or not item.file_id
                    or not isinstance(item.filename, str) or not item.filename
                    or not isinstance(item.mime_type, str) or not item.mime_type
                    or not isinstance(item.owner_key_id, str) or not item.owner_key_id
                    or type(item.size) is not int or item.size <= 0
                    or not isinstance(item.path, str) or not item.path
                    or item.path in {".", ".."}
                    or "/" in item.path or "\\" in item.path or ":" in item.path
                    or Path(item.path).name != item.path
                    or not isinstance(item.purpose, str) or not item.purpose
                    or not isinstance(item.sha256, str)
                    or (item.sha256 and not re.fullmatch(r"[0-9a-f]{64}", item.sha256))
                ): 
                    raise ValueError("file index contains invalid file metadata")
                if item.file_id in loaded or item.path in disk_paths:
                    raise ValueError("file index contains duplicate file IDs or disk paths")
                disk_path = self.root / item.path
                try:
                    disk_metadata = disk_path.lstat()
                except FileNotFoundError:
                    disk_metadata = None
                if disk_metadata is not None and (
                    not stat.S_ISREG(disk_metadata.st_mode) or disk_metadata.st_nlink != 1
                    or disk_metadata.st_size != item.size
                ):
                    raise ValueError("stored file path must be a single-link regular file of the indexed size")
                if "sha256" not in value:
                    # Older indexes predate content digests. Migrate only after
                    # reading the original regular file through a no-follow fd.
                    migrated = True
                    if disk_metadata is not None:
                        fd = os.open(
                            disk_path,
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                        )
                        try:
                            opened = os.fstat(fd)
                            if (
                                not stat.S_ISREG(opened.st_mode)
                                or opened.st_nlink != 1
                                or opened.st_size != item.size
                            ):
                                raise ValueError("legacy stored file is not the indexed regular file")
                            with os.fdopen(fd, "rb") as handle:
                                fd = -1
                                legacy_payload = handle.read()
                        finally:
                            if fd >= 0:
                                os.close(fd)
                        if len(legacy_payload) != item.size:
                            raise ValueError("legacy stored file content is incomplete")
                        item.sha256 = hashlib.sha256(legacy_payload).hexdigest()
                loaded[item.file_id] = item
                disk_paths.add(item.path)

            # Publish only after the entire index has been validated. A corrupt
            # row must never turn the authoritative file catalog into an empty
            # in-memory store while leaving the original index on disk.
            with self.lock:
                previous_items = self.items
                self.items = loaded
                if migrated:
                    try:
                        self.save_index()
                    except Exception:
                        self.items = previous_items
                        raise
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
            raise RuntimeError(
                "File index is corrupt or unreadable; refusing to discard file metadata"
            ) from error

    def save_index(self) -> None:
        with self.lock:
            self._ensure_private_root()
            # Keep a unique temp path as an additional guard for callers that
            # may invoke save_index outside the normal mutation methods.
            temp = self.index_path.with_name(
                f".{self.index_path.name}.{secrets.token_hex(8)}.tmp"
            )
            fd = -1
            created_temp = False
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(temp, flags, 0o600)
                created_temp = True
                with os.fdopen(fd, "wb") as handle:
                    fd = -1
                    encoded = json.dumps(
                        {"files": [asdict(item) for item in self.items.values()]},
                        ensure_ascii=False, indent=2,
                    ).encode("utf-8")
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                # The temp file was created with mode 0600, so the atomic
                # replacement already publishes a private index. Do not perform
                # a fallible chmod after the commit point.
                os.replace(temp, self.index_path)
            finally:
                if fd >= 0:
                    os.close(fd)
                if created_temp:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        # Cleanup must not turn a committed index replacement
                        # into a reported failure that callers would roll back.
                        pass

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
        self._ensure_private_root()
        fd = -1
        created_path = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags, 0o600)
            created_path = True
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(path, 0o600)
        except Exception:
            if fd >= 0:
                os.close(fd)
            if created_path:
                path.unlink(missing_ok=True)
            raise
        item = StoredFile(
            file_id=file_id,
            filename=clean,
            mime_type=detected,
            size=len(payload),
            created_at=utc_now(),
            owner_key_id=owner_key_id,
            path=disk_name,
            purpose=purpose,
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        with self.lock:
            self.items[file_id] = item
            try:
                self.save_index()
            except Exception:
                self.items.pop(file_id, None)
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    # Keep the original persistence error; an unindexed orphan can
                    # be safely cleaned up without exposing a phantom file record.
                    pass
                raise
        return item

    def get(self, file_id: str) -> StoredFile | None:
        with self.lock:
            return self.items.get(str(file_id or ""))

    def read(self, file_id: str) -> tuple[StoredFile, bytes]:
        with self.lock:
            item = self.items.get(str(file_id or ""))
            if not item:
                raise KeyError("Unknown file_id")
            path = self.root / item.path
            try:
                # O_NONBLOCK prevents a replaced FIFO/device path from
                # hanging an API worker before fstat can reject it.
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                )
                fd = os.open(path, flags)
            except FileNotFoundError as error:
                raise FileNotFoundError("Stored file content is unavailable") from error
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise FileNotFoundError(
                        "Stored file content is not the original regular file"
                    ) from error
                raise FileNotFoundError("Stored file content is unavailable") from error
            try:
                metadata = os.fstat(fd)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_size != item.size
                ):
                    raise FileNotFoundError("Stored file content is not the original regular file")
                with os.fdopen(fd, "rb") as handle:
                    fd = -1
                    payload = handle.read()
                if len(payload) != item.size:
                    raise FileNotFoundError("Stored file content is incomplete")
                digest = hashlib.sha256(payload).hexdigest()
                if not secrets.compare_digest(digest, item.sha256):
                    raise FileNotFoundError("Stored file content digest does not match the index")
                return item, payload
            finally:
                if fd >= 0:
                    os.close(fd)

    async def delete(self, file_id: str, owner_key_id: str | None = None) -> None:
        with self.lock:
            item = self.items.get(str(file_id or ""))
            if not item:
                raise KeyError("Unknown file_id")
            # "master" is a privileged owner namespace, not a shared-file marker.
            # Only an explicitly privileged caller (None or "master") may delete
            # master-owned files; ordinary API keys must own the exact file.
            if owner_key_id is not None and owner_key_id != "master" and item.owner_key_id != owner_key_id:
                raise PermissionError("File belongs to another API key")

            path = self.root / item.path
            # Commit the catalog removal before unlinking content. If the
            # process exits between these operations, the old index still
            # points to an intact file; after the index commit, an unlink
            # failure leaves only an inaccessible orphan, never a live row
            # whose content was moved out from under it.
            self.items.pop(file_id, None)
            try:
                self.save_index()
            except Exception:
                self.items[file_id] = item
                raise

            # The index commit is the logical deletion point. Cleanup is
            # best-effort: an orphan is safe to remove offline and must never
            # resurrect a record whose deletion has already committed.
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
