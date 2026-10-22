"""Pure classifier for browser-internal media/tool handoff markers.

The ChatGPT stream sometimes sends repeated adjacent JSON objects, but
neither a single nor repeated handoff object is an assistant answer.
Do not strip these strings from user messages or legitimate mixed content.
"""
import re

_HANDOFF = re.compile(r'(?:\{\s*"skipped_mainline"\s*:\s*true\s*\}\s*)+')


def is_internal_tool_handoff(value: object) -> bool:
    raw = str(value or "").strip()
    return bool(raw and _HANDOFF.fullmatch(raw))


class StreamMediaMarkerGate:
    """Presentation-only gate for an OpenAI Chat Completions SSE stream.

    The broker owns the request terminal state. This class merely prevents
    partial Markdown image markers (including source CDN URLs and Base64 bytes)
    from reaching the client before media archival changes their source URL.
    Once a marker starts, the terminal handler sends the rewritten final suffix.
    """

    def __init__(self) -> None:
        self.pending = ""
        self.holding = False

    def push(self, fragment: str) -> str:
        if self.holding:
            return ""
        self.pending += str(fragment or "")
        start = self.pending.find("![")
        if start >= 0:
            output = self.pending[:start]
            self.pending = ""
            self.holding = True
            return output
        # The next SSE chunk may begin with '['; defer a trailing '!'.
        retain = 1 if self.pending.endswith("!") else 0
        output = self.pending[:-retain] if retain else self.pending
        self.pending = self.pending[-retain:] if retain else ""
        return output
