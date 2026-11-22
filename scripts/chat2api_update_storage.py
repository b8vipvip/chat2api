#!/usr/bin/env python3
"""No-follow, single-link file operations for the privileged host updater.

This module deliberately uses only the Python standard library: it runs on the
host, outside the application container and without the application's venv.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any


MAX_JSON_BYTES = 1024 * 1024
DEFAULT_DATA_DIR_MODE = 0o755


def validate_directory(path: Path, *, create: bool = False, mode: int = DEFAULT_DATA_DIR_MODE) -> Path:
    path = Path(os.path.abspath(path))

    def reject_symlink_components() -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError(f"Updater directory cannot contain symlinks: {current}")

    # Inspect existing ancestors before mkdir so a symlinked parent is never
    # used as a creation target.
    reject_symlink_components()
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=mode)
    reject_symlink_components()

    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"Updater path must be a directory: {path}")
    # chmod through a validated directory descriptor, not a path that could be
    # swapped for a symlink between lstat and chmod.
    if create:
        directory_fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(directory_fd)
            if not stat.S_ISDIR(opened.st_mode) or opened.st_ino != metadata.st_ino:
                raise RuntimeError(f"Updater directory changed during validation: {path}")
            os.fchmod(directory_fd, mode)
            metadata = os.fstat(directory_fd)
        finally:
            os.close(directory_fd)
    if metadata.st_mode & 0o022:
        raise RuntimeError(f"Updater directory must not be group/world writable: {path}")
    return path


def open_private_file(path: Path, flags: int, *, mode: int | None = 0o600) -> int:
    path = Path(path)
    validate_directory(path.parent)
    if path.is_symlink():
        raise RuntimeError(f"Updater files cannot be symlinks: {path}")
    safe_flags = flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, safe_flags, 0o600 if mode is None else mode)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError(f"Updater files must be single-link regular files: {path}")
        if mode is not None and hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        return fd
    except Exception:
        os.close(fd)
        raise


def read_bytes(path: Path, *, max_bytes: int = MAX_JSON_BYTES) -> bytes | None:
    try:
        fd = open_private_file(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if metadata.st_size > max_bytes:
            raise ValueError(f"Updater file exceeds its size limit: {path}")
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"Updater file exceeds its size limit: {path}")
    return payload


def read_json(path: Path) -> dict[str, Any]:
    raw = read_bytes(path)
    if raw is None:
        return {}
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Updater JSON must contain an object: {path}")
    return payload


def write_bytes_atomic(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path = Path(path)
    validate_directory(path.parent)
    if path.exists() or path.is_symlink():
        fd = open_private_file(path, os.O_RDONLY, mode=mode)
        os.close(fd)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}.tmp")
    fd = open_private_file(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=mode,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() or path.is_symlink():
            check_fd = open_private_file(path, os.O_RDONLY, mode=mode)
            os.close(check_fd)
        os.replace(temporary, path)
        directory_fd = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: dict[str, Any], *, mode: int = 0o600) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    write_bytes_atomic(path, encoded, mode=mode)


def copy_file_atomic(
    source: Path, destination: Path, *, mode: int = 0o600,
    max_bytes: int = 4 * 1024 * 1024,
) -> None:
    fd = open_private_file(source, os.O_RDONLY, mode=None)
    with os.fdopen(fd, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if metadata.st_size > max_bytes:
            raise ValueError(f"Updater copy source exceeds its size limit: {source}")
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"Updater copy source exceeds its size limit: {source}")
    write_bytes_atomic(destination, payload, mode=mode)


def truncate_private_file(path: Path, *, mode: int = 0o640) -> None:
    # Validate link count before truncating: O_TRUNC would modify every hard-link
    # target before fstat could reject it.
    fd = open_private_file(path, os.O_WRONLY | os.O_CREAT, mode=mode)
    try:
        os.ftruncate(fd, 0)
        os.fsync(fd)
    finally:
        os.close(fd)


def append_private_file(path: Path, payload: bytes, *, mode: int = 0o640) -> None:
    fd = open_private_file(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, mode=mode)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write to updater log")
            view = view[written:]
    finally:
        os.close(fd)


def tee_log(path: Path) -> None:
    fd = open_private_file(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, mode=0o640)
    try:
        while True:
            chunk = sys.stdin.buffer.read(65536)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short write to updater log")
                view = view[written:]
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    finally:
        os.close(fd)


def prepare_lock(path: Path) -> None:
    fd = open_private_file(path, os.O_RDWR | os.O_CREAT, mode=0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
    finally:
        os.close(fd)


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    ensure = sub.add_parser("ensure-dir")
    ensure.add_argument("path", type=Path)
    lock = sub.add_parser("prepare-lock")
    lock.add_argument("path", type=Path)
    truncate = sub.add_parser("truncate-log")
    truncate.add_argument("path", type=Path)
    tee = sub.add_parser("tee-log")
    tee.add_argument("path", type=Path)
    read_id = sub.add_parser("read-request-id")
    read_id.add_argument("path", type=Path)
    read_cache = sub.add_parser("read-build-cache")
    read_cache.add_argument("path", type=Path)
    write = sub.add_parser("write-json")
    write.add_argument("path", type=Path)
    write.add_argument("--mode", type=lambda value: int(value, 8), default=0o600)
    copy = sub.add_parser("copy-file")
    copy.add_argument("source", type=Path)
    copy.add_argument("destination", type=Path)
    copy.add_argument("--mode", type=lambda value: int(value, 8), default=0o600)
    args = parser.parse_args()

    if args.command == "ensure-dir":
        validate_directory(args.path, create=True)
    elif args.command == "prepare-lock":
        prepare_lock(args.path)
    elif args.command == "truncate-log":
        truncate_private_file(args.path)
    elif args.command == "tee-log":
        tee_log(args.path)
    elif args.command == "read-request-id":
        request_id = str(read_json(args.path).get("request_id") or "")
        if not (len(request_id) == 22 and request_id.startswith("upd_")
                and all(ch in "0123456789abcdef" for ch in request_id[4:])):
            request_id = ""
        print(request_id)
    elif args.command == "read-build-cache":
        print("false" if read_json(args.path).get("use_build_cache") is False else "true")
    elif args.command == "write-json":
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Updater JSON input must be an object")
        write_json_atomic(args.path, payload, mode=args.mode)
    elif args.command == "copy-file":
        copy_file_atomic(args.source, args.destination, mode=args.mode)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except Exception as error:
        print(f"chat2api updater storage: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
