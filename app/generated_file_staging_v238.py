"""Bounded, request-scoped generated-file staging with restart-safe completion.

Only messages from the authenticated socket assigned to an active Broker request
may reach ingest(). Completed stages are durably indexed and hash-verified on
startup so a Worker terminal replay can finish archival after an API restart.
Incomplete stages are deliberately discarded: they are not proof of a complete
attachment and must never be exposed as generated files.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
import sqlite3
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
PERSISTED_STAGE_PRUNE_INTERVAL_SECONDS = 60.0
UPLOAD_ID = re.compile(r"^[a-f0-9]{32}$")
SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")
STAGE_PATH = re.compile(r"^stg_[a-f0-9]{48}\.part$")


@dataclass
class _Stage:
    request_id: str
    upload_id: str
    owner_key_id: str
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
        if self.root.is_symlink():
            raise ValueError("Generated staging root must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db_path = self.root / "index.sqlite3"
        if self.db_path.is_symlink():
            raise ValueError("Generated staging index must not be a symlink")
        self.lock = threading.RLock()
        self.entries: dict[tuple[str, str], _Stage] = {}
        self._last_persisted_prune_mono = 0.0
        self._init_db()
        os.chmod(self.db_path, 0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.db_path) + suffix)
            if sidecar.exists() and not sidecar.is_symlink():
                os.chmod(sidecar, 0o600)
        self._restore_completed()

    def _db(self):
        if self.db_path.is_symlink():
            raise ValueError("Generated staging index must not be a symlink")
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _init_db(self) -> None:
        with self._db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS generated_file_stages (
                request_id TEXT NOT NULL,
                owner_key_id TEXT NOT NULL DEFAULT '',
                upload_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime TEXT NOT NULL,
                expected_bytes INTEGER NOT NULL,
                expected_sha256 TEXT NOT NULL,
                path_name TEXT NOT NULL,
                created_at REAL NOT NULL,
                size INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                finished INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(request_id, upload_id),
                UNIQUE(path_name)
            )""")
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(generated_file_stages)")
            }
            if "owner_key_id" not in columns:
                conn.execute(
                    "ALTER TABLE generated_file_stages "
                    "ADD COLUMN owner_key_id TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS generated_file_stages_created_idx "
                "ON generated_file_stages(created_at)"
            )

    def _delete_persisted(self, request_id: str, upload_id: str, path: Path | None = None) -> None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT path_name FROM generated_file_stages WHERE request_id=? AND upload_id=?",
                (request_id, upload_id),
            ).fetchone()
            conn.execute(
                "DELETE FROM generated_file_stages WHERE request_id=? AND upload_id=?",
                (request_id, upload_id),
            )
        target = path
        if target is None and row is not None:
            path_name = str(row["path_name"] or "")
            if STAGE_PATH.fullmatch(path_name):
                target = self.root / path_name
        if target is not None:
            target.unlink(missing_ok=True)

    def _restore_completed(self) -> None:
        now_wall = time.time()
        now_mono = time.monotonic()
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM generated_file_stages ORDER BY created_at, request_id, upload_id"
            ).fetchall()

        retained_paths: set[str] = set()
        request_counts: dict[str, int] = {}
        request_bytes: dict[str, int] = {}
        for raw in rows:
            request_id = str(raw["request_id"] or "")
            owner_key_id = str(raw["owner_key_id"] or "")
            upload_id = str(raw["upload_id"] or "")
            filename = str(raw["filename"] or "")
            mime = str(raw["mime"] or "").strip().lower()
            path_name = str(raw["path_name"] or "")
            expected_bytes = raw["expected_bytes"]
            expected_sha256 = str(raw["expected_sha256"] or "").lower()
            created_at = raw["created_at"]
            try:
                size = int(raw["size"])
                sequence = int(raw["sequence"])
                expected = int(expected_bytes)
                created = float(created_at)
            except (TypeError, ValueError, OverflowError):
                self._delete_persisted(request_id, upload_id)
                continue

            suffix = Path(filename).suffix.lower()
            valid = (
                0 < len(request_id) <= 128
                and not any(ord(char) < 32 or ord(char) == 127 for char in request_id)
                and 0 < len(owner_key_id) <= 160
                and not any(ord(char) < 32 or ord(char) == 127 for char in owner_key_id)
                and bool(UPLOAD_ID.fullmatch(upload_id))
                and bool(STAGE_PATH.fullmatch(path_name))
                and safe_name(filename) == filename
                and len(filename) <= 140
                and suffix in FILE_MIMES
                and mime in FILE_MIMES[suffix]
                and 1 <= expected <= MAX_STAGED_FILE_BYTES
                and bool(SHA256_HEX.fullmatch(expected_sha256))
                and 0 <= size <= expected
                and 0 <= sequence <= 257
                and created > 0
            )
            age = max(0.0, now_wall - created) if created > 0 else MAX_STAGE_AGE_SECONDS + 1
            if not valid or age > MAX_STAGE_AGE_SECONDS:
                self._delete_persisted(request_id, upload_id)
                continue

            path = self.root / path_name
            try:
                if (
                    path.is_symlink()
                    or path.resolve().parent != self.root.resolve()
                    or not path.is_file()
                ):
                    raise ValueError("staged file missing or unsafe")
                payload = path.read_bytes()
            except (OSError, ValueError):
                self._delete_persisted(request_id, upload_id, path)
                continue

            # A crash can occur after the final bytes reach disk but before the
            # finished=1 SQLite update. Exact size+digest is sufficient proof
            # to promote that stage; partial or corrupt bytes are discarded.
            complete = (
                len(payload) == expected
                and hashlib.sha256(payload).hexdigest() == expected_sha256
            )
            if not complete:
                # Another API process may still be appending this fresh stage.
                # Do not let a concurrent process startup destroy its bytes;
                # incomplete entries are never restored or exposed, and the
                # normal TTL/drop_request cleanup remains responsible for them.
                if not bool(raw["finished"]):
                    retained_paths.add(path_name)
                    continue
                self._delete_persisted(request_id, upload_id, path)
                continue

            count = request_counts.get(request_id, 0)
            total = request_bytes.get(request_id, 0)
            if count >= MAX_STAGED_FILES or total + expected > MAX_STAGED_RESPONSE_BYTES:
                self._delete_persisted(request_id, upload_id, path)
                continue

            digest = hashlib.sha256()
            digest.update(payload)
            stage = _Stage(
                request_id=request_id,
                upload_id=upload_id,
                owner_key_id=owner_key_id,
                filename=filename,
                mime=mime,
                expected_bytes=expected,
                expected_sha256=expected_sha256,
                path=path,
                created_at=now_mono - age,
                digest=digest,
                size=expected,
                sequence=max(1, sequence),
                finished=True,
            )
            self.entries[(request_id, upload_id)] = stage
            retained_paths.add(path_name)
            request_counts[request_id] = count + 1
            request_bytes[request_id] = total + expected
            with self._db() as conn:
                conn.execute(
                    """UPDATE generated_file_stages
                       SET size=?, sequence=?, finished=1
                       WHERE request_id=? AND upload_id=?""",
                    (expected, stage.sequence, request_id, upload_id),
                )

        # Remove orphan files created before a metadata insert or left by a
        # crash during rollback. The SQLite index is the only restore authority.
        for path in self.root.glob("stg_*.part"):
            if path.name not in retained_paths:
                path.unlink(missing_ok=True)

    def _prune(self) -> None:
        # A second API process deliberately does not restore another process's
        # incomplete upload. Those rows are therefore absent from self.entries;
        # prune their durable index and bytes periodically once the shared TTL
        # expires. Do not take a SQLite write lock for every upload chunk.
        now_mono = time.monotonic()
        if now_mono - self._last_persisted_prune_mono >= PERSISTED_STAGE_PRUNE_INTERVAL_SECONDS:
            cutoff = time.time() - MAX_STAGE_AGE_SECONDS
            removed: list[tuple[str, str, str]] = []
            try:
                with self._db() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    rows = conn.execute(
                        """SELECT request_id, upload_id, path_name, created_at
                           FROM generated_file_stages
                           WHERE created_at<=?
                           ORDER BY created_at ASC LIMIT 500""",
                        (cutoff,),
                    ).fetchall()
                    for raw in rows:
                        request_id = str(raw["request_id"] or "")
                        upload_id = str(raw["upload_id"] or "")
                        path_name = str(raw["path_name"] or "")
                        if STAGE_PATH.fullmatch(path_name):
                            try:
                                # unlink removes a symlink itself; it never follows it.
                                (self.root / path_name).unlink(missing_ok=True)
                            except OSError:
                                # Keep the durable row so a later prune can retry.
                                continue
                        deleted = conn.execute(
                            """DELETE FROM generated_file_stages
                               WHERE request_id=? AND upload_id=? AND path_name=? AND created_at=?""",
                            (request_id, upload_id, path_name, raw["created_at"]),
                        ).rowcount
                        if deleted:
                            removed.append((request_id, upload_id, path_name))
            except sqlite3.Error:
                # Cleanup is best effort; a later request retries it. Upload
                # admission and reads must not fail just because pruning is busy.
                pass
            else:
                self._last_persisted_prune_mono = now_mono
                for request_id, upload_id, path_name in removed:
                    key = (request_id, upload_id)
                    entry = self.entries.get(key)
                    if entry is not None and entry.path.name == path_name:
                        self.entries.pop(key, None)

        # Keep the monotonic-clock guard for in-memory entries if the wall
        # clock moved backwards or a legacy index row is missing.
        expired = [
            key for key, row in self.entries.items()
            if time.monotonic() - row.created_at > MAX_STAGE_AGE_SECONDS
        ]
        for request_id, upload_id in expired:
            key = (request_id, upload_id)
            row = self.entries.get(key)
            if row is not None:
                try:
                    row.path.unlink(missing_ok=True)
                except OSError:
                    # Keep both the in-memory entry and durable row retryable.
                    continue
                self.entries.pop(key, None)
                self._delete_persisted(request_id, upload_id, row.path)

    def ingest(
        self, *, request_id: str, owner_key_id: str, upload_id: str, filename: str,
        mime: str, sequence: int, expected_bytes: int,
        expected_sha256: str, base64_chunk: str, final: bool,
    ) -> dict[str, object]:
        request_id = str(request_id or "")
        owner_key_id = str(owner_key_id or "")
        if (
            not request_id or len(request_id) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in request_id)
            or not owner_key_id or len(owner_key_id) > 160
            or any(ord(char) < 32 or ord(char) == 127 for char in owner_key_id)
            or not UPLOAD_ID.fullmatch(str(upload_id or ""))
            or not SHA256_HEX.fullmatch(str(expected_sha256 or ""))
            or type(sequence) is not int or sequence < 0 or sequence > 256
            or type(expected_bytes) is not int
            or expected_bytes < 1 or expected_bytes > MAX_STAGED_FILE_BYTES
            or type(base64_chunk) is not str
            or len(base64_chunk) > 4 * ((MAX_CHUNK_BYTES + 2) // 3) + 4
            or type(final) is not bool
        ):
            raise ValueError("invalid-staged-file-envelope")
        name = safe_name(str(filename or ""))
        suffix = Path(name).suffix.lower()
        canonical = str(mime or "").strip().lower()
        if (
            name != filename or suffix not in FILE_MIMES
            or canonical not in FILE_MIMES[suffix] or len(name) > 140
        ):
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
                peers = [item for (rid, _), item in self.entries.items() if rid == request_id]
                if len(peers) >= MAX_STAGED_FILES:
                    raise ValueError("staged-file-count-exceeded")
                if sum(item.expected_bytes for item in peers) + expected_bytes > MAX_STAGED_RESPONSE_BYTES:
                    raise ValueError("staged-file-total-budget-exceeded")
                path_name = "stg_" + secrets.token_hex(24) + ".part"
                path = self.root / path_name
                path.touch(mode=0o600, exist_ok=False)
                os.chmod(path, 0o600)
                row = _Stage(
                    request_id, upload_id, owner_key_id, name, canonical, expected_bytes,
                    expected_sha256, path, time.monotonic(), hashlib.sha256(),
                )
                try:
                    with self._db() as conn:
                        conn.execute(
                            """INSERT INTO generated_file_stages
                               (request_id, owner_key_id, upload_id, filename, mime, expected_bytes,
                                expected_sha256, path_name, created_at, size, sequence, finished)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
                            (
                                request_id, owner_key_id, upload_id, name, canonical, expected_bytes,
                                expected_sha256, path_name, time.time(), 0, 0,
                            ),
                        )
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
                self.entries[key] = row

            if (
                row.finished or row.owner_key_id != owner_key_id
                or sequence != row.sequence
                or row.filename != name or row.mime != canonical
                or row.expected_bytes != expected_bytes
                or row.expected_sha256 != expected_sha256
            ):
                raise ValueError("staged-file-sequence-or-metadata-mismatch")
            if row.size + len(chunk) > row.expected_bytes:
                raise ValueError("staged-file-overflow")

            previous_size = row.size
            try:
                if row.path.is_symlink() or row.path.resolve().parent != self.root.resolve():
                    raise ValueError("staged file path changed")
                with row.path.open("ab") as sink:
                    sink.write(chunk)
                    sink.flush()
                    os.fsync(sink.fileno())
                row.digest.update(chunk)
                row.size += len(chunk)
                row.sequence += 1
                if final:
                    if (
                        row.size != row.expected_bytes
                        or row.digest.hexdigest() != row.expected_sha256
                    ):
                        raise ValueError("staged-file-size-or-hash-mismatch")
                    row.finished = True
                elif row.size == row.expected_bytes:
                    raise ValueError("staged-file-complete-without-final")
                with self._db() as conn:
                    updated = conn.execute(
                        """UPDATE generated_file_stages
                           SET size=?, sequence=?, finished=?
                           WHERE request_id=? AND upload_id=? AND path_name=?""",
                        (
                            row.size, row.sequence, 1 if row.finished else 0,
                            request_id, upload_id, row.path.name,
                        ),
                    ).rowcount
                    if updated != 1:
                        raise OSError("staged-file metadata row disappeared")
            except Exception:
                # Never acknowledge a chunk whose bytes or metadata could not
                # be made durable. Drop the whole stage so a retry must restart
                # at sequence zero rather than append onto an ambiguous file.
                self.entries.pop(key, None)
                self._delete_persisted(request_id, upload_id, row.path)
                raise

            return {
                "accepted": True,
                "finished": row.finished,
                "received_bytes": row.size,
                "sequence": row.sequence,
            }

    def read(
        self, request_id: str, upload_id: str, filename: str, *, owner_key_id: str,
    ) -> tuple[bytes, str]:
        key = (str(request_id or ""), str(upload_id or ""))
        owner_key_id = str(owner_key_id or "")
        with self.lock:
            self._prune()
            row = self.entries.get(key)
            if (
                not row or not row.finished or row.filename != filename
                or not owner_key_id or row.owner_key_id != owner_key_id
            ):
                raise ValueError("Generated staged file was not verified")
            try:
                if row.path.is_symlink() or row.path.resolve().parent != self.root.resolve():
                    raise ValueError("staged file path changed")
                payload = row.path.read_bytes()
            except OSError as exc:
                self.entries.pop(key, None)
                self._delete_persisted(key[0], key[1], row.path)
                raise ValueError("Generated staged file was not verified") from exc
            if (
                len(payload) != row.expected_bytes
                or hashlib.sha256(payload).hexdigest() != row.expected_sha256
            ):
                self.entries.pop(key, None)
                self._delete_persisted(key[0], key[1], row.path)
                raise ValueError("Generated staged file integrity mismatch")
            return payload, row.mime

    def drop_request(self, request_id: str) -> None:
        # Once the authoritative Broker outcome is persisted, staged bytes are
        # no longer needed and must not accumulate on disk.
        request_id = str(request_id or "")
        with self.lock:
            self._prune()
            keys = [key for key in self.entries if key[0] == request_id]
            for key in keys:
                row = self.entries.pop(key, None)
                if row is not None:
                    self._delete_persisted(key[0], key[1], row.path)
            # Also remove any durable rows that were not restored into memory.
            with self._db() as conn:
                rows = conn.execute(
                    "SELECT path_name FROM generated_file_stages WHERE request_id=?",
                    (request_id,),
                ).fetchall()
                conn.execute(
                    "DELETE FROM generated_file_stages WHERE request_id=?",
                    (request_id,),
                )
            for item in rows:
                name = str(item["path_name"] or "")
                if STAGE_PATH.fullmatch(name):
                    (self.root / name).unlink(missing_ok=True)

    def drop(self, request_id: str, upload_id: str) -> None:
        key = (str(request_id or ""), str(upload_id or ""))
        with self.lock:
            row = self.entries.pop(key, None)
            if row is not None:
                self._delete_persisted(key[0], key[1], row.path)
            else:
                self._delete_persisted(key[0], key[1])
