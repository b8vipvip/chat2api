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
