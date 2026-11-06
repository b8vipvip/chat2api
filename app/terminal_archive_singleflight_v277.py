"""One request-owned post-terminal archive, independent of HTTP delivery lifetime.

A cancelled SSE task does NOT cancel an asyncio.to_thread worker. Without
single-flight, the detached Broker terminal supervisor starts another archive
for the same completed request while the first one is still writing images.
This helper shares the *same* result or error and never silently reassigns
an archive to different terminal bytes or another API-key owner.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def archive_once(
    state: Any, *, request_id: str, owner: str, final_text: str,
    base_url: str, run: Callable[[], Awaitable[T]],
) -> T:
    """Shield a single archive task from a caller's HTTP cancellation.

    Creation and storing of the shared task happen without an await: concurrent
    consumers of the same Broker state cannot each start an archive. This
    deliberately caches failures too: retrying after a timeout/cancellation can
    duplicate the side effects of an uninterruptible Python thread.
    """
    identity = (
        str(request_id), str(owner),
        hashlib.sha256(str(final_text).encode("utf-8")).hexdigest(),
        str(base_url),
    )
    task = getattr(state, "_chat2api_terminal_archive_task", None)
    if task is None:
        task = asyncio.create_task(run(), name="chat2api-terminal-archive")
        state._chat2api_terminal_archive_identity = identity
        state._chat2api_terminal_archive_task = task

        def observe_result(done: asyncio.Task) -> None:
            # Avoid "Task exception was never retrieved" if the HTTP peer
            # vanishes and no subscriber remains; a later awaiting subscriber
            # still receives the original exception from this same task.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(observe_result)
    else:
        if getattr(state, "_chat2api_terminal_archive_identity", None) != identity:
            raise ValueError("Terminal archive identity changed for the same request")
        state.diagnostics["server_finalization_archive_reused"] = True

    # Even if an HTTP task is cancelled mid-archive, detached finalization
    # awaits this same future rather than starting another media write.
    return await asyncio.shield(task)
