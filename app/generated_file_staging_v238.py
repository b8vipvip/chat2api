"""Bounded request-scoped generated-file chunk staging for the Chrome Worker.

This is a transport/archival helper, NOT a terminal owner. Only messages from
the authenticated socket assigned to a currently active Broker request may
reach ingest(). Every completed staged artifact is checked by the existing
GeneratedFileArchive before its URL is issued.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .file_store import safe_name
from .generated_files_v196 import FILE_MIMES

MAX_CHUNK_BYTES = 96 * 1024
MAX_STAGED_FILE_BYTES = 12 * 1024 * 1024
MAX_STAGED_RESPONSE_BYTES = 24 * 1024 * 1024
MAX_STAGED_FILES = 4
MAX_STAGE_AGE_SECONDS = 1800
UPLOAD_ID = re.compile(r"^[a-f0-9]{32}$")
SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")


@dataclass
class _Stage:
    request_id: str
    upload_id: str
    filename: str
    mime: str
    expected_bytes: int
    expected_sha256: str
    path: Path
    created_at: float
    digest: object
    size: int = 0
    sequence: int = 0
    finished: bool = False


class GeneratedFileStaging:
    def __init__(self, storage_root: Path):
        self.root = Path(storage_root) / "generated-file-staging"
        self.root.mkdir(parents=True, exist_ok=True)
        # Requests cannot remain active across a process restart. Any staging
        # leftovers are undiscoverable and may safely be discarded.
        for stale in self.root.glob("stg_*.part"):
            stale.unlink(missing_ok=True)
        self.lock = threading.RLock()
        self.entries: dict[tuple[str, str], _Stage] = {}

    def _prune(self) -> None:
        expired = [key for key, row in self.entries.items()
                   if time.monotonic() - row.created_at > MAX_STAGE_AGE_SECONDS]
        for key in expired:
            self.entries.pop(key).path.unlink(missing_ok=True)

    def ingest(self, *, request_id: str, upload_id: str, filename: str,
               mime: str, sequence: int, expected_bytes: int,
               expected_sha256: str, base64_chunk: str, final: bool) -> dict[str, object]:
        if (not UPLOAD_ID.fullmatch(str(upload_id or ""))
            or not SHA256_HEX.fullmatch(str(expected_sha256 or ""))
            or type(sequence) is not int or sequence < 0 or sequence > 256
            or type(expected_bytes) is not int
            or expected_bytes < 1 or expected_bytes > MAX_STAGED_FILE_BYTES
            or type(base64_chunk) is not str
            or len(base64_chunk) > 4 * ((MAX_CHUNK_BYTES + 2) // 3) + 4
            or type(final) is not bool):
            raise ValueError("invalid-staged-file-envelope")
        name = safe_name(str(filename or ""))
        suffix = Path(name).suffix.lower()
        canonical = str(mime or "").strip().lower()
        if (name != filename or suffix not in FILE_MIMES
            or canonical not in FILE_MIMES[suffix] or len(name) > 140):
            raise ValueError("invalid-staged-file-name-or-mime")
        try:
            chunk = base64.b64decode(base64_chunk, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid-staged-file-chunk") from exc
        if not 0 < len(chunk) <= MAX_CHUNK_BYTES:
            raise ValueError("invalid-staged-file-chunk-size")
        key = (request_id, upload_id)
        with self.lock:
            self._prune()
            row = self.entries.get(key)
            if row is None:
                if sequence != 0:
                    raise ValueError("staged-file-missing-initial-chunk")
                peers = [item for (rid, _), item in self.entries.items()
                         if rid == request_id]
                if len(peers) >= MAX_STAGED_FILES:
                    raise ValueError("staged-file-count-exceeded")
                if sum(item.expected_bytes for item in peers) + expected_bytes > MAX_STAGED_RESPONSE_BYTES:
                    raise ValueError("staged-file-total-budget-exceeded")
                path = self.root / ("stg_" + secrets.token_hex(24) + ".part")
                row = _Stage(request_id, upload_id, name, canonical, expected_bytes,
                             expected_sha256, path, time.monotonic(), hashlib.sha256())
                self.entries[key] = row
            if (row.finished or sequence != row.sequence
                or row.filename != name or row.mime != canonical
                or row.expected_bytes != expected_bytes
                or row.expected_sha256 != expected_sha256):
                raise ValueError("staged-file-sequence-or-metadata-mismatch")
            if row.size + len(chunk) > row.expected_bytes:
                raise ValueError("staged-file-overflow")
            with row.path.open("ab") as sink:
                sink.write(chunk)
            row.digest.update(chunk)
            row.size += len(chunk)
            row.sequence += 1
            if final:
                if (row.size != row.expected_bytes
                    or row.digest.hexdigest() != row.expected_sha256):
                    raise ValueError("staged-file-size-or-hash-mismatch")
                row.finished = True
            elif row.size == row.expected_bytes:
                raise ValueError("staged-file-complete-without-final")
            return {"accepted": True, "finished": row.finished,
                    "received_bytes": row.size, "sequence": row.sequence}

    def read(self, request_id: str, upload_id: str,
             filename: str) -> tuple[bytes, str]:
        with self.lock:
            self._prune()
            row = self.entries.get((request_id, upload_id))
            if not row or not row.finished or row.filename != filename:
                raise ValueError("Generated staged file was not verified")
            payload = row.path.read_bytes()
            if (len(payload) != row.expected_bytes
                or hashlib.sha256(payload).hexdigest() != row.expected_sha256):
                raise ValueError("Generated staged file integrity mismatch")
            return payload, row.mime

    def drop(self, request_id: str, upload_id: str) -> None:
        with self.lock:
            row = self.entries.pop((request_id, upload_id), None)
            if row:
                row.path.unlink(missing_ok=True)
