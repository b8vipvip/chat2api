"""Bounded, content-safe persistence for browser-generated inline media.

The browser's chat.completed event may contain several megabytes of PNG Base64.
The same bytes are archived separately before API delivery. Request-history
telemetry must never append that transport representation to request_history.jsonl.
Keep ordinary rich Markdown text verbatim; only replace embedded Base64 media
data URLs, including non-image attachments the browser could someday emit.
"""
from __future__ import annotations

import re

_INLINE_MEDIA_DATA_URL = re.compile(
    r"data:[a-z0-9.+-]+/[a-z0-9.+-]+(?:;[a-z0-9._+=-]+)*;base64,[A-Za-z0-9+/=]+",
    re.IGNORECASE,
)
_PLACEHOLDER = "[media payload stored separately]"


def history_safe_response_text(value: str) -> str:
    text = str(value or "")
    if "data:" not in text.lower() or ";base64," not in text.lower():
        return text
    return _INLINE_MEDIA_DATA_URL.sub(_PLACEHOLDER, text)
