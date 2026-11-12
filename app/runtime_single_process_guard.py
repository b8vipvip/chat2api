"""Fail-closed single-process guard for the current in-process runtime.

Broker and ClientRegistry are process-local authorities. Until a shared lease,
message transport and transactional persistence architecture is approved, two
server processes must not concurrently own the same data directory.
"""
from __future__ import annotations

import errno
import os
from contextlib import asynccontextmanager
import stat
import threading
from pathlib import Path
from typing import Any


class RuntimeTopologyError(RuntimeError):
    """The requested deployment topology is unsupported by this runtime."""


class SingleProcessRuntimeGuard:
    """Hold an advisory lock for the full ASGI application lifetime.

    The lock protects processes sharing one local data directory. It is not a
    distributed lock for separate volumes or filesystems without reliable
    advisory-lock semantics; those topologies remain unsupported.
    """

    _WORKER_ENV_VARS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS")

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.lock_path = self.data_dir / ".chat2api-runtime.lock"
        self._fd: int | None = None
        self._lock = threading.RLock()

    @classmethod
    def _validate_worker_environment(cls) -> None:
        for name in cls._WORKER_ENV_VARS:
            raw = str(os.environ.get(name) or "").strip()
            if not raw:
                continue
            try:
                workers = int(raw, 10)
            except ValueError:
                # Values such as "auto" do not prove a multi-worker topology.
                # The shared-data-dir OS lock remains the second line of defence.
                continue
            if workers > 1:
                raise RuntimeTopologyError(
                    f"{name}={workers} is unsupported: Chat2API currently requires one server process"
                )

    def acquire(self) -> None:
        with self._lock:
            if self._fd is not None:
                return
            self._validate_worker_environment()
            if self.data_dir.is_symlink():
                raise RuntimeTopologyError("Chat2API data directory cannot be a symbolic link")
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if self.lock_path.is_symlink():
                raise RuntimeTopologyError("Runtime lock path cannot be a symbolic link")

            flags = os.O_CREAT | os.O_RDWR
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.lock_path, flags, 0o600)
            except OSError as exc:
                raise RuntimeTopologyError("Unable to open the private runtime lock file") from exc

            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeTopologyError("Runtime lock path must be a regular file")
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                else:
                    os.chmod(self.lock_path, 0o600)

                if os.name == "nt":
                    import msvcrt

                    if metadata.st_size < 1:
                        os.write(fd, b"0")
                        os.fsync(fd)
                    os.lseek(fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        raise RuntimeTopologyError(
                            "Another Chat2API server process already owns this data directory"
                        ) from exc
                else:
                    import fcntl

                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        if exc.errno in {errno.EACCES, errno.EAGAIN}:
                            raise RuntimeTopologyError(
                                "Another Chat2API server process already owns this data directory"
                            ) from exc
                        raise

                payload = f"pid={os.getpid()}\n"
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, payload.encode("ascii"))
                os.fsync(fd)
                self._fd = fd
            except Exception:
                os.close(fd)
                raise

    def release(self) -> None:
        with self._lock:
            fd, self._fd = self._fd, None
            if fd is None:
                return
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def install_single_process_runtime_guard(app: Any) -> Any:
    """Install one startup/shutdown lock on the production FastAPI app."""
    if getattr(app.state, "single_process_runtime_guard_installed", False):
        return app
    settings = getattr(app.state, "settings", None)
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is None:
        raise RuntimeTopologyError("Runtime guard requires the configured Chat2API data directory")

    guard = SingleProcessRuntimeGuard(data_dir)
    app.state.single_process_runtime_guard = guard

    # The production FastAPI app already supplies a lifespan context manager
    # for loading its stores. FastAPI does not combine custom lifespan with
    # legacy on_event handlers, so wrap the existing context instead of adding
    # startup/shutdown handlers that would silently never run.
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def guarded_lifespan(application):
        guard.acquire()
        try:
            async with original_lifespan(application) as state:
                yield state
        finally:
            guard.release()

    app.router.lifespan_context = guarded_lifespan
    app.state.single_process_runtime_guard_installed = True
    return app
