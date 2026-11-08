"""Compact, ordered rich-content manifest for OpenAI-compatible chat responses.

Source of truth remains choices.message.content (Markdown). The optional
chat2api.content_blocks array locates already-archived assets by source span;
no duplicated full-text content, no injected HTML/JS or execution semantics.
"""
from __future__ import annotations

import re

_IMAGE = re.compile(r"!\[[^\]\n]{0,200}\]\((https://[^)\s]{1,2048})\)")
_FILE = re.compile(r"\[📎 [^\]\n]{1,180}\]\((https://[^)\s]{1,2048})\)")


def _fenced_ranges(source: str) -> list[tuple[int, int]]:
    """Never promote a sample Markdown media link inside a fenced code block."""
    blocked: list[tuple[int, int]] = []
    fence: tuple[str, int, int] | None = None
    offset = 0
    for line in source.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if fence is None:
            opening = re.match(r"^ {0,3}([\x60]{3,}|~{3,})", body)
            if opening:
                token = opening.group(1)
                fence = (token[0], len(token), offset)
        else:
            char, count, begin = fence
            if re.fullmatch(r" {0,3}" + re.escape(char) +
                            r"{" + str(count) + r",}[ \t]*", body):
                blocked.append((begin, offset + len(line)))
                fence = None
        offset += len(line)
    if fence is not None:
        blocked.append((fence[2], len(source)))
    return blocked


def _unique_asset_urls(rows: list[dict] | None, aliases: tuple[str, ...]) -> dict[str, dict]:
    """Conflicting archive claims for one URL cannot prove an asset identity."""
    unique: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            continue
        for alias in aliases:
            url = row.get(alias)
            if not isinstance(url, str) or not url.startswith("https://"):
                continue
            if url in ambiguous:
                continue
            prior = unique.get(url)
            if prior is not None and (
                prior.get("id") != row.get("id")
                or prior.get("sha256") != row.get("sha256")
            ):
                unique.pop(url, None)
                ambiguous.add(url)
                continue
            unique[url] = row
    return unique


def manifest(markdown: str, images: list[dict] | None, files: list[dict] | None) -> dict:
    source = str(markdown or "")
    image_by_url = _unique_asset_urls(images, ("owned_url", "url"))
    file_by_url = _unique_asset_urls(files, ("url",))
    blocked = _fenced_ranges(source)

    def content_link(start: int, end: int) -> bool:
        return not any(start < stop and end > begin for begin, stop in blocked)

    spans = []
    for match in _IMAGE.finditer(source):
        record = image_by_url.get(match.group(1))
        if record and content_link(match.start(), match.end()):
            spans.append((match.start(), match.end(), "image", str(record["id"]), record))
    for match in _FILE.finditer(source):
        record = file_by_url.get(match.group(1))
        if record and content_link(match.start(), match.end()):
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
