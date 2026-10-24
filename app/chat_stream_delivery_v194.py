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
