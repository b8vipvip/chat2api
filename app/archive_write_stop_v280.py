"""Thread-safe cooperative stop signal for timed-out post-terminal archival.

asyncio.wait_for(to_thread(...)) cannot halt the underlying Python thread.
The owning task signals its timeout; blocking storage calls cannot be forcibly
interrupted, but every subsequent write/manifest checkpoint fails closed and
the existing rollback logic compensates unpublished media.
"""
from __future__ import annotations

import threading


class ArchiveWriteStopped(RuntimeError):
    """Archive exceeded the owning request's post-terminal write deadline."""


class ArchiveWriteStop:
    def __init__(self) -> None:
        self._stopped = threading.Event()

    def stop(self) -> None:
        self._stopped.set()

    def checkpoint(self) -> None:
        if self._stopped.is_set():
            raise ArchiveWriteStopped("Post-terminal media archive deadline reached")

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()
