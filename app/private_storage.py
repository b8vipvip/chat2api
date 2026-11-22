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
