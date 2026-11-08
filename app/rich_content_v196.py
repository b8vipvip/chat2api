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
    image_by_url = {
        str(url): row
        for row in (images or []) if isinstance(row, dict)
        for url in (row.get("owned_url"), row.get("url"))
        if isinstance(url, str) and url.startswith("https://")
    }
    file_by_url = {str(row.get("url")): row for row in (files or []) if row.get("url")}
    spans = []
    for match in _IMAGE.finditer(source):
        record = image_by_url.get(match.group(1))
        if record:
            spans.append((match.start(), match.end(), "image", str(record["id"]), record))
    for match in _FILE.finditer(source):
        record = file_by_url.get(match.group(1))
        if record:
            spans.append((match.start(), match.end(), "file", str(record["id"]), record))
    spans.sort()
    blocks = []
    cursor = 0
    for start, end, kind, asset_id, record in spans[:100]:
        if start < cursor:
            continue
        if start > cursor:
            blocks.append({"type": "markdown", "start": cursor, "end": start})
        item = {"type": kind, "start": start, "end": end, "asset_id": asset_id}
        if kind == "image":
            # Exact source span + verified archive bytes, NOT visual DOM
            # placement. Do not infer image order from array indices/sizes.
            digest = record.get("sha256")
            if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
                item["archive_sha256"] = digest
                item["position_proof"] = "canonical-markdown-source-span"
        blocks.append(item)
        cursor = end
    if cursor < len(source) or not blocks:
        blocks.append({"type": "markdown", "start": cursor, "end": len(source)})
    return {"schema_version": 1, "format": "markdown", "blocks": blocks}
