"""Bounded media-handoff allowance for ordinary Chat Completions.

A chat request can legitimately invoke ChatGPT's image generator without using
/v1/images/generations. Keep the broker as the only terminal authority: verified
Worker evidence may extend the *existing* server deadline, never complete a
request or infer a generated image from a canvas / handoff marker.

Explicit API timeout overrides always win. No retry, terminal state or scheduler
is introduced here.
"""

from __future__ import annotations

import re

MEDIA_HANDOFF_TIMEOUT_SECONDS = 600
LONG_FORM_TEXT_TIMEOUT_SECONDS = 600
STREAM_PROGRESS_TIMEOUT_SECONDS = 900
_LONG_FORM_WORDS = re.compile(r"(?:不少于|至少|不低于|at\s+least|minimum\s+of)\s*([0-9][0-9,]*)\s*(?:个?汉?字|words?|characters?)", re.I)
_LONG_FORM_HANDOFF = re.compile(
    r"(?:生成|撰写|编写|整理|输出|提供|制作).{0,48}"
    r"(?:项目交接上下文|项目交接文档|项目交接报告|开发交接报告)",
    re.I | re.S,
)


def long_form_text_requested(prompt: str) -> bool:
    """Positive user-specified length evidence; not a Worker completion signal."""
    user_text = str(prompt or "").rsplit("[User]", 1)[-1]
    return (
        any(int(m.group(1).replace(",", "")) >= 2500 for m in _LONG_FORM_WORDS.finditer(user_text))
        or bool(_LONG_FORM_HANDOFF.search(user_text))
    )


def chat_timeout_window(
    base_timeout_seconds: int,
    *,
    automatic_extension: bool,
    media_handoff_proven: bool,
    long_text_requested: bool = False,
    stream_progress_proven: bool = False,
    stream: bool = False,
) -> int:
    """Return the same lease sent to the Worker, for default requests.

    Previously, ordinary non-stream chats expired server-side at 300 seconds
    while the Worker was granted 600 seconds; default stream chats could also
    have a shorter API deadline than their Worker lease before first progress.
    Both halves must use one absolute deadline budget. Only explicit client
    timeouts override the default, and no page status/evidence is promoted into
    a completion decision.
    """
    baseline = int(base_timeout_seconds)
    if not automatic_extension:
        return baseline
    # A streaming request's default Worker lease is 900s even before the
    # first delta. Real progress remains diagnostic, not a competing clock.
    if stream:
        return worker_chat_timeout(baseline, automatic_extension=True, stream=True)
    return worker_chat_timeout(baseline, automatic_extension=True, stream=False)


def worker_chat_timeout(
    base_timeout_seconds: int, *, automatic_extension: bool, stream: bool = False,
) -> int:
    """Give the same Worker owner enough time to finish a possible image job.

    The server still enforces the normal deadline for requests with no verified
    media handoff. An explicit timeout is respected on both sides.
    """
    if not automatic_extension:
        return int(base_timeout_seconds)
    # The Worker must outlive the server's bounded active-progress lease.
    return max(int(base_timeout_seconds), STREAM_PROGRESS_TIMEOUT_SECONDS if stream else MEDIA_HANDOFF_TIMEOUT_SECONDS)
