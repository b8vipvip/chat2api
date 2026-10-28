"""Archive generated, downloadable files using the existing media storage settings.

Only bounded, explicitly marked, already fetched Worker file bytes are accepted.
No server-side arbitrary URL fetching and no shell, HTML, or SVG rendering.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
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

    def store(self, data: bytes, filename: str, mime: str, owner: str, request_id: str):
        name, canonical = self.validate(filename, mime, data)
        config = self.storage._config()
        if not config["auto_archive"]:
            raise ValueError("Generated file auto-archiving is disabled")
        digest = hashlib.sha256(data).hexdigest()
        # Retried terminal delivery must not manufacture a different download
        # URL for identical bytes owned by the same request. Avoid filename-only
        # identity: a genuine edited artifact has a different digest.
        with self._db() as conn:
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
        self.storage._write(config, key, data, canonical)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._db() as conn:
                conn.execute(
                    "INSERT INTO generated_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (file_id, token, owner, request_id, name, canonical, len(data),
                     digest, config["backend"], key, now),
                )
        except Exception:
            # Storage data is intentionally left recoverable if persistence fails.
            # Never issue an unindexed bearer URL.
            raise
        return {
            "id": file_id, "token": token, "filename": name, "mime_type": canonical,
            "bytes": len(data), "sha256": digest, "backend": config["backend"],
            "created_at": now, "request_id": request_id, "_created": True,
        }

    def rewrite_markdown(self, text: str, owner: str, request_id: str, base_url: str):
        source = str(text or "")
        matches = list(FILE_MARKER.finditer(source))
        if not matches:
            # Do not allow an unknown/unhandled payload to escape archival.
            if "![📎 " in source:
                raise ValueError("Generated file handoff is malformed or unsupported")
            return source, []
        if len(matches) > MAX_FILES_PER_RESPONSE:
            raise ValueError("Too many generated files in one response")
        # Phase 1: validate ALL markers and byte signatures before touching
        # storage. An invalid 4th file must not leave the first three archived.
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

        # Phase 2: the same response owns every archived object. Roll back only
        # newly written records on a backend failure, never previously published
        # files reused by an identical request replay.
        output, offset, records, created = [], 0, [], []
        cfg = self.storage._config()
        origin = (cfg.get("public_base_url") or base_url.rstrip("/"))
        try:
            for match, filename, mime, data in decoded:
                record = self.store(data, filename, mime, owner, request_id)
                if record["_created"]:
                    created.append(record["id"])
                asset = f"{origin}/api/generated-files/{record['id']}/{record['token']}"
                output.append(source[offset:match.start()])
                output.append(f"[📎 {record['filename'].replace('[', '').replace(']', '')}]({asset})")
                records.append({key: value for key, value in record.items()
                                if key not in {"token", "_created"}} |
                               {"url": asset, "kind": "file", "position": match.start(),
                                "status": "archived"})
                offset = match.end()
        except Exception:
            for file_id in reversed(created):
                try:
                    self.delete(file_id)
                except Exception:
                    # A failed remote delete must not falsely complete the
                    # response; the object remains inaccessible without token.
                    pass
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
