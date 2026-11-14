"""Offline, fail-closed data-directory backup verification.

Backups may contain credentials and user content. Creation and restore therefore
require an explicit plaintext-data acknowledgement until the G0 encryption gate
is approved. This module never prints file contents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from .runtime_single_process_guard import RuntimeTopologyError, SingleProcessRuntimeGuard


MANIFEST_NAME = ".chat2api-backup-manifest.json"
LOCK_NAME = ".chat2api-runtime.lock"
MANIFEST_VERSION = 1
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_FILES = 100_000
MAX_JSON_FILE_BYTES = 256 * 1024 * 1024


class BackupError(RuntimeError):
    pass


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix in {".sqlite", ".sqlite3", ".db"}:
        return "sqlite"
    return "binary"


def _walk_files(root: Path, *, allow_manifest: bool = False) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise BackupError("Data directory must be a real directory")
    result: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in list(dirs):
            path = base / name
            if path.is_symlink():
                raise BackupError("Data directory contains a symbolic link")
        for name in files:
            path = base / name
            if path.is_symlink():
                raise BackupError("Data directory contains a symbolic link")
            if not path.is_file():
                raise BackupError("Data directory contains a non-regular file")
            relative = path.relative_to(root).as_posix()
            if relative == LOCK_NAME:
                continue
            if relative == MANIFEST_NAME:
                if allow_manifest:
                    continue
                raise BackupError("Reserved backup manifest name exists in source data")
            result.append(path)
    return sorted(result, key=lambda item: item.relative_to(root).as_posix())


def _entry(root: Path, path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return {
        "path": path.relative_to(root).as_posix(),
        "size": size,
        "sha256": digest.hexdigest(),
        "mode": stat.S_IMODE(path.stat().st_mode),
        "kind": _kind(path),
    }


def _validate_content(root: Path, entry: dict[str, Any]) -> None:
    path = root / PurePosixPath(entry["path"])
    kind = entry["kind"]
    if kind == "json":
        try:
            if path.stat().st_size > MAX_JSON_FILE_BYTES:
                raise BackupError("JSON file exceeds the offline validation size limit")
            with path.open("r", encoding="utf-8-sig") as handle:
                json.load(handle)
        except (OSError, UnicodeError, ValueError) as exc:
            raise BackupError("Backup contains invalid JSON data") from exc
    elif kind == "jsonl":
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if line.strip():
                        json.loads(line)
        except (OSError, UnicodeError, ValueError) as exc:
            raise BackupError("Backup contains invalid JSONL data") from exc
    elif kind == "sqlite":
        # POSIX separators keep Windows drive paths valid SQLite URIs.
        # immutable=1 prevents verification from creating WAL/SHM sidecars or
        # changing the verified backup directory after its manifest is checked.
        uri = "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro&immutable=1"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            try:
                result = connection.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise BackupError("Backup contains a SQLite integrity failure")
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise BackupError("Backup contains an unreadable SQLite database") from exc


def _manifest(root: Path) -> dict[str, Any]:
    entries = [_entry(root, path) for path in _walk_files(root)]
    for entry in entries:
        _validate_content(root, entry)
    return {
        "schema_version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": entries,
    }


def _validate_manifest(value: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != MANIFEST_VERSION
    ):
        raise BackupError("Unsupported or invalid backup manifest")
    entries = value.get("files")
    if not isinstance(entries, list) or len(entries) > MAX_MANIFEST_FILES:
        raise BackupError("Invalid or oversized backup manifest file list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256", "mode", "kind"}:
            raise BackupError("Invalid backup manifest entry")
        name = entry["path"]
        if not isinstance(name, str) or "\\" in name or ":" in name or "\x00" in name:
            raise BackupError("Invalid backup path")
        relative = PurePosixPath(name)
        if relative.is_absolute() or relative.as_posix() != name or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise BackupError("Unsafe backup path")
        if name in {LOCK_NAME, MANIFEST_NAME} or name in seen:
            raise BackupError("Duplicate or reserved backup path")
        seen.add(name)
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise BackupError("Invalid backup file size")
        if type(entry["mode"]) is not int or entry["mode"] < 0 or entry["mode"] > 0o777:
            raise BackupError("Invalid backup file mode")
        if not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64 or any(
            ch not in "0123456789abcdef" for ch in entry["sha256"]
        ):
            raise BackupError("Invalid backup SHA256")
        if not isinstance(entry["kind"], str) or entry["kind"] not in {"json", "jsonl", "sqlite", "binary"}:
            raise BackupError("Invalid backup file kind")
    return entries


def _verify_entries(root: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    actual = _walk_files(root, allow_manifest=(root / MANIFEST_NAME).is_file())
    actual_names = {path.relative_to(root).as_posix() for path in actual}
    expected_names = {entry["path"] for entry in entries}
    if actual_names != expected_names:
        raise BackupError("Backup file set does not match its manifest")
    for entry in entries:
        path = root / PurePosixPath(entry["path"])
        actual = _entry(root, path)
        if actual["size"] != entry["size"] or actual["sha256"] != entry["sha256"]:
            raise BackupError("Backup file checksum or size mismatch")
        if actual["mode"] != entry["mode"]:
            raise BackupError("Backup file permission mismatch")
        if actual["kind"] != entry["kind"]:
            raise BackupError("Backup file type mismatch")
        _validate_content(root, entry)
    return {"ok": True, "file_count": len(entries), "schema_version": MANIFEST_VERSION}


def create_backup(
    source: Path | str,
    destination: Path | str,
    *,
    acknowledge_plaintext_sensitive_data: bool = False,
) -> dict[str, Any]:
    if not acknowledge_plaintext_sensitive_data:
        raise BackupError("Explicit plaintext-sensitive-data acknowledgement is required")
    source_path = Path(source)
    destination_path = Path(destination)
    if source_path.is_symlink() or destination_path.is_symlink():
        raise BackupError("Source and destination paths cannot be symbolic links")
    src = source_path.resolve()
    dest = destination_path.resolve()
    if not src.is_dir():
        raise BackupError("Source data directory is unavailable")
    if dest == src or src in dest.parents or dest in src.parents:
        raise BackupError("Backup destination must be separate from the source data directory")
    if dest.exists():
        raise BackupError("Backup destination must not already exist")

    guard = SingleProcessRuntimeGuard(src)
    try:
        guard.acquire()
    except RuntimeTopologyError as exc:
        raise BackupError("Cannot back up while another server process owns the data directory") from exc
    try:
        # Bound manifest cardinality before copying anything. Otherwise an
        # unexpectedly large source tree could consume unbounded time/disk and
        # only fail after the complete copy when the manifest is serialized.
        source_files = _walk_files(src)
        if len(source_files) > MAX_MANIFEST_FILES:
            raise BackupError("Data directory exceeds the 100,000-file backup limit")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.mkdir(mode=0o700)
        try:
            for path in source_files:
                relative = path.relative_to(src)
                target = dest / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target, follow_symlinks=False)
            manifest = _manifest(dest)
            manifest_path = dest / MANIFEST_NAME
            encoded_manifest = (json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, indent=2
            ) + "\n").encode("utf-8")
            if len(encoded_manifest) > MAX_MANIFEST_BYTES:
                raise BackupError("Backup manifest exceeds the 16 MiB limit")
            fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded_manifest)
                handle.flush()
                os.fsync(handle.fileno())
            return {"ok": True, "file_count": len(manifest["files"]), "manifest": MANIFEST_NAME}
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
    finally:
        guard.release()


def verify_backup(backup: Path | str) -> dict[str, Any]:
    root = Path(backup)
    if root.is_symlink() or not root.is_dir():
        raise BackupError("Backup directory is unavailable")
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupError("Backup manifest is missing")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise BackupError("Backup manifest exceeds the 16 MiB limit")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise BackupError("Backup manifest is unreadable") from exc
    entries = _validate_manifest(manifest)
    return _verify_entries(root, entries)


def restore_backup(
    backup: Path | str,
    target: Path | str,
    *,
    acknowledge_plaintext_sensitive_data: bool = False,
) -> dict[str, Any]:
    if not acknowledge_plaintext_sensitive_data:
        raise BackupError("Explicit plaintext-sensitive-data acknowledgement is required")
    backup_path = Path(backup)
    target_path = Path(target)
    if backup_path.is_symlink() or target_path.is_symlink():
        raise BackupError("Backup and restore paths cannot be symbolic links")
    source = backup_path.resolve()
    dest = target_path.resolve()
    if dest == source or source in dest.parents or dest in source.parents:
        raise BackupError("Restore target must be separate from the backup")
    verify_backup(source)
    if dest.exists():
        raise BackupError("Restore target must not already exist")
    guard = SingleProcessRuntimeGuard(dest)
    try:
        guard.acquire()
    except RuntimeTopologyError as exc:
        raise BackupError("Restore target is already owned by another process") from exc
    succeeded = False
    try:
        manifest = json.loads((source / MANIFEST_NAME).read_text(encoding="utf-8"))
        entries = _validate_manifest(manifest)
        for entry in entries:
            relative = PurePosixPath(entry["path"])
            source_file = source / relative
            target_file = dest / relative
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file, follow_symlinks=False)
        result = _verify_entries(dest, entries)
        succeeded = True
        return {**result, "restored": True}
    finally:
        # Release the target lock before removing a failed restore directory;
        # otherwise a new process could create a replacement lock inode while
        # this process still holds the unlinked original inode.
        guard.release()
        if not succeeded:
            shutil.rmtree(dest, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create, verify or restore-check a Chat2API data-directory backup")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--source", required=True)
    create.add_argument("--destination", required=True)
    create.add_argument("--acknowledge-plaintext-sensitive-data", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--backup", required=True)
    restore = commands.add_parser("restore-check")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--target", required=True)
    restore.add_argument("--acknowledge-plaintext-sensitive-data", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_backup(
                args.source, args.destination,
                acknowledge_plaintext_sensitive_data=args.acknowledge_plaintext_sensitive_data,
            )
        elif args.command == "verify":
            result = verify_backup(args.backup)
        else:
            result = restore_backup(
                args.backup, args.target,
                acknowledge_plaintext_sensitive_data=args.acknowledge_plaintext_sensitive_data,
            )
    except BackupError as exc:
        parser.exit(2, f"backup operation refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
