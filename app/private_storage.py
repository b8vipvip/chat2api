"""Small, shared helpers for owner-private local application storage."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def ensure_private_directory(path: Path, *, mode: int = 0o700) -> None:
    """Create a private directory and reject a linked or non-directory path."""
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Private storage directories cannot be symbolic links")
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("Private storage path must be a regular directory")
    os.chmod(path, mode)


def open_private_regular_file(path: Path, flags: int, *, mode: int = 0o600) -> int:
    """Open a single-link regular file without following links or blocking on FIFOs."""
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Private storage files cannot be symbolic links")
    safe_flags = flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, safe_flags, mode)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError("Private storage files must be single-link regular files")
        if os.name != "nt" and hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        return fd
    except Exception:
        os.close(fd)
        raise


def read_private_bytes(path: Path) -> bytes | None:
    """Read a private regular file through its validated descriptor, or return None."""
    try:
        fd = open_private_regular_file(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def write_private_bytes_atomic(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Atomically replace a private file using a unique exclusive staging file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        fd = open_private_regular_file(path, os.O_RDONLY)
        os.close(fd)
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    fd = open_private_regular_file(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=mode,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Re-check an existing destination before replacement. os.replace
        # replaces a symlink itself, but rejecting it keeps the store fail-closed.
        if path.exists() or path.is_symlink():
            check_fd = open_private_regular_file(path, os.O_RDONLY)
            os.close(check_fd)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
