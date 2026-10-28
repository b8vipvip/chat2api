"""Archive generated, downloadable files using the existing media storage settings.

Only bounded, explicitly marked, already fetched Worker file bytes are accepted.
No server-side arbitrary URL fetching and no shell, HTML, or SVG rendering.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .file_store import safe_name

# The Worker emits ![📎 filename](data:MIME;base64,...) as an internal
# completion-only handoff. The existing image SSE gate suppresses the full
# marker until it can be replaced with a stable first-party download link.
FILE_MARKER = re.compile(
    r"!\[📎 ([^\]\r\n]{1,180})\]\("
    r"(data:([a-z0-9.+-]+/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=]+))\)",
    re.IGNORECASE,
)
MAX_FILE_BYTES = 6 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_FILES_PER_RESPONSE = 4

# Downloads only, never serve as inline HTML or script.
FILE_MIMES = {
    ".pdf": ("application/pdf",),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation",),
    ".csv": ("text/csv", "text/plain", "application/csv"),
    ".txt": ("text/plain",),
    ".md": ("text/markdown", "text/plain"),
    ".json": ("application/json", "text/json", "text/plain"),
    ".py": ("text/x-python", "text/plain", "application/octet-stream"),
    ".js": ("text/javascript", "application/javascript", "text/plain", "application/octet-stream"),
    ".ts": ("text/plain", "application/typescript", "application/octet-stream"),
    ".html": ("text/html", "text/plain"),
    ".zip": ("application/zip", "application/x-zip-compressed", "application/octet-stream"),
}

class GeneratedFileArchive:
    def __init__(self, media_archive):
        self.storage = media_archive
        self.db_path = media_archive.root / "generated_files.sqlite3"
        with self._db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS generated_files (
                id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
                request_id TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL, backend TEXT NOT NULL,
                object_key TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS generated_files_request_idx ON generated_files(request_id)")

    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def validate(filename: str, mime: str, data: bytes):
        name = safe_name(filename)
        suffix = Path(name).suffix.lower()
        canonical = str(mime or "").strip().lower().split(";", 1)[0]
        if suffix not in FILE_MIMES or canonical not in FILE_MIMES[suffix]:
            raise ValueError("Unsupported generated file type")
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError("Generated file exceeds transfer limit or is empty")
        if suffix == ".pdf" and not data.startswith(b"%PDF-"):
            raise ValueError("Generated PDF signature mismatch")
        if suffix in {".docx", ".xlsx", ".pptx", ".zip"} and not data.startswith(b"PK\x03\x04"):
            raise ValueError("Generated ZIP/Office signature mismatch")
        if suffix in {".html", ".txt", ".md", ".py", ".js", ".ts", ".csv", ".json"}:
            if b"\x00" in data[:4096]:
                raise ValueError("Text file contains unexpected binary content")
        return name, canonical

    def _store_in_transaction(self, conn, cfg, data: bytes, name: str,
                              canonical: str, owner: str, request_id: str,
                              new_objects: list[tuple[dict, str]]):
        """One SQLite write lock owns replay deduplication and object metadata."""
        digest = hashlib.sha256(data).hexdigest()
        prior = conn.execute(
            """SELECT * FROM generated_files
               WHERE owner=? AND request_id=? AND filename=? AND sha256=?
               ORDER BY created_at ASC LIMIT 1""",
            (owner, request_id, name, digest),
        ).fetchone()
        if prior:
            return {
                "id": prior["id"], "token": prior["token"],
                "filename": prior["filename"], "mime_type": prior["mime"],
                "bytes": prior["size"], "sha256": prior["sha256"],
                "backend": prior["backend"], "created_at": prior["created_at"],
                "request_id": request_id, "_created": False,
            }

        file_id = "gen_" + secrets.token_urlsafe(15).replace("-", "").replace("_", "")
        token = secrets.token_urlsafe(24)
        key = f"objects/{file_id}{Path(name).suffix.lower()}"
        # Track before upload; a partially failed local/S3 write may leave
        # an object even though _write raised. Random keys are never shared.
        new_objects.append((cfg, key))
        self.storage._write(cfg, key, data, canonical)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO generated_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (file_id, token, owner, request_id, name, canonical, len(data),
             digest, cfg["backend"], key, now),
        )
        return {
            "id": file_id, "token": token, "filename": name, "mime_type": canonical,
            "bytes": len(data), "sha256": digest, "backend": cfg["backend"],
            "created_at": now, "request_id": request_id, "_created": True,
        }

    def _rollback_unpublished_objects(self, new_objects: list[tuple[dict, str]]) -> None:
        """Remove only newly allocated random keys after the DB rolls back.

        Existing published bearer URLs and objects are never touched. This
        also handles _write failures before metadata was inserted.
        """
        for cfg, key in reversed(new_objects):
            try:
                if cfg["backend"] == "local":
                    target = (self.storage.root / key).resolve()
                    if not target.is_relative_to(self.storage.root.resolve()):
                        raise ValueError("Invalid generated file rollback key")
                    target.unlink(missing_ok=True)
                    target.with_suffix(".part").unlink(missing_ok=True)
                else:
                    self.storage._s3(cfg).delete_object(
                        Bucket=cfg["s3"]["bucket"], Key=key)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Generated file rollback failed for backend %s", cfg["backend"])

    def store(self, data: bytes, filename: str, mime: str, owner: str, request_id: str):
        name, canonical = self.validate(filename, mime, data)
        cfg = self.storage._config()
        if not cfg["auto_archive"]:
            raise ValueError("Generated file auto-archiving is disabled")
        new_objects: list[tuple[dict, str]] = []
        try:
            with self._db() as conn:
                # SQLite lock serializes independently instantiated server
                # workers/processes, not just threads within one process.
                conn.execute("BEGIN IMMEDIATE")
                return self._store_in_transaction(
                    conn, cfg, data, name, canonical, owner, request_id, new_objects)
        except Exception:
            # The context manager has rolled back before deleting objects.
            self._rollback_unpublished_objects(new_objects)
            raise

    def rewrite_markdown(self, text: str, owner: str, request_id: str, base_url: str):
        source = str(text or "")
        matches = list(FILE_MARKER.finditer(source))
        # Even one malformed marker mixed with valid entries must fail before
        # storage. Otherwise an unarchived data: payload could escape in a
        # successful completion alongside otherwise valid file links.
        marker_positions = {m.start() for m in re.finditer(re.escape("![📎 "), source)}
        if marker_positions != {m.start() for m in matches}:
            raise ValueError("Generated file handoff is malformed or unsupported")
        if not matches:
            return source, []
        if len(matches) > MAX_FILES_PER_RESPONSE:
            raise ValueError("Too many generated files in one response")

        # Phase 1: validate every payload and total budget before any writes.
        total = 0
        decoded: list[tuple[re.Match[str], str, str, bytes]] = []
        for match in matches:
            filename, mime, encoded = match.group(1), match.group(3), match.group(4)
            if len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3) + 4:
                raise ValueError("Generated file exceeds transfer limit")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Generated file handoff has invalid Base64") from exc
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("Generated files exceed response transfer limit")
            name, canonical = self.validate(filename, mime, data)
            decoded.append((match, name, canonical, data))

        cfg = self.storage._config()
        if not cfg["auto_archive"]:
            raise ValueError("Generated file auto-archiving is disabled")
        origin = (cfg.get("public_base_url") or base_url.rstrip("/"))
        output, offset, records = [], 0, []
        new_objects: list[tuple[dict, str]] = []
        try:
            # One transaction for the WHOLE reply. No client or concurrent
            # retry can observe a half-published multi-file manifest.
            with self._db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for match, filename, mime, data in decoded:
                    record = self._store_in_transaction(
                        conn, cfg, data, filename, mime, owner, request_id, new_objects)
                    asset = f"{origin}/api/generated-files/{record['id']}/{record['token']}"
                    output.append(source[offset:match.start()])
                    output.append(f"[📎 {record['filename'].replace('[', '').replace(']', '')}]({asset})")
                    records.append(
                        {key: value for key, value in record.items()
                         if key not in {"token", "_created"}} |
                        {"url": asset, "kind": "file", "position": match.start(),
                         "status": "archived"})
                    offset = match.end()
        except Exception:
            self._rollback_unpublished_objects(new_objects)
            raise
        output.append(source[offset:])
        return "".join(output), records

    def describe(self, file_id: str, owner: str, *, admin: bool = False):
        with self._db() as conn:
            row = conn.execute("SELECT * FROM generated_files WHERE id = ?", (file_id,)).fetchone()
        if not row or (row["owner"] != owner and not admin):
            raise KeyError("Unknown generated file")
        return {k: row[k] for k in ("id", "token", "request_id", "filename", "mime",
                                   "size", "sha256", "backend", "created_at")}

    def read(self, file_id: str, token: str):
        with self._db() as conn:
            row = conn.execute("SELECT * FROM generated_files WHERE id = ?", (file_id,)).fetchone()
        if not row or not secrets.compare_digest(row["token"], token):
            raise KeyError("Unknown generated file")
        data = self.storage._read(self.storage._config(), row["backend"], row["object_key"])
        # The durable manifest is not proof of the bytes fetched later from
        # local disk/S3. Never respond HTTP 200 with a truncated/replaced file.
        if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Generated file integrity verification failed")
        return data, row["mime"], row["filename"]

    def list_items(self, limit: int = 80):
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id,request_id,filename,mime,size,sha256,backend,created_at "
                "FROM generated_files ORDER BY created_at DESC LIMIT ?",
                (min(200, max(1, limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, file_id: str):
        with self._db() as conn:
            row = conn.execute("SELECT * FROM generated_files WHERE id = ?", (file_id,)).fetchone()
            if not row:
                return False
            if row["backend"] == "local":
                target = (self.storage.root / row["object_key"]).resolve()
                if not target.is_relative_to(self.storage.root.resolve()):
                    raise ValueError("Invalid generated file path")
                target.unlink(missing_ok=True)
            else:
                self.storage._s3(self.storage._config()).delete_object(
                    Bucket=self.storage._config()["s3"]["bucket"], Key=row["object_key"])
            conn.execute("DELETE FROM generated_files WHERE id = ?", (file_id,))
        return True
