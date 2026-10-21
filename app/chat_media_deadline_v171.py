"""Bounded media-handoff allowance for ordinary Chat Completions.

A chat request can legitimately invoke ChatGPT's image generator without using
/v1/images/generations. Keep the broker as the only terminal authority: verified
Worker evidence may extend the *existing* server deadline, never complete a
request or infer a generated image from a canvas / handoff marker.

Explicit API timeout overrides always win. No retry, terminal state or scheduler
is introduced here.
"""

from __future__ import annotations

MEDIA_HANDOFF_TIMEOUT_SECONDS = 600


def chat_timeout_window(
    base_timeout_seconds: int,
    *,
    automatic_extension: bool,
    media_handoff_proven: bool,
) -> int:
    """Return the sole request deadline window, relative to request start.

    Only a correlated chat.diagnostics event from the active Worker can set
    media_handoff_marker=True on the broker's request state. A probe event,
    screenshot, editor canvas or browser-displayed image cannot set the flag.
    """
    baseline = int(base_timeout_seconds)
    if automatic_extension and media_handoff_proven:
        return max(baseline, MEDIA_HANDOFF_TIMEOUT_SECONDS)
    return baseline


def worker_chat_timeout(
    base_timeout_seconds: int, *, automatic_extension: bool,
) -> int:
    """Give the same Worker owner enough time to finish a possible image job.

    The server still enforces the normal deadline for requests with no verified
    media handoff. An explicit timeout is respected on both sides.
    """
    return max(int(base_timeout_seconds), MEDIA_HANDOFF_TIMEOUT_SECONDS) if automatic_extension else int(base_timeout_seconds)
