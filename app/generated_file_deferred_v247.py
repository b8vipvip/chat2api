"""Durable, bounded post-terminal archive jobs for verified generated file bytes.

Only already captured, signature-checked Worker bytes enter this queue. The
request-v6 Worker alone decides terminal completion. This component owns
archival/availability, not request status or browser/window lifetime.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .generated_files_v196 import (
    FILE_MARKER, STAGED_MARKER, MAX_FILES_PER_RESPONSE,
    MAX_FILE_BYTES, MAX_INLINE_FILE_BYTES, MAX_TOTAL_BYTES,
)


class FileStillProcessing(Exception):
    pass


class FileTransferFailed(Exception):
    pass


class DeferredGeneratedFiles:
    def __init__(self, archive):
        self.archive = archive
        self.root = archive.storage.root / "generated-file-pending"
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="generated-file-archive")
        self.lock = threading.RLock()
        with self._db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS deferred_generated_files (
                id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
                request_id TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL, state TEXT NOT NULL,
                file_id TEXT, file_token TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS deferred_files_request_idx ON deferred_generated_files(request_id)")
            # A restart may have interrupted the actual cloud write. Store()
            # deduplicates by owner/request/name/hash in a SQLite transaction.
            conn.execute("UPDATE deferred_generated_files SET state='pending' WHERE state='processing'")
            rows = conn.execute("SELECT id FROM deferred_generated_files WHERE state='pending'").fetchall()
        for row in rows:
            self.pool.submit(self._finish, row["id"])

    def _db(self):
        conn = sqlite3.connect(self.archive.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    def rewrite(self, text: str, owner: str, request_id: str, base_url: str):
        source = str(text or "")
        matches = sorted([*FILE_MARKER.finditer(source), *STAGED_MARKER.finditer(source)],
                         key=lambda match: match.start())
        import re
        positions = {m.start() for m in re.finditer(re.escape("![📎 "), source)}
        if positions != {m.start() for m in matches}:
            raise ValueError("Generated file handoff is malformed or unsupported")
        if not matches:
            return source, []
        if len(matches) > MAX_FILES_PER_RESPONSE:
            raise ValueError("Too many generated files in one response")
        config = self.archive.storage._config()
        if not config["auto_archive"]:
            raise ValueError("Generated file auto-archiving is disabled")
        origin = (config.get("public_base_url") or base_url.rstrip("/"))
        decoded = []
        total = 0
        for match in matches:
            name = match.group(1)
            if match.re is STAGED_MARKER:
                if not getattr(self.archive, "staging", None):
                    raise ValueError("Generated staged upload transport unavailable")
                data, mime = self.archive.staging.read(request_id, match.group(2), name)
            else:
                mime, encoded = match.group(3), match.group(4)
                if len(encoded) > 4 * ((MAX_INLINE_FILE_BYTES + 2) // 3) + 4:
                    raise ValueError("Generated inline file exceeds transfer limit")
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("Generated file handoff has invalid Base64") from exc
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("Generated files exceed response transfer limit")
            canonical_name, canonical_mime = self.archive.validate(name, mime, data)
            decoded.append((match, canonical_name, canonical_mime, data))

        # Stage all bytes on durable disk BEFORE public URLs leave the server.
        # Atomic file creation is bounded by the same existing file limits.
        results, output, offset = [], [], 0
        submitted = []
        try:
            with self.lock:
                for match, filename, mime, data in decoded:
                    file_id = "pgen_" + secrets.token_hex(16)
                    token = secrets.token_urlsafe(24)
                    path = self.root / (file_id + ".bin")
                    staged = path.with_suffix(".part")
                    staged.write_bytes(data)
                    staged.replace(path)
                    now = datetime.now(timezone.utc).isoformat()
                    with self._db() as conn:
                        conn.execute("""INSERT INTO deferred_generated_files
                            (id,token,owner,request_id,filename,mime,size,sha256,
                             state,file_id,file_token,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (file_id,token,owner,request_id,filename,mime,len(data),
                             hashlib.sha256(data).hexdigest(),"pending",None,None,now,now))
                    submitted.append(file_id)
                    url = f"{origin}/api/generated-files/pending/{file_id}/{token}"
                    output.extend((source[offset:match.start()],
                                   f"[📎 {filename.replace('[','').replace(']','')}]({url})"))
                    results.append({"id":file_id,"filename":filename,"mime_type":mime,
                                    "bytes":len(data),"request_id":request_id,
                                    "kind":"file","url":url,"position":match.start(),
                                    "status":"processing"})
                    offset = match.end()
                output.append(source[offset:])
                for item in submitted:
                    self.pool.submit(self._finish, item)
            return "".join(output), results
        except Exception:
            # Never leave a partially published response: this method raises,
            # so no pending link is returned. Clean only files from this batch.
            with self._db() as conn:
                for item in submitted:
                    conn.execute("DELETE FROM deferred_generated_files WHERE id=?",(item,))
                    (self.root / (item + ".bin")).unlink(missing_ok=True)
            raise

    def _finish(self, pending_id: str) -> None:
        with self._db() as conn:
            updated = conn.execute(
                "UPDATE deferred_generated_files SET state='processing' WHERE id=? AND state='pending'",
                (pending_id,),
            )
            if updated.rowcount != 1:
                return
            row = conn.execute("SELECT * FROM deferred_generated_files WHERE id=?", (pending_id,)).fetchone()
        path = self.root / (pending_id + ".bin")
        try:
            data = path.read_bytes()
            if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("Deferred generated file staging integrity mismatch")
            saved = self.archive.store(data,row["filename"],row["mime"],row["owner"],row["request_id"])
            with self._db() as conn:
                conn.execute("""UPDATE deferred_generated_files
                    SET state='ready',file_id=?,file_token=?,updated_at=?
                    WHERE id=? AND state='processing'""",
                    (saved["id"],saved["token"],datetime.now(timezone.utc).isoformat(),pending_id))
            path.unlink(missing_ok=True)
        except Exception:
            with self._db() as conn:
                conn.execute("UPDATE deferred_generated_files SET state='failed',updated_at=? WHERE id=?",
                             (datetime.now(timezone.utc).isoformat(), pending_id))
            # Retain no raw content after a failed job; the client sees an
            # explicit retriable failure rather than a counterfeit download.
            path.unlink(missing_ok=True)

    def read(self, pending_id: str, token: str):
        if not pending_id.startswith("pgen_") or len(pending_id)!=37:
            raise KeyError("Unknown generated file")
        with self._db() as conn:
            row = conn.execute("SELECT * FROM deferred_generated_files WHERE id=? AND token=?",
                               (pending_id,token)).fetchone()
        if not row:
            raise KeyError("Unknown generated file")
        if row["state"] in ("pending","processing"):
            raise FileStillProcessing("Generated file transfer is still processing")
        if row["state"]!="ready" or not row["file_id"] or not row["file_token"]:
            raise FileTransferFailed("Generated file transfer could not be completed")
        return self.archive.read(row["file_id"],row["file_token"])

    def status(self, pending_id: str, token: str):
        with self._db() as conn:
            row=conn.execute("SELECT state,filename FROM deferred_generated_files WHERE id=? AND token=?",
                             (pending_id,token)).fetchone()
        if not row:
            raise KeyError("Unknown generated file")
        return {"status":row["state"],"filename":row["filename"]}
