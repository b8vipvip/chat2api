"""Pure classifier for browser-internal media/tool handoff markers.

The ChatGPT stream sometimes sends repeated adjacent JSON objects, but
neither a single nor repeated handoff object is an assistant answer.
Do not strip these strings from user messages or legitimate mixed content.
"""
import re
import json

_HANDOFF = re.compile(r'(?:\{\s*"skipped_mainline"\s*:\s*true\s*\}\s*)+')


def is_internal_tool_handoff(value: object) -> bool:
    """Discard exact internal tool payloads; preserve ordinary prose/Markdown."""
    raw = str(value or "").strip()
    if not raw:
        return False
    if _HANDOFF.fullmatch(raw):
        return True
    if len(raw) > 12000 or raw.startswith((chr(96), "#")):
        return False
    if re.match(r"^(?:bash\s+-lc\s+(?:cat|python|sed)\b|python3?\s+-c\s+)", raw, re.I):
        return not bool(re.search(r"\n\s*\n", raw))
    if raw.startswith("{") and raw.endswith("}"):
        try:
            data = json.loads(raw)
        except ValueError:
            return False
        if isinstance(data, dict):
            keys = set(data)
            if keys == {"query"} and isinstance(data["query"], str):
                return True
            if "size" in keys and "n" in keys and keys.issubset({
                "size", "n", "transparent_background", "quality", "background",
                "output_format", "style",
            }):
                return bool(re.fullmatch(r"\d{2,5}x\d{2,5}", str(data["size"])))
            if "command" in keys and keys.issubset({
                "command", "timeout", "cwd", "workdir", "shell",
            }):
                return True
    return False


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
