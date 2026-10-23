"""Compact, ordered rich-content manifest for OpenAI-compatible chat responses.

Source of truth remains choices.message.content (Markdown). The optional
chat2api.content_blocks array locates already-archived assets by source span;
no duplicated full-text content, no injected HTML/JS or execution semantics.
"""
from __future__ import annotations

import re

_IMAGE = re.compile(r"!\[[^\]\n]{0,200}\]\((https://[^)\s]{1,2048})\)")
_FILE = re.compile(r"\[📎 [^\]\n]{1,180}\]\((https://[^)\s]{1,2048})\)")

def manifest(markdown: str, images: list[dict] | None, files: list[dict] | None) -> dict:
    source = str(markdown or "")
    image_by_url = {str(row.get("url")): row for row in (images or []) if row.get("url")}
    file_by_url = {str(row.get("url")): row for row in (files or []) if row.get("url")}
    spans = []
    for match in _IMAGE.finditer(source):
        record = image_by_url.get(match.group(1))
        if record:
            spans.append((match.start(), match.end(), "image", str(record["id"])))
    for match in _FILE.finditer(source):
        record = file_by_url.get(match.group(1))
        if record:
            spans.append((match.start(), match.end(), "file", str(record["id"])))
    spans.sort()
    blocks = []
    cursor = 0
    for start, end, kind, asset_id in spans[:100]:
        if start < cursor:
            continue
        if start > cursor:
            blocks.append({"type": "markdown", "start": cursor, "end": start})
        blocks.append({"type": kind, "start": start, "end": end, "asset_id": asset_id})
        cursor = end
    if cursor < len(source) or not blocks:
        blocks.append({"type": "markdown", "start": cursor, "end": len(source)})
    return {"schema_version": 1, "format": "markdown", "blocks": blocks}
