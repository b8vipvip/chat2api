"""Bounded, append-safe OpenAI SSE delivery for browser-driven chats.

Worker chat.delta/chat.snapshot are *provisional* browser observations and may
represent ChatGPT's visible analysis synopsis rather than its final answer.
The single server terminal (chat.completed) supplies the authoritative text.

Never permit a provisional prefix to contaminate an append-only SSE content lane.
Reasoning preview is optional and does not change terminal ownership.
"""

from __future__ import annotations

FINAL_CHUNK_CHARS = 4096
MAX_PREVIEW_CHARS = 2048


class StagedStreamText:
    """Emit only bounded, public, same-request preview via a separate lane."""

    def __init__(self) -> None:
        self.preview_sent_chars = 0

    def preview_delta(self, candidate: str, *, enabled: bool) -> str:
        if not enabled:
            return ""
        remaining = MAX_PREVIEW_CHARS - self.preview_sent_chars
        if remaining <= 0:
            return ""
        text = str(candidate or "")[:remaining]
        self.preview_sent_chars += len(text)
        return text


def final_text_chunks(final_text: str):
    """Emit every character of the authoritative terminal, no prefix heuristic."""
    text = str(final_text or "")
    for start in range(0, len(text), FINAL_CHUNK_CHARS):
        yield text[start : start + FINAL_CHUNK_CHARS]


def sse_heartbeat_interval(elapsed_seconds: float) -> float:
    """Keep an admitted SSE request alive before the first slow Worker event.

    Real 2026-10-29 requests lost their HTTP peer after 2-8 seconds, before the
    former 15-second heartbeat. This is transport cadence only: provisional
    heartbeats cannot claim an assistant completion or change Broker state.
    """
    # v315: the real long rich-media A request lost its HTTP SSE peer
    # 285 seconds in, 2.927s after the last accepted ASGI body, while the
    # Worker later continued to a 915s lease stall. The previous 30s->5s
    # backoff leaves a >3s idle window across the application-level
    # generator and proxy chain. Match the independently configured 1s
    # transport middleware at *every* elapsed time, without adding assistant
    # content, altering request-v6, or inferring client receipt from ASGI send.
    # This only narrows transport idle gaps; it does not assert that a proxy
    # or browser navigation was the actual cause of every observed disconnect.
    max(0.0, float(elapsed_seconds))  # retain numeric contract
    return 1.0
