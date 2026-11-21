"""Cancellation handshake for Mode 1 requests that have not reached a Worker."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

_T = TypeVar("_T")


class WebChatAdmissionCancelled(RuntimeError):
    """The signed-in Web Chat user cancelled before Worker dispatch."""


@dataclass
class WebChatCancellationSignal:
    requested: asyncio.Event = field(default_factory=asyncio.Event)
    finalized: asyncio.Event = field(default_factory=asyncio.Event)


async def await_worker_admission_or_cancel(
    resolver: Callable[[], Awaitable[_T]],
    signal: WebChatCancellationSignal | None,
) -> _T:
    """Wait for Worker selection, but let an owner-scoped cancellation win the race."""
    if signal is None:
        return await resolver()
    resolve_task = asyncio.create_task(resolver())
    cancel_task = asyncio.create_task(signal.requested.wait())
    try:
        done, _ = await asyncio.wait(
            {resolve_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task in done and signal.requested.is_set():
            if not resolve_task.done():
                resolve_task.cancel()
            await asyncio.gather(resolve_task, return_exceptions=True)
            raise WebChatAdmissionCancelled()
        cancel_task.cancel()
        await asyncio.gather(cancel_task, return_exceptions=True)
        if signal.requested.is_set():
            raise WebChatAdmissionCancelled()
        return await resolve_task
    except BaseException:
        if not resolve_task.done():
            resolve_task.cancel()
        if not cancel_task.done():
            cancel_task.cancel()
        await asyncio.gather(resolve_task, cancel_task, return_exceptions=True)
        raise
