"""Bounded extraction and validation of citation cards from native tool metadata."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .native_content_v1 import _safe_external_link


MAX_TOOL_REFERENCES = 8
_MAX_METADATA_NODES = 256
_MAX_METADATA_DEPTH = 6
_ROOT_KEYS = (
    "citations",
    "content_references",
    "search_result_groups",
    "search_results",
    "references",
    "sources",
    # Responses-style annotations and the current web-search metadata envelope.
    "annotations",
    "citation_annotations",
    "url_citation",
    "web_search_results",
    "source_references",
    "tool_references",
)
_URL_KEYS = (
    "url", "href", "safe_url", "source_url", "web_url", "link_url",
    "canonical_url", "resolved_url", "target_url",
)
_URL_LIST_KEYS = ("safe_urls", "urls")
_TITLE_KEYS = (
    "title", "display_title", "name", "label", "source_name",
    "source_title", "site_name", "publisher",
)
_SNIPPET_KEYS = ("snippet", "description", "excerpt", "summary", "matched_text")
_ID_KEYS = (
    "ref_id", "citation_id", "reference_id", "source_id", "result_id",
    "annotation_id", "source_ref", "id",
)
_REFERENCE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _plain_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return " ".join(cleaned.split())[:limit]


def _reference_id(value: Any) -> str:
    candidate = _plain_text(value, 128)
    return candidate if _REFERENCE_ID_RE.fullmatch(candidate) else ""


def _citation_offset(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def normalize_tool_references(value: Any) -> list[dict[str, Any]]:
    """Normalize client-persisted references; never trust browser-supplied URLs."""
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value[:32]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not _safe_external_link(url):
            continue
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            continue
        if not host:
            continue
        reference_id = _reference_id(item.get("id"))
        start_index = _citation_offset(item.get("start_index"))
        end_index = _citation_offset(item.get("end_index"))
        valid_range = (
            start_index is not None and end_index is not None
            and start_index < end_index
        )
        identity = (
            ("id:" + reference_id if reference_id else "url:" + url)
            + (f":{start_index}:{end_index}" if valid_range else "")
        )
        if identity in seen:
            continue
        seen.add(identity)
        row = {
            "url": url,
            "title": _plain_text(item.get("title"), 180) or host,
            "snippet": _plain_text(item.get("snippet"), 420),
        }
        if reference_id:
            row["id"] = reference_id
        if valid_range:
            row["start_index"] = start_index
            row["end_index"] = end_index
        result.append(row)
        if len(result) >= MAX_TOOL_REFERENCES:
            break
    return result


def extract_tool_references(metadata: Any) -> list[dict[str, Any]]:
    """Extract only bounded citation/search-result roots from Worker metadata."""
    if not isinstance(metadata, dict):
        return []
    candidates: list[dict[str, Any]] = []
    visited = 0

    def visit(value: Any, depth: int = 0, inherited_title: str = "",
              inherited_snippet: str = "", inherited_start: int | None = None,
              inherited_end: int | None = None) -> None:
        nonlocal visited
        if depth > _MAX_METADATA_DEPTH or visited >= _MAX_METADATA_NODES:
            return
        visited += 1
        if isinstance(value, list):
            for item in value[:32]:
                visit(item, depth + 1, inherited_title, inherited_snippet, inherited_start, inherited_end)
            return
        if not isinstance(value, dict):
            return

        title = next(
            (_plain_text(value.get(key), 180) for key in _TITLE_KEYS
             if _plain_text(value.get(key), 180)),
            inherited_title,
        )
        snippet = next(
            (_plain_text(value.get(key), 420) for key in _SNIPPET_KEYS
             if _plain_text(value.get(key), 420)),
            inherited_snippet,
        )
        reference_id = next(
            (_reference_id(value.get(key)) for key in _ID_KEYS
             if _reference_id(value.get(key))),
            "",
        )
        start_index = _citation_offset(value.get("start_index"))
        end_index = _citation_offset(value.get("end_index"))
        if start_index is None:
            start_index = inherited_start
        if end_index is None:
            end_index = inherited_end

        def add_candidate(raw_url: str) -> None:
            candidate: dict[str, Any] = {"url": raw_url, "title": title, "snippet": snippet}
            if reference_id:
                candidate["id"] = reference_id
            if start_index is not None and end_index is not None and start_index < end_index:
                candidate["start_index"] = start_index
                candidate["end_index"] = end_index
            candidates.append(candidate)

        for key in _URL_KEYS:
            raw_url = value.get(key)
            if isinstance(raw_url, str):
                add_candidate(raw_url)
            elif isinstance(raw_url, list):
                for item in raw_url[:16]:
                    if isinstance(item, str):
                        add_candidate(item)
                    elif isinstance(item, (dict, list)):
                        visit(item, depth + 1, title, snippet, start_index, end_index)

        for key in _URL_LIST_KEYS:
            raw_urls = value.get(key)
            if isinstance(raw_urls, list):
                for item in raw_urls[:16]:
                    if isinstance(item, str):
                        add_candidate(item)
                    elif isinstance(item, (dict, list)):
                        visit(item, depth + 1, title, snippet, start_index, end_index)

        for child in value.values():
            if isinstance(child, (dict, list)):
                visit(child, depth + 1, title, snippet, start_index, end_index)

    for key in _ROOT_KEYS:
        if key in metadata:
            visit(metadata[key])
    return normalize_tool_references(candidates)



def _utf16_to_codepoint_offset(text: str, offset: int) -> int | None:
    """Convert a browser/Responses UTF-16 offset without splitting a surrogate pair."""
    units = 0
    for index, character in enumerate(text):
        if units == offset:
            return index
        units += 2 if ord(character) > 0xFFFF else 1
        if units > offset:
            return None
    return len(text) if units == offset else None


def apply_inline_citation_markers(
    text: str, references: Any,
) -> str:
    """Insert the renderer's inert citation marker after validated annotation spans."""
    if not isinstance(text, str) or not text or len(text) > 1_000_000:
        return text if isinstance(text, str) else ""
    rows = normalize_tool_references(references)
    by_end: dict[int, list[str]] = {}
    for reference in rows:
        reference_id = reference.get("id")
        start_index = reference.get("start_index")
        end_index = reference.get("end_index")
        if not isinstance(reference_id, str):
            continue
        if type(start_index) is not int or type(end_index) is not int:
            continue
        start = _utf16_to_codepoint_offset(text, start_index)
        end = _utf16_to_codepoint_offset(text, end_index)
        if start is None or end is None or start >= end or end > len(text):
            continue
        ids = by_end.setdefault(end, [])
        if reference_id not in ids:
            ids.append(reference_id)
    result = text
    marker_open = "\uE200cite\uE202"
    marker_separator = "\uE202"
    marker_close = "\uE201"
    for end in sorted(by_end, reverse=True):
        marker = marker_open + marker_separator.join(by_end[end]) + marker_close
        result = result[:end] + marker + result[end:]
    return result
